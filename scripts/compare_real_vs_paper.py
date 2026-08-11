"""เทียบผลเทรดจริง (Binance Futures CSV) กับสัญญาณ/paper ของระบบ

Binance export เป็นราย "fill" — ต้องประกอบเป็นรอบเข้า-ออก (position) ก่อน แล้วจับคู่
กับ paper trade ในระบบ (symbol + เวลาใกล้กัน) เพื่อดู:
  • slippage/ดีเลย์ตอนเข้า (ราคาจริง vs ราคาสัญญาณ)
  • PnL จริง vs paper
  • ผลตรงกันไหม (ทั้งคู่กำไร/ขาดทุน)

    python -m scripts.compare_real_vs_paper "path/to/Binance-Futures-Trade-History.csv"

⚠️ sample เล็กบอกได้แค่ "execution ต่างแค่ไหน" ไม่ได้ตอบว่ามี edge — edge ยังต้องรอ
หลายร้อยไม้เหมือนเดิม
"""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

BKK = timezone(timedelta(hours=7))     # Binance export เป็น UTC+7


@dataclass
class RealTrade:
    symbol: str
    side: str                # LONG | SHORT
    opened_at: int           # ms epoch UTC
    closed_at: int
    entry_price: float
    exit_price: float
    qty: float
    realized_pnl: float      # ผลรวม Realized Profit (ไม่รวม fee)
    fees: float
    @property
    def net_pnl(self) -> float:
        return round(self.realized_pnl - self.fees, 6)


def _fee(s: str) -> float:
    return float(s.replace("USDT", "").strip() or 0)


def _ts(s: str) -> int:
    dt = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=BKK)
    return int(dt.timestamp() * 1000)


def parse_binance_csv(path: str) -> list[RealTrade]:
    """ประกอบ fill → ไม้ (รอบเข้า-ออก) แยกตาม symbol ด้วย net position ตัดผ่านศูนย์"""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not r.get("Symbol"):
                continue
            rows.append({
                "sym": r["Symbol"], "side": r["Side"].upper(), "ts": _ts(r["Time"]),
                "price": float(r["Price"]), "qty": float(r["Quantity"]),
                "fee": _fee(r["Fee"]), "rp": float(r["Realized Profit"] or 0),
            })
    trades: list[RealTrade] = []
    by_sym: dict[str, list] = {}
    for r in rows:
        by_sym.setdefault(r["sym"], []).append(r)

    for sym, fills in by_sym.items():
        fills.sort(key=lambda x: x["ts"])
        pos = 0.0
        opens, closes = [], []          # (price, qty)
        open_fee = close_fee = realized = 0.0
        entry_ts = None
        for fl in fills:
            signed = fl["qty"] if fl["side"] == "BUY" else -fl["qty"]
            same = (pos == 0) or ((pos > 0) == (signed > 0))
            if pos == 0:                # เปิดใหม่
                opens, closes = [(fl["price"], fl["qty"])], []
                open_fee, close_fee, realized = fl["fee"], 0.0, 0.0
                entry_ts = fl["ts"]; pos = signed
            elif same:                  # เติมทิศเดิม
                opens.append((fl["price"], fl["qty"])); open_fee += fl["fee"]; pos += signed
            else:                       # ปิด/ลด
                closes.append((fl["price"], fl["qty"])); close_fee += fl["fee"]
                realized += fl["rp"]; pos += signed
                if abs(pos) < 1e-9:     # ปิดครบ → บันทึกรอบ
                    eq = sum(q for _, q in opens)
                    xq = sum(q for _, q in closes)
                    entry = sum(p * q for p, q in opens) / eq if eq else 0
                    exit_ = sum(p * q for p, q in closes) / xq if xq else 0
                    side = "LONG" if opens and closes and entry <= exit_ or eq else "LONG"
                    # ทิศจาก fill เปิด: opens เป็น BUY → LONG, SELL → SHORT
                    side = "LONG" if _open_is_buy(fills, entry_ts) else "SHORT"
                    trades.append(RealTrade(sym, side, entry_ts, fl["ts"], round(entry, 8),
                                            round(exit_, 8), eq, round(realized, 6),
                                            round(open_fee + close_fee, 6)))
                    pos = 0.0
    trades.sort(key=lambda t: t.opened_at)
    return trades


def _open_is_buy(fills: list, entry_ts: int) -> bool:
    for fl in fills:
        if fl["ts"] == entry_ts:
            return fl["side"] == "BUY"
    return True


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%m-%d %H:%M")


def load_paper_trades(dsn: str) -> list[dict]:
    """paper trade จากระบบ (Supabase) — เอาไว้จับคู่กับไม้จริง"""
    import psycopg
    out = []
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("""SELECT symbol, side, filled_entry, exit_price, pnl_amount,
                              actual_rr, exit_reason, opened_at, status
                       FROM trades WHERE opened_at IS NOT NULL ORDER BY opened_at""")
        cols = ["symbol", "side", "filled_entry", "exit_price", "pnl_amount",
                "actual_rr", "exit_reason", "opened_at", "status"]
        for r in cur.fetchall():
            out.append(dict(zip(cols, r)))
    return out


def match(real: RealTrade, paper: list[dict], window_ms: int = 3_600_000):
    """หา paper trade ที่ symbol ตรง + เปิดใกล้เวลาที่สุด (ภายใน window)"""
    best, best_dt = None, window_ms + 1
    for p in paper:
        if p["symbol"] != real.symbol or p["opened_at"] is None:
            continue
        dt = abs(int(p["opened_at"]) - real.opened_at)
        if dt < best_dt:
            best, best_dt = p, dt
    return best if best_dt <= window_ms else None


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    if len(argv) < 2:
        print("ใส่ path ของ Binance CSV:  python -m scripts.compare_real_vs_paper <file.csv>")
        return
    trades = parse_binance_csv(argv[1])
    print(f"\nไม้จริงจาก Binance: {len(trades)} รอบ · รวม PnL สุทธิ "
          f"{sum(t.net_pnl for t in trades):+.4f} USDT\n")

    from worker.app.store import backend_name, database_url
    dsn = database_url()
    if not dsn:
        print("ไม่มี DATABASE_URL — แสดงเฉพาะไม้จริง (จับคู่ paper ไม่ได้)")
        for t in trades:
            print(f"  {t.symbol:<9}{t.side:<6}{_fmt(t.opened_at):<13} PnL {t.net_pnl:+.4f}")
        return

    paper = load_paper_trades(dsn)
    print(f"เทียบกับ paper {len(paper)} ไม้ (backend: {backend_name()})\n")
    print(f"  {'ไม้จริง':<20}{'จับคู่':<7}{'slippage':>10}{'จริง':>9}{'paper':>9}{'ตรงกัน':>8}")
    n_match = agree = 0
    for t in trades:
        p = match(t, paper)
        row = f"  {t.symbol+' '+t.side:<20}"
        if not p:
            print(row + f"{'ไม่พบ':<7}{'—':>10}{t.net_pnl:>9.3f}{'—':>9}{'—':>8}")
            continue
        n_match += 1
        # slippage ตอนเข้า: ราคาจริง vs ราคา paper (สัญญาณ) — เทียบทิศ
        pe = float(p["filled_entry"]) if p["filled_entry"] else t.entry_price
        slip = (t.entry_price - pe) / pe * 100 if pe else 0.0
        ppnl = float(p["pnl_amount"]) if p["pnl_amount"] is not None else 0.0
        same = (t.net_pnl >= 0) == (ppnl >= 0)
        agree += 1 if same else 0
        print(row + f"{'✓':<7}{slip:>+9.2f}%{t.net_pnl:>9.3f}{ppnl:>9.3f}{'✓' if same else '✗':>8}")

    print(f"\n  จับคู่ได้ {n_match}/{len(trades)} · ผลตรงกัน (กำไร/ขาดทุนทิศเดียว) {agree}/{n_match}")
    print("\n⚠️ sample เล็ก — บอกได้แค่ execution ต่างแค่ไหน ไม่ใช่มี edge หรือไม่")


if __name__ == "__main__":
    main(sys.argv)

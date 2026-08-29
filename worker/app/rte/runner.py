"""RTE runtime loop — forward paper-test บนตลาดจริง (สเปก §5, §10, §12).

REST polling (fstream ถูก geo-block ในไทย ตาม CLAUDE.md) → ทุกแท่ง 4h ที่ปิด → ถ้า
ถึงรอบ rebalance (ทุก 6 แท่ง, anchor 00:00 UTC) → ensemble.decide → paper rebalance →
persist Supabase → แจ้งเตือนไทย

⚠️ paper เท่านั้น — ไม่มี code path ใดส่งคำสั่งจริง · fill ใช้ close ของแท่งที่เพิ่ง
ปิด (≈ next-bar open เพราะตลาดต่อเนื่อง) — จุดต่างจาก backtest ที่ใช้ open[t+1] เป๊ะ

core `process_bar()` ไม่แตะเน็ต (รับ window ที่เตรียมไว้) → ทดสอบ offline ได้
network อยู่ใน `_fetch_*` ล้วน
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from worker.app.models import Candle
from .config import RTEConfig
from . import ensemble as ens
from .portfolio import PaperPortfolio

FOUR_H_MS = 14_400_000
POLL_SECONDS = 300
WARMUP_BARS = 400          # > min_score_bars มากพอ (สเปก §5.4 แนะ >=250)
WINDOW_CAP = 600           # เก็บ window ต่อเหรียญไม่ให้โตไม่จำกัด


def is_rebalance_bar(open_time_ms: int, cfg: RTEConfig) -> bool:
    """rebalance เมื่อแท่งเปิดตรง anchor (00:00 UTC) แล้วทุก 6 แท่ง (~24h) — สเปก §10.
    epoch 0 = 00:00 UTC และ 4h หาร 24h ลงตัว → (open//4h)%6==0 คือเปิด 00:00 UTC ทุกวัน"""
    anchor = (cfg.rebalance_anchor_utc_hour * 3_600_000 // FOUR_H_MS) % cfg.rebalance_every_bars
    return (open_time_ms // FOUR_H_MS) % cfg.rebalance_every_bars == anchor


def classify(prev_w: dict, dec, cfg: RTEConfig) -> list[tuple]:
    """เทียบน้ำหนักเก่า→ใหม่ → (symbol, action, old_w, new_w) สำหรับแจ้งเตือน (สเปก §11)"""
    new_w = {} if dec.to_cash else dec.target_weights
    out = []
    for s in sorted(set(prev_w) | set(new_w)):
        pw, nw = prev_w.get(s, 0.0), new_w.get(s, 0.0)
        if abs(nw - pw) < cfg.min_weight_change:
            continue
        if pw == 0 and nw > 0:
            act = "ENTER"
        elif nw == 0 and pw > 0:
            act = "EXIT"
        elif nw > pw:
            act = "INCREASE"
        else:
            act = "DECREASE"
        out.append((s, act, pw, nw))
    return out


def format_rebalance(dec, snap: dict, events: list, cfg: RTEConfig) -> str:
    from datetime import datetime, timezone
    t = datetime.fromtimestamp(dec.bar_close_time / 1000, timezone.utc)
    head = f"🔁 RTE rebalance · แท่ง {t:%Y-%m-%d %H:%M} UTC"
    if dec.to_cash:
        state = f"💵 ถือเงินสด — {dec.reason}"
    else:
        picks = " ".join(f"{s[:-4]} {w*100:.0f}%" for s, w in
                         sorted(dec.target_weights.items(), key=lambda kv: -kv[1]))
        state = (f"📊 breadth {dec.breadth*100:.0f}% · gross {dec.gross_exposure*100:.0f}%\n"
                 f"เลือก: {picks}")
    regime = (f"BTC {'✅เหนือ' if dec.btc_trend_ok else '❌ต่ำกว่า'} EMA100 · "
              f"crash filter {'ผ่าน' if dec.crash_filter_ok else '❌ร่วงแรง'}")
    acts = "\n".join(
        f"  {'🟢' if a in ('ENTER','INCREASE') else '🔻'} {a} {s[:-4]} "
        f"{ow*100:.0f}%→{nw*100:.0f}%" for s, a, ow, nw in events) or "  (ไม่มีการปรับ / HOLD)"
    halt = "\n🛑 RISK_HALT — DD แตะเพดาน หยุดเทรด (ต้อง resume มือ)" if snap["halted"] else ""
    return (f"{head}\n{regime}\n{state}\n{acts}\n"
            f"— equity ${snap['equity']:,.0f} · DD {snap['drawdown']*100:.1f}% · "
            f"cash {snap['cash']/snap['equity']*100:.0f}% "
            f"(fee ${snap['cumulative_fee']:.1f} · funding ${snap['cumulative_funding']:.1f}){halt}\n"
            f"📄 paper · {cfg.strategy_version}")


class RTEEngine:
    def __init__(self, cfg: RTEConfig, store, notifier):
        self.cfg = cfg
        self.store = store
        self.notifier = notifier
        self.config_hash = cfg.config_hash()
        self.window: dict[str, list[Candle]] = {s: [] for s in cfg.symbols}
        self.portfolio: Optional[PaperPortfolio] = None
        self.last_weights: dict[str, float] = {}
        self.last_processed: int = 0

    # ---------- core (offline-testable) ----------
    def snapshot_window(self, bar_open_time: int) -> dict[str, list[Candle]]:
        return {s: [c for c in cs if c.open_time <= bar_open_time]
                for s, cs in self.window.items()}

    def _marks(self, snap: dict[str, list[Candle]], bar_open_time: int) -> dict[str, float]:
        out = {}
        for s, cs in snap.items():
            if cs and cs[-1].open_time == bar_open_time:
                out[s] = cs[-1].close
        return out

    def _position_rows(self, marks: dict[str, float], equity: float) -> list[dict]:
        """A3: snapshot P&L รายเหรียญหลัง rebalance (unrealized = qty×(mark−avg_entry))"""
        rows = []
        for sym, p in self.portfolio.positions.items():
            m = marks.get(sym, p.avg_entry)
            notional = p.qty * m
            rows.append({
                "symbol": sym, "qty": p.qty, "avg_entry": p.avg_entry, "mark_price": m,
                "notional": notional, "unrealized_pnl": p.qty * (m - p.avg_entry),
                "weight": (notional / equity) if equity else 0.0,
            })
        return rows

    async def process_bar(self, bar_open_time: int, funding: Optional[dict] = None):
        """ประมวลผลแท่งปิด 1 แท่ง — ถ้าเป็นรอบ rebalance เท่านั้นจึงตัดสินใจ.
        funding: dict symbol→อัตรารวมตั้งแต่ rebalance ก่อน (None = ดึงเอง/0)
        คืน (decision, fills, events) หรือ None ถ้าไม่ใช่รอบ rebalance"""
        if not is_rebalance_bar(bar_open_time, self.cfg):
            return None
        snap = self.snapshot_window(bar_open_time)
        marks = self._marks(snap, bar_open_time)
        if len(marks) < len(self.cfg.symbols):
            print(f"[rte] ข้าม rebalance {bar_open_time}: ราคาไม่ครบ 8 เหรียญ ({len(marks)})")
            return None

        dec = ens.decide(snap, self.cfg)
        assert self.portfolio is not None
        # funding ของช่วงที่ผ่านมา (คิดก่อน rebalance เพราะคิดบนสถานะที่ถืออยู่)
        if self.cfg.include_funding and self.portfolio.positions:
            fr = funding if funding is not None else await self._fetch_funding_since(
                self.portfolio.last_rebalance_time or bar_open_time, bar_open_time,
                list(self.portfolio.positions))
            if fr:
                self.portfolio.apply_funding(fr, marks)

        prev_w = dict(self.last_weights)
        fills = self.portfolio.rebalance(dec.target_weights, marks, bar_open_time)
        self.last_weights = {} if dec.to_cash else dict(dec.target_weights)
        events = classify(prev_w, dec, self.cfg)

        snapd = self.portfolio.snapshot(marks)
        prows = self._position_rows(marks, snapd["equity"])
        try:
            self.store.record_rebalance(self.config_hash, dec, snapd["equity"],
                                        snapd["drawdown"], self.portfolio.halted)
            self.store.save_state(self.config_hash, self.portfolio, self.last_weights)
            self.store.record_positions(self.config_hash, dec.bar_close_time, prows)  # A3
        except Exception as e:
            print(f"[rte] persist ล้มเหลว (ไม่หยุด loop): {e!r}")

        if events or dec.to_cash or self.portfolio.halted:
            try:
                await self.notifier.send(format_rebalance(dec, snapd, events, self.cfg),
                                         priority="high" if self.portfolio.halted else "normal")
            except Exception as e:
                print(f"[rte] แจ้งเตือนล้มเหลว: {e!r}")
        print(f"[rte] rebalance {bar_open_time} · {dec.reason} · equity ${snapd['equity']:,.0f}")
        return dec, fills, events

    # ---------- network ----------
    async def _fetch_recent(self, symbol: str, n: int) -> list[Candle]:
        from backtest.fetch_binance import PERP_BASE, fetch
        rows = await asyncio.to_thread(fetch, symbol, "4h", n, PERP_BASE)
        return _rows_to_candles(symbol, rows)

    async def _fetch_funding_since(self, since_ms: int, until_ms: int,
                                   symbols: list[str]) -> dict[str, float]:
        """รวม funding rate ที่ settle ในช่วง (since, until] ต่อเหรียญ (best-effort)"""
        from backtest.fetch_funding import fetch
        out: dict[str, float] = {}
        for sym in symbols:
            try:
                rows = await asyncio.to_thread(fetch, sym, 30)
            except Exception:
                continue
            total = 0.0
            for r in rows:
                ft = int(r.get("fundingTime", 0))
                if since_ms < ft <= until_ms:
                    total += float(r.get("fundingRate", 0.0) or 0.0)
            out[sym] = total
        return out

    def _merge(self, symbol: str, candles: list[Candle]) -> None:
        by_ts = {c.open_time: c for c in self.window[symbol]}
        for c in candles:
            by_ts[c.open_time] = c
        merged = sorted(by_ts.values(), key=lambda c: c.open_time)
        self.window[symbol] = merged[-WINDOW_CAP:]

    def _common_closed(self) -> list[int]:
        """open_time ที่ปิดแล้วและมีครบทุกเหรียญ (เรียงเวลา)"""
        if any(not self.window[s] for s in self.cfg.symbols):
            return []
        sets = [set(c.open_time for c in self.window[s]) for s in self.cfg.symbols]
        common = set.intersection(*sets)
        return sorted(common)

    # ---------- lifecycle ----------
    async def warmup(self):
        for sym in self.cfg.symbols:
            try:
                self._merge(sym, await self._fetch_recent(sym, WARMUP_BARS))
            except Exception as e:
                print(f"[rte] warmup {sym} ล้มเหลว: {e!r}")
        common = self._common_closed()
        last_bar = common[-1] if common else int(time.time() * 1000)

        st = None
        try:
            st = self.store.load_state(self.config_hash)
        except Exception as e:
            print(f"[rte] โหลด state ไม่ได้: {e!r}")
        if st and st.get("config_hash") == self.config_hash:
            self.portfolio = PaperPortfolio.from_dict(self.cfg, st["state"])
            self.last_weights = st.get("weights", {})
            print(f"[rte] กู้ portfolio จาก store · equity(cost) ~ ${self.portfolio.cash:,.0f} cash")
        else:
            if st:
                print(f"[rte] config_hash เปลี่ยน ({st.get('config_hash')}→{self.config_hash}) "
                      "— เริ่ม portfolio ใหม่ (สเปก §27: เปลี่ยนกฎ = version/สถิติใหม่)")
            self.portfolio = PaperPortfolio.new(self.cfg, ts=last_bar)
        # ประมวลผลย้อนหลังเฉพาะแท่งที่ยังไม่เคยเห็น: เริ่มจากแท่งล่าสุด (ไม่ backfill fill
        # ย้อนหลังเสมือนจริง — สเปก §10/§24) นับจากนี้ไป
        self.last_processed = self.portfolio.last_rebalance_time or last_bar

    async def poll_once(self):
        now = int(time.time() * 1000)
        for sym in self.cfg.symbols:
            try:
                self._merge(sym, await self._fetch_recent(sym, 5))
            except Exception as e:
                print(f"[rte] poll {sym} ล้มเหลว: {e!r}")
        common = [t for t in self._common_closed() if t + FOUR_H_MS <= now]  # ปิดจริงแล้ว
        new_bars = [t for t in common if t > self.last_processed]
        for t in new_bars:
            try:
                await self.process_bar(t)
            except Exception as e:
                print(f"[rte] process_bar {t} ล้มเหลว: {e!r}")
            self.last_processed = t

    async def run_loop(self):
        await self.warmup()
        print(f"[rte] เริ่ม forward paper-test · {self.cfg.strategy_version} · "
              f"config_hash {self.config_hash} · เหรียญ {', '.join(self.cfg.symbols)}")
        try:
            await self.notifier.send(
                f"🟢 RTE forward paper-test เริ่มทำงาน\n"
                f"{len(self.cfg.symbols)} เหรียญ @ 4h · rebalance ทุก ~24h (00:00 UTC)\n"
                f"ทุนเริ่ม ${self.cfg.starting_equity:,.0f} · โหมด paper (ไม่ส่งคำสั่งจริง)\n"
                f"⚠️ ยังไม่ใช่ edge พิสูจน์แล้ว — เก็บหลักฐาน live · {self.config_hash}",
                priority="normal")
        except Exception:
            pass
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[rte] poll รอบนี้ล้มเหลว: {e!r}")
            await asyncio.sleep(POLL_SECONDS)


def _rows_to_candles(symbol: str, rows: list) -> list[Candle]:
    now = int(time.time() * 1000)
    out = []
    for k in rows:
        ot = int(k[0])
        if ot + FOUR_H_MS > now:
            continue    # แท่งยังไม่ปิด — ไม่เอา (สเปก §3.2)
        out.append(Candle(exchange="binance", symbol=symbol, timeframe="4h",
                          open_time=ot, open=float(k[1]), high=float(k[2]),
                          low=float(k[3]), close=float(k[4]), volume=float(k[5]),
                          is_closed=True))
    return out

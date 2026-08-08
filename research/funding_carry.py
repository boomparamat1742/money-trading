"""Funding-rate carry — a market-neutral crypto edge (long spot + short perp).

The position is delta-neutral, so price direction cancels; the return is the
funding the short-perp leg RECEIVES when funding is positive. This is a CARRY,
not a directional bet — its benchmark is cash (0), and any positive Sharpe after
costs is real edge, independent of BTC beta.

We aggregate funding to a DAILY total per symbol (handles 8h vs 4h uniformly),
then test:
  A) BTC always-on carry
  B) Diversified always-on (equal weight across the universe)
  C) Conditional cross-sectional: hold only symbols whose trailing funding is
     above a threshold (skip negative-funding bear regimes), with flip costs.

Costs: entering/exiting the neutral position costs both legs; we charge a
one-way 0.15% (spot taker ~0.10% + perp taker ~0.05%) whenever a name enters or
leaves the held set.

Usage:
    python -m research.funding_carry        # fetches funding for the universe if missing
"""
from __future__ import annotations

import math
import os
import sys
import time

from backtest.fetch_funding import fetch, write_csv

UNIVERSE = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "LTC",
            "LINK", "DOT", "AVAX", "TRX", "ATOM", "BCH", "ETC", "XLM"]
DAY_MS = 86_400_000
ONE_WAY = 0.0015          # 0.15% per name entering/leaving (spot+perp taker)
ANNUAL = 365


def ensure_funding(sym: str, records: int = 3000) -> dict[int, float] | None:
    path = f"data/{sym}USDT_funding.csv"
    if not os.path.exists(path):
        try:
            print(f"  fetching {sym}USDT funding ...", flush=True)
            rows = fetch(f"{sym}USDT", records)
            if not rows:
                return None
            write_csv(rows, path)
        except SystemExit:
            print(f"  skip {sym} (no funding / fetch failed)")
            return None
    daily: dict[int, float] = {}
    import csv
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                t = int(row["funding_time"]); rate = float(row["funding_rate"])
            except (ValueError, KeyError):
                continue
            day = (t // DAY_MS) * DAY_MS
            daily[day] = daily.get(day, 0.0) + rate
    return daily


def _stats(rets: list[float], times: list[int]):
    if not rets:
        return dict(ann_return=0, ann_vol=0, sharpe=0, max_dd=0, pos_days=0, total=0)
    eq = 1.0; peak = 1.0; max_dd = 0.0
    for r in rets:
        eq *= (1 + r); peak = max(peak, eq); max_dd = max(max_dd, (peak - eq) / peak)
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    sd = math.sqrt(var)
    return dict(
        ann_return=mean * ANNUAL, ann_vol=sd * math.sqrt(ANNUAL),
        sharpe=(mean / sd * math.sqrt(ANNUAL)) if sd > 0 else 0.0,
        max_dd=max_dd, pos_days=sum(1 for r in rets if r > 0) / len(rets),
        total=eq - 1,
    )


def always_on(funding: dict[str, dict[int, float]], dates: list[int], syms: list[str]):
    t_out, r_out = [], []
    for d in dates:
        vals = [funding[s][d] for s in syms if d in funding[s]]
        if vals:
            t_out.append(d); r_out.append(sum(vals) / len(vals))
    return t_out, r_out


def _trailing_mean(m: dict[int, float], dates: list[int], i: int, L: int):
    vals = [m[dates[j]] for j in range(max(0, i - L), i) if dates[j] in m]
    return (sum(vals) / len(vals)) if vals else None


def conditional(funding, dates, syms, L, threshold, one_way=ONE_WAY):
    t_out, r_out, held_counts = [], [], []
    prev: set[str] = set()
    for i in range(len(dates)):
        d = dates[i]
        elig = set()
        for s in syms:
            tm = _trailing_mean(funding[s], dates, i, L)
            if tm is not None and tm > threshold and d in funding[s]:
                elig.add(s)
        turnover = 0.0
        if elig != prev:
            n_new = len(elig) or 1
            n_old = len(prev) or 1
            w_new = {s: 1 / n_new for s in elig}
            w_old = {s: 1 / n_old for s in prev}
            turnover = sum(abs(w_new.get(s, 0) - w_old.get(s, 0)) for s in elig | prev)
            prev = elig
        if elig:
            ret = sum(funding[s][d] for s in elig) / len(elig) - one_way * turnover
            t_out.append(d); r_out.append(ret); held_counts.append(len(elig))
    return t_out, r_out, held_counts


def _slice(t, r, lo=None, hi=None):
    st, sr = [], []
    for i in range(len(t)):
        if (lo is None or t[i] >= lo) and (hi is None or t[i] < hi):
            st.append(t[i]); sr.append(r[i])
    return st, sr


def _fmt(s):
    return (f"{s['ann_return']*100:>7.1f}% {s['sharpe']:>7.2f} {s['ann_vol']*100:>7.1f}% "
            f"{s['max_dd']*100:>6.1f}% {s['pos_days']*100:>5.0f}%")


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    os.makedirs("data", exist_ok=True)

    funding, loaded = {}, []
    for sym in UNIVERSE:
        m = ensure_funding(sym)
        if m:
            funding[sym] = m; loaded.append(sym)
    if "BTC" not in funding:
        print("ต้องมี BTC funding เป็นอย่างน้อย"); return
    dates = sorted(set().union(*[set(m) for m in funding.values()]))
    d0 = time.strftime("%Y-%m-%d", time.gmtime(dates[0] / 1000))
    d1 = time.strftime("%Y-%m-%d", time.gmtime(dates[-1] / 1000))
    print(f"\nUniverse ({len(loaded)}): {', '.join(loaded)}")
    print(f"Daily funding days: {len(dates)}  ({d0} → {d1})\n")

    hdr = f"{'':<26}{'AnnRet':>8}{'Sharpe':>8}{'AnnVol':>8}{'MaxDD':>7}{'Pos%':>6}"
    print(hdr)
    bt, br = always_on(funding, dates, ["BTC"])
    print(f"{'A) BTC always-on':<26}{_fmt(_stats(br, bt))}")
    dt, dr = always_on(funding, dates, loaded)
    print(f"{'B) Diversified always-on':<26}{_fmt(_stats(dr, dt))}")
    ct, cr, _ = conditional(funding, dates, loaded, L=14, threshold=0.0)
    print(f"{'C) Conditional (14d, >0)':<26}{_fmt(_stats(cr, ct))}")

    print("\n(market-neutral → benchmark คือ cash = 0; Sharpe เป็นบวกหลัง cost = edge)")

    # ---- walk-forward on the conditional strategy ----
    grid = [(L, thr) for L in (7, 14, 30) for thr in (0.0, 0.0001, 0.0003)]
    split_time = dates[int(len(dates) * 0.6)]
    best, best_sharpe = None, -1e9
    for L, thr in grid:
        t, r, _ = conditional(funding, dates, loaded, L, thr)
        tr_t, tr_r = _slice(t, r, hi=split_time)
        s = _stats(tr_r, tr_t)
        if s["sharpe"] > best_sharpe:
            best_sharpe, best = s["sharpe"], (L, thr)
    L, thr = best
    t, r, _ = conditional(funding, dates, loaded, L, thr)
    te_t, te_r = _slice(t, r, lo=split_time)
    oos = _stats(te_r, te_t)
    # diversified always-on OOS for comparison
    dt_te, dr_te = _slice(dt, dr, lo=split_time)
    div_oos = _stats(dr_te, dt_te)

    print(f"\nWalk-forward: เลือก L={L}d, threshold={thr} จาก train (Sharpe {best_sharpe:.2f})")
    print("\n── ผล OUT-OF-SAMPLE (test) ──")
    print(f"{'':<20}{'Conditional':>13}{'Diversified':>13}")
    print(f"{'Ann return':<20}{oos['ann_return']*100:>12.1f}%{div_oos['ann_return']*100:>12.1f}%")
    print(f"{'Sharpe':<20}{oos['sharpe']:>13.2f}{div_oos['sharpe']:>13.2f}")
    print(f"{'Ann vol':<20}{oos['ann_vol']*100:>12.1f}%{div_oos['ann_vol']*100:>12.1f}%")
    print(f"{'Max drawdown':<20}{oos['max_dd']*100:>12.1f}%{div_oos['max_dd']*100:>12.1f}%")
    print(f"{'Positive days':<20}{oos['pos_days']*100:>12.0f}%{div_oos['pos_days']*100:>12.0f}%")

    print()
    if oos["sharpe"] > 1.0 and oos["ann_return"] > 0:
        print("🟢 มี edge จริง — carry ให้ Sharpe > 1 หลัง cost แบบ market-neutral "
              "(ต่างจากทุกอันก่อนหน้า) ควรตรวจต่อ: cost/slippage จริง, การ execute 2 ขา, funding ขาลบช่วงหมี")
    elif oos["sharpe"] > 0.5 and oos["ann_return"] > 0:
        print("🟡 มีแนวโน้ม — carry เป็นบวกหลัง cost แต่ Sharpe ปานกลาง คุ้มศึกษาต่อ (execution สำคัญมาก)")
    elif oos["ann_return"] > 0:
        print("🟡 บวกอ่อนๆ — carry เป็นบวกแต่ Sharpe ต่ำ กำไรอาจถูก cost/execution กินหมดจริง")
    else:
        print("🔴 ไม่คุ้ม — carry ติดลบหลัง cost ในช่วงนี้")
    print("หมายเหตุ: ยังไม่รวม borrow/slippage จริง, การถอน-ฝากข้ามตลาด spot↔futures, "
          "และ funding เปลี่ยนเร็ว — ผลจริงต้องทดสอบ live เล็กๆ ก่อน")


if __name__ == "__main__":
    main(sys.argv)

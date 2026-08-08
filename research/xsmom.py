"""Cross-Sectional Momentum (XSMOM) on daily crypto — the research-backed edge.

At each weekly rebalance: rank the coin universe by trailing L-day return, go
LONG the top-K equal-weight (optionally SHORT the bottom-K), hold for the week.
This bets that recent winners keep winning RELATIVE to other coins — a factor
distinct from BTC beta (Liu-Tsyvinski-Wu document a crypto momentum factor).

Benchmarks it must beat to matter:
  • Hold BTC              (the usual default)
  • Equal-weight market   (hold the whole universe — isolates the momentum tilt)

Universe membership is dynamic: a coin is eligible on a date only if it has both
a current price and a price L days earlier (so newly-listed coins join over time
and there is no survivorship look-ahead beyond the fixed symbol list).

Usage:
    python -m research.xsmom            # fetches ~16 coins (daily) if missing, then runs
Fees 0.05% per unit turnover at rebalance. Long-only is spot-realistic; the
long-short row is reference only (needs perps to short).
"""
from __future__ import annotations

import os
import sys

from backtest.fetch_binance import fetch, write_csv
from backtest.synthetic import load_csv
from research.momentum import ANNUALIZE, FEE, _stats  # reuse return-series metrics

UNIVERSE = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "LTC",
            "LINK", "DOT", "AVAX", "TRX", "ATOM", "BCH", "ETC", "XLM"]
DAY_MS = 86_400_000


def ensure_data(sym: str, bars: int = 2500) -> dict[int, float] | None:
    path = f"data/{sym}USDT_1d.csv"
    if not os.path.exists(path):
        try:
            print(f"  fetching {sym}USDT 1d ...", flush=True)
            rows = fetch(f"{sym}USDT", "1d", bars)
            if not rows:
                return None
            write_csv(rows, path)
        except SystemExit:
            print(f"  skip {sym} (fetch failed)")
            return None
    candles = load_csv(path, symbol=f"{sym}USDT", timeframe="1d")
    return {c.open_time: c.close for c in candles}


def _weights(prices, dates, i, L, top_k, long_short):
    if i - L < 0:
        return {}
    d, d0 = dates[i], dates[i - L]
    elig = []
    for sym, series in prices.items():
        c, c0 = series.get(d), series.get(d0)
        if c is not None and c0 and c0 > 0:
            elig.append((sym, c / c0 - 1))
    if len(elig) < top_k:
        return {}
    elig.sort(key=lambda x: -x[1])
    w = {sym: 1.0 / top_k for sym, _ in elig[:top_k]}
    if long_short and len(elig) >= 2 * top_k:
        for sym, _ in elig[-top_k:]:
            w[sym] = w.get(sym, 0.0) - 1.0 / top_k
    return w


def _turnover(old, new):
    return sum(abs(new.get(s, 0.0) - old.get(s, 0.0)) for s in set(old) | set(new))


def run_xsmom(prices, dates, L, top_k, fee=FEE, long_short=False, rebalance=7):
    holdings: dict[str, float] = {}
    pending = 0.0
    t_out, r_out, hold = [], [], []
    for i in range(1, len(dates)):
        d, dp = dates[i], dates[i - 1]
        r = 0.0
        for sym, wt in holdings.items():
            c, cp = prices[sym].get(d), prices[sym].get(dp)
            if c is not None and cp is not None and cp > 0:
                r += wt * (c / cp - 1)
        r -= pending
        pending = 0.0
        t_out.append(d); r_out.append(r); hold.append(1 if holdings else 0)
        if i % rebalance == 0:
            new = _weights(prices, dates, i, L, top_k, long_short)
            pending = fee * _turnover(holdings, new)
            holdings = new
    return t_out, r_out, hold


def benchmarks(prices, dates):
    btc = prices.get("BTC", {})
    btc_t, btc_r, mkt_t, mkt_r = [], [], [], []
    for i in range(1, len(dates)):
        d, dp = dates[i], dates[i - 1]
        c, cp = btc.get(d), btc.get(dp)
        if c is not None and cp:
            btc_t.append(d); btc_r.append(c / cp - 1)
        rs = [s.get(d) / s.get(dp) - 1 for s in prices.values()
              if s.get(d) is not None and s.get(dp) not in (None, 0)]
        if rs:
            mkt_t.append(d); mkt_r.append(sum(rs) / len(rs))
    return (btc_t, btc_r), (mkt_t, mkt_r)


def _slice(t, r, h, lo=None, hi=None):
    st, sr, sh = [], [], []
    for i in range(len(t)):
        if (lo is None or t[i] >= lo) and (hi is None or t[i] < hi):
            st.append(t[i]); sr.append(r[i]); sh.append(h[i] if h else 1)
    return st, sr, sh


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    os.makedirs("data", exist_ok=True)

    prices, loaded = {}, []
    for sym in UNIVERSE:
        s = ensure_data(sym)
        if s:
            prices[sym] = s
            loaded.append(sym)
    if "BTC" not in prices:
        print("ต้องมี BTC เป็นอย่างน้อย"); return
    import time as _t
    dates = sorted(set().union(*[set(s) for s in prices.values()]))
    d0 = _t.strftime("%Y-%m-%d", _t.gmtime(dates[0] / 1000))
    d1 = _t.strftime("%Y-%m-%d", _t.gmtime(dates[-1] / 1000))
    print(f"\nUniverse ({len(loaded)}): {', '.join(loaded)}")
    print(f"Daily dates: {len(dates)}  ({d0} → {d1})\n")

    (btc_t, btc_r), (mkt_t, mkt_r) = benchmarks(prices, dates)
    btc_stats = _stats(btc_r, btc_t, [1] * len(btc_r))
    mkt_stats = _stats(mkt_r, mkt_t, [1] * len(mkt_r))

    grid = [(L, k) for L in (30, 60, 90) for k in (3, 5)]

    print("Full-sample scan (long-only top-K, in-sample):")
    print(f"{'L':>4} {'K':>3} | {'CAGR':>8} {'Sharpe':>7} {'MaxDD':>7}")
    for L, k in grid:
        t, r, h = run_xsmom(prices, dates, L, k)
        s = _stats(r, t, h)
        print(f"{L:>4} {k:>3} | {s.cagr*100:>7.1f}% {s.sharpe:>7.2f} {s.max_drawdown*100:>6.1f}%")
    print(f"{'BTC':>8} | {btc_stats.cagr*100:>7.1f}% {btc_stats.sharpe:>7.2f} {btc_stats.max_drawdown*100:>6.1f}%")
    print(f"{'Market':>8} | {mkt_stats.cagr*100:>7.1f}% {mkt_stats.sharpe:>7.2f} {mkt_stats.max_drawdown*100:>6.1f}%")

    # reference long-short (needs perps to actually short)
    t, r, h = run_xsmom(prices, dates, 30, 3, long_short=True)
    ls = _stats(r, t, h)
    print(f"{'L/S 30/3':>8} | {ls.cagr*100:>7.1f}% {ls.sharpe:>7.2f} {ls.max_drawdown*100:>6.1f}%  (reference, needs perps)")

    # ---- walk-forward ----
    split_time = dates[int(len(dates) * 0.6)]
    best, best_sharpe = None, -1e9
    for L, k in grid:
        t, r, h = run_xsmom(prices, dates, L, k)
        tr_t, tr_r, tr_h = _slice(t, r, h, hi=split_time)
        s = _stats(tr_r, tr_t, tr_h)
        if s.sharpe > best_sharpe:
            best_sharpe, best = s.sharpe, (L, k)
    L, k = best
    t, r, h = run_xsmom(prices, dates, L, k)
    te = _slice(t, r, h, lo=split_time)
    strat = _stats(te[1], te[0], te[2])
    btc_te = _slice(btc_t, btc_r, None, lo=split_time)
    mkt_te = _slice(mkt_t, mkt_r, None, lo=split_time)
    btc_o = _stats(btc_te[1], btc_te[0], [1] * len(btc_te[1]))
    mkt_o = _stats(mkt_te[1], mkt_te[0], [1] * len(mkt_te[1]))

    print(f"\nWalk-forward: เลือก L={L}, K={k} จาก train (Sharpe {best_sharpe:.2f})")
    print("\n── ผล OUT-OF-SAMPLE (test) ──")
    print(f"{'':<14}{'XSMOM':>10}{'BTC':>10}{'Market':>10}")
    print(f"{'CAGR':<14}{strat.cagr*100:>9.1f}%{btc_o.cagr*100:>9.1f}%{mkt_o.cagr*100:>9.1f}%")
    print(f"{'Sharpe':<14}{strat.sharpe:>10.2f}{btc_o.sharpe:>10.2f}{mkt_o.sharpe:>10.2f}")
    print(f"{'Max drawdown':<14}{strat.max_drawdown*100:>9.1f}%{btc_o.max_drawdown*100:>9.1f}%{mkt_o.max_drawdown*100:>9.1f}%")
    print(f"{'Total return':<14}{strat.total_return*100:>9.1f}%{btc_o.total_return*100:>9.1f}%{mkt_o.total_return*100:>9.1f}%")

    beats_market = strat.sharpe > mkt_o.sharpe
    beats_btc = strat.sharpe > btc_o.sharpe
    print()
    if beats_market and beats_btc:
        print("🟢 น่าสนใจ — XSMOM ให้ Sharpe สูงกว่าทั้ง BTC และ market (มีสัญญาณ momentum factor จริง) "
              "ควรตรวจต่อ: universe ใหญ่ขึ้น, cost จริง, robustness, long-short บน perps")
    elif beats_market:
        print("🟡 ก้ำกึ่ง — ชนะ market (มี momentum tilt) แต่ไม่ชนะ BTC ในช่วงนี้")
    else:
        print("🔴 ยังไม่เห็น edge — ไม่ชนะ market เชิง risk-adjusted")
    print("หมายเหตุ: long-only spot · rebalance รายสัปดาห์ · ผลอดีตไม่การันตีอนาคต")


if __name__ == "__main__":
    main(sys.argv)

"""RTE ensemble computation — สูตรทั้งหมดของ robust_trend_ensemble_v1_frozen.

รับแท่ง 4h (ปิดแล้ว, เรียงเวลา) ต่อเหรียญ → คืน RebalanceDecision ของ "แท่งล่าสุด"
สูตรทุกตัวตรงกับสเปก §7/§8/§9 และตรงกับ hypothesis ที่ผ่าน walk-forward
(research/lab/hypotheses.py::RobustTrendEnsemble) — ผูกด้วย test parity

pure Python ไม่มี numpy (เข้ากับ style ของ indicators.py) ตัดสินจากแท่งที่ปิดแล้ว
เท่านั้น ห้าม look-ahead — ผู้เรียกต้องส่งเฉพาะแท่งที่ is_closed
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import RTEConfig


def seeded_ema(closes: list[float], n: int) -> list[float | None]:
    """EMA seed แท่งแรกด้วยค่าเฉลี่ย n แท่งแรก (สเปก §7.2)"""
    out: list[float | None] = [None] * len(closes)
    if len(closes) < n:
        return out
    out[n - 1] = sum(closes[:n]) / n
    a = 2 / (n + 1)
    for i in range(n, len(closes)):
        out[i] = a * closes[i] + (1 - a) * out[i - 1]  # type: ignore[operator]
    return out


def pstdev(xs: list[float]) -> float:
    """population standard deviation (สเปก §7.10 ใช้ population ไม่ใช่ sample)"""
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


@dataclass
class SymbolScore:
    symbol: str
    close: float
    ema: dict[int, float]           # horizon → EMA value
    momentum: dict[int, float]      # horizon → momentum return
    trend_votes: int
    momentum_votes: int
    ensemble_score: int
    vol_4h: float
    annual_vol: float
    eligible: bool
    rank: int | None = None
    target_weight: float = 0.0


@dataclass
class RebalanceDecision:
    bar_close_time: int
    btc_close: float
    btc_ema100: float
    btc_return_21: float
    btc_trend_ok: bool
    crash_filter_ok: bool
    breadth: float
    gross_exposure: float
    to_cash: bool
    scores: dict[str, SymbolScore] = field(default_factory=dict)
    selected: list[str] = field(default_factory=list)
    target_weights: dict[str, float] = field(default_factory=dict)  # symbol → weight (0..1)
    reason: str = ""


def _returns(closes: list[float]) -> list[float]:
    r = [0.0] * len(closes)
    for i in range(1, len(closes)):
        r[i] = closes[i] / closes[i - 1] - 1 if closes[i - 1] else 0.0
    return r


def decide(candles_by_symbol: dict[str, list], cfg: RTEConfig) -> RebalanceDecision:
    """คำนวณ target weights ของ "แท่งปิดล่าสุด" จากแท่ง 4h ที่ส่งเข้ามา.

    candles_by_symbol: symbol → list[Candle] (ปิดแล้ว, เรียงเวลาเก่า→ใหม่)
    ต้องมีครบทุก symbol ใน cfg.symbols และ timestamp แท่งล่าสุดตรงกันทุกเหรียญ
    (สเปก §6 data-integrity) — ไม่งั้นคืน to_cash พร้อม reason
    """
    eps = 1e-8
    btc = cfg.symbols[0]  # BTCUSDT

    closes: dict[str, list[float]] = {}
    last_ts: dict[str, int] = {}
    for sym in cfg.symbols:
        cs = candles_by_symbol.get(sym) or []
        if not cs:
            return _cash(0, f"ไม่มีข้อมูล {sym}")
        closes[sym] = [c.close for c in cs]
        last_ts[sym] = cs[-1].open_time

    # แท่งล่าสุดต้องตรงกันทุกเหรียญ (สเปก §6.1) — ประวัติจะลึกไม่เท่ากันได้
    # (เหรียญที่ลิสต์ทีหลังมีแท่งน้อยกว่า) แต่ "แท่งปิดล่าสุด" ต้องเป็น timestamp เดียวกัน
    ts = last_ts[btc]
    misaligned = [s for s, t in last_ts.items() if t != ts]
    if misaligned:
        return _cash(ts, f"แท่งล่าสุดไม่ตรงกัน: {', '.join(misaligned)} ≠ BTC")
    # BTC ต้องมีประวัติพอคิด regime + score (ไม่งั้นคิดทั้งพอร์ตไม่ได้)
    if len(closes[btc]) < cfg.min_score_bars:
        return _cash(ts, f"ข้อมูล BTC ไม่พอ ({len(closes[btc])}/{cfg.min_score_bars} แท่ง)")

    # ---- BTC regime + crash filter (สเปก §7.7, §7.8) ----
    bc = closes[btc]
    ema_btc = seeded_ema(bc, cfg.btc_ema_bars)[-1]
    btc_close = bc[-1]
    btc_ret21 = btc_close / bc[-1 - cfg.crash_lookback_bars] - 1
    btc_trend_ok = ema_btc is not None and btc_close > ema_btc
    crash_ok = btc_ret21 > cfg.crash_return_floor

    dec = RebalanceDecision(
        bar_close_time=ts, btc_close=btc_close, btc_ema100=ema_btc or float("nan"),
        btc_return_21=btc_ret21, btc_trend_ok=btc_trend_ok, crash_filter_ok=crash_ok,
        breadth=0.0, gross_exposure=0.0, to_cash=True,
    )

    # ---- score ทุกเหรียญ (สเปก §7.4–7.6, §7.10) ----
    for sym in cfg.symbols:
        cl = closes[sym]
        if len(cl) < cfg.min_score_bars:
            continue    # ประวัติไม่พอ (เหรียญลิสต์ทีหลัง) — ยังไม่คิด ไม่นับใน breadth
        rets = _returns(cl)
        emas = {n: seeded_ema(cl, n)[-1] for n in cfg.ema_horizons}
        moms = {h: (cl[-1] / cl[-1 - h] - 1) for h in cfg.momentum_horizons}
        tv = sum(1 for n in cfg.ema_horizons if emas[n] is not None and cl[-1] > emas[n])
        mv = sum(1 for h in cfg.momentum_horizons if moms[h] > 0)
        window = rets[-cfg.volatility_lookback_bars:]
        v = pstdev(window) if len(window) == cfg.volatility_lookback_bars else 0.0
        score = tv + mv
        dec.scores[sym] = SymbolScore(
            symbol=sym, close=cl[-1], ema={n: (emas[n] or float("nan")) for n in emas},
            momentum=moms, trend_votes=tv, momentum_votes=mv, ensemble_score=score,
            vol_4h=v, annual_vol=v * math.sqrt(cfg.annualization_bars),
            eligible=(score >= cfg.min_ensemble_score and v > 0),
        )

    # breadth = สัดส่วนเหรียญที่ score >= 3 (สเปก §7.9) — นับก่อนกรอง vol
    n_uni = len(cfg.symbols)
    dec.breadth = sum(1 for s in dec.scores.values()
                      if s.ensemble_score >= cfg.min_ensemble_score) / n_uni

    if not btc_trend_ok:
        dec.reason = "BTC ต่ำกว่า EMA100 → ถือเงินสด"
        return dec
    if not crash_ok:
        dec.reason = f"BTC ร่วง {btc_ret21*100:.1f}%/21 แท่ง (crash filter) → ถือเงินสด"
        return dec

    # ---- ranking + top-N (สเปก §8) ----
    elig = [s for s in dec.scores.values() if s.eligible]
    # tie-break: score desc, momentum63 desc, symbol desc (reverse-alpha)
    m63 = cfg.momentum_horizons[1] if len(cfg.momentum_horizons) > 1 else cfg.momentum_horizons[0]
    elig.sort(key=lambda s: (s.ensemble_score, s.momentum[m63], s.symbol), reverse=True)
    sel = elig[:cfg.top_n]
    if not sel:
        dec.reason = "ไม่มีเหรียญผ่านเกณฑ์ score ≥ 3 → ถือเงินสด"
        return dec

    # ---- weights (สเปก §9) ----
    raw = {s.symbol: s.ensemble_score / max(s.vol_4h, eps) for s in sel}
    tot = sum(raw.values())
    base = {sym: raw[sym] / tot for sym in raw}
    est_vol = sum(base[s.symbol] * s.annual_vol for s in sel)
    vol_mult = min(1.0, cfg.target_annual_volatility / max(est_vol, eps))
    breadth_mult = min(1.0, dec.breadth / cfg.breadth_full_exposure_level)
    gross = min(cfg.max_gross_exposure, vol_mult * breadth_mult)

    for i, s in enumerate(sel):
        s.rank = i + 1
        s.target_weight = base[s.symbol] * gross
    dec.selected = [s.symbol for s in sel]
    dec.target_weights = {s.symbol: s.target_weight for s in sel}
    dec.gross_exposure = gross
    dec.to_cash = False
    dec.reason = (f"เลือก {len(sel)} เหรียญ · breadth {dec.breadth*100:.0f}% · "
                  f"gross {gross*100:.0f}% (vol×{vol_mult:.2f} breadth×{breadth_mult:.2f})")
    return dec


def _cash(ts: int, reason: str) -> RebalanceDecision:
    return RebalanceDecision(
        bar_close_time=ts, btc_close=float("nan"), btc_ema100=float("nan"),
        btc_return_21=float("nan"), btc_trend_ok=False, crash_filter_ok=False,
        breadth=0.0, gross_exposure=0.0, to_cash=True, reason=reason,
    )

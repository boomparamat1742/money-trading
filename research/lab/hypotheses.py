"""สมมติฐาน edge ทั้งหมด — เพิ่มอันใหม่โดยเขียน subclass แล้วใส่ใน REGISTRY

กติกา: param_grid ต้องประกาศ "ก่อน" เห็นผล และควรเล็ก (grid ใหญ่ = จูนจนหลอกตัวเอง)
"""
from __future__ import annotations

from .core import (ANNUAL, DAY_MS, DataBundle, Hypothesis, load_funding,
                   load_ohlcv, load_oi, load_perp, load_prices)

MAJORS = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "LTC",
          "LINK", "DOT", "AVAX", "TRX", "ATOM", "BCH", "ETC", "XLM"]

# เหรียญนอกกลุ่มหลัก — สภาพคล่องต่ำกว่า สถาบันเข้ายาก (สนามที่แข่งน้อยกว่า)
SMALLCAPS = ["INJ", "SEI", "TIA", "ARB", "OP", "SUI", "APT", "FTM", "ALGO",
             "SAND", "MANA", "AXS", "GALA", "CHZ", "ENJ", "ONE", "ZIL",
             "IOTA", "NEO", "QTUM", "WAVES", "KAVA", "RVN", "ANKR"]

TRADE_COST = 0.0005   # 0.05% ต่อการเปลี่ยนน้ำหนัก 1 หน่วย (taker ต่อขา)


# ---------------------------------------------------------------- helpers
def _daily_returns(prices: dict[int, float], dates: list[int]) -> dict[int, float]:
    out = {}
    for i in range(1, len(dates)):
        c, p = prices.get(dates[i]), prices.get(dates[i - 1])
        if c is not None and p:
            out[dates[i]] = c / p - 1
    return out


def _equal_weight_market(data: DataBundle):
    dates = data.dates
    t, r = [], []
    for i in range(1, len(dates)):
        d, dp = dates[i], dates[i - 1]
        rs = [s[d] / s[dp] - 1 for s in data.prices.values()
              if s.get(d) is not None and s.get(dp)]
        if rs:
            t.append(d); r.append(sum(rs) / len(rs))
    return t, r


def _xs_momentum(data: DataBundle, lookback: int, top_k: int, rebalance: int,
                 cost: float = TRADE_COST):
    """Long the top-K trailing performers, equal weight, rebalanced every N days."""
    dates = data.dates
    holdings: dict[str, float] = {}
    pending = 0.0
    t_out, r_out, pos_out = [], [], []
    for i in range(1, len(dates)):
        d, dp = dates[i], dates[i - 1]
        r = 0.0
        for sym, w in holdings.items():
            c, p = data.prices[sym].get(d), data.prices[sym].get(dp)
            if c is not None and p:
                r += w * (c / p - 1)
        r -= pending
        pending = 0.0
        t_out.append(d); r_out.append(r); pos_out.append(1.0 if holdings else 0.0)

        if i % rebalance == 0 and i - lookback >= 0:
            d0 = dates[i - lookback]
            ranked = []
            for sym, s in data.prices.items():
                c, c0 = s.get(d), s.get(d0)
                if c is not None and c0 and c0 > 0:
                    ranked.append((sym, c / c0 - 1))
            if len(ranked) >= top_k:
                ranked.sort(key=lambda x: -x[1])
                new = {sym: 1.0 / top_k for sym, _ in ranked[:top_k]}
                turnover = sum(abs(new.get(s, 0) - holdings.get(s, 0))
                               for s in set(new) | set(holdings))
                pending = cost * turnover
                holdings = new
    return t_out, r_out, pos_out


# ---------------------------------------------------------------- hypotheses
class TSMomentum(Hypothesis):
    name = "tsmom_btc"
    question = "ถือ BTC เฉพาะตอนโมเมนตัมย้อนหลังเป็นบวก ชนะการถือเฉยๆ ไหม?"
    neutral = False
    cost_note = f"{TRADE_COST*100:.2f}% ต่อการสลับสถานะ"

    def param_grid(self):
        return [{"lookback": L} for L in (20, 30, 50, 80, 100, 150, 200)]

    def load(self):
        return DataBundle(prices=load_prices(["BTC"], max_age_hours=self.max_age_hours))

    def run(self, data, params):
        L = params["lookback"]
        dates = data.dates
        px = data.prices["BTC"]
        t, r, pos = [], [], []
        prev = 0.0
        for i in range(L + 1, len(dates)):
            d, dp, d0 = dates[i], dates[i - 1], dates[i - 1 - L]
            if None in (px.get(d), px.get(dp), px.get(d0)) or not px.get(d0):
                continue
            p = 1.0 if px[dp] / px[d0] - 1 > 0 else 0.0
            day = px[d] / px[dp] - 1
            t.append(d); r.append(p * day - TRADE_COST * abs(p - prev)); pos.append(p)
            prev = p
        return t, r, pos

    def benchmark(self, data):
        dates = data.dates
        px = data.prices["BTC"]
        t, r = [], []
        for i in range(1, len(dates)):
            d, dp = dates[i], dates[i - 1]
            if px.get(d) is not None and px.get(dp):
                t.append(d); r.append(px[d] / px[dp] - 1)
        return t, r


class XSMomentumMajors(Hypothesis):
    name = "xsmom_majors"
    question = "ซื้อเหรียญหลักที่วิ่งแรงสุด (top-K) ชนะการถือทั้งตลาดไหม?"
    neutral = False
    cost_note = f"{TRADE_COST*100:.2f}% ต่อ turnover 1 หน่วย"

    UNIVERSE = MAJORS

    def param_grid(self):
        return [{"lookback": L, "top_k": k, "rebalance": rb}
                for L in (30, 60, 90) for k in (3, 5) for rb in (7,)]

    def load(self):
        return DataBundle(prices=load_prices(self.UNIVERSE, max_age_hours=self.max_age_hours))

    def run(self, data, params):
        return _xs_momentum(data, params["lookback"], params["top_k"], params["rebalance"])

    def benchmark(self, data):
        return _equal_weight_market(data)


class XSMomentumSmallCap(XSMomentumMajors):
    """การทดลอง B: กลยุทธ์เดิม แต่ย้ายไปสนามที่สถาบันเข้าไม่ได้"""
    name = "xsmom_smallcap"
    question = "momentum แบบเดียวกัน แต่บนเหรียญเล็ก (แข่งขันน้อยกว่า) — มี edge ไหม?"
    UNIVERSE = SMALLCAPS


class FundingCarry(Hypothesis):
    name = "funding_carry"
    question = "long spot + short perp เก็บ funding (market-neutral) ได้ผลตอบแทนบวกหลังต้นทุนไหม?"
    neutral = True
    cost_note = "0.15% ต่อการเข้า/ออกสถานะ (spot+perp taker)"

    ONE_WAY = 0.0015
    RECORDS = 6000        # funding ทุก 8 ชม. → ~2000 วัน ให้ได้หลาย fold
    cost_note = "0.15%/เข้าออก + basis P&L (perp−spot) รายวัน"

    def param_grid(self):
        return [{"lookback": L, "top_n": n, "floor": f}
                for L in (7, 14, 30) for n in (3, 5) for f in (0.0, 0.0002, 0.0005)]

    def load(self):
        funding = load_funding(MAJORS, records=self.RECORDS, max_age_hours=self.max_age_hours)
        prices = load_prices(MAJORS, max_age_hours=self.max_age_hours)
        perp = load_perp(MAJORS, max_age_hours=self.max_age_hours)
        # จัดแกนวันให้อยู่ในช่วงที่มี funding จริง — ไม่งั้น spot ที่ยาวกว่าจะพา
        # วันที่ไม่มี funding (ถือไม่ได้) เข้ามาเป็น 0 เยอะจนบิด fold
        fdays = [d for m in funding.values() for d in m]
        if fdays:
            lo, hi = min(fdays), max(fdays)
            prices = {s: {d: v for d, v in m.items() if lo <= d <= hi} for s, m in prices.items()}
        return DataBundle(funding=funding, prices=prices, perp=perp)

    def run(self, data, params):
        L, N, floor = params["lookback"], params["top_n"], params["floor"]
        dates = data.dates
        spot, perp = data.prices, data.perp
        prev: set[str] = set()
        t, r, pos = [], [], []
        for i, d in enumerate(dates):
            scored = []
            for sym, m in data.funding.items():
                window = [m[dates[j]] for j in range(max(0, i - L), i) if dates[j] in m]
                if window and d in m:
                    avg = sum(window) / len(window)
                    if avg > floor:
                        scored.append((sym, avg))
            scored.sort(key=lambda x: -x[1])
            held = {s for s, _ in scored[:N]}
            turn = 0.0
            if held != prev:
                nn, no = (len(held) or 1), (len(prev) or 1)
                turn = sum(abs((1 / nn if s in held else 0) - (1 / no if s in prev else 0))
                           for s in held | prev)
                prev = held
            # return รายวันของ long-spot/short-perp = funding − การเปลี่ยนของ basis
            #   price P&L = spot_ret − perp_ret = −(perp_ret − spot_ret) = −Δbasis
            # ถ้าไม่คิด Δbasis (แบบเดิม) vol จะต่ำปลอมจน Sharpe เว่อร์ — basis คือ
            # ความเสี่ยงจริงของ carry ที่ทำให้ leg สองข้างไม่หักล้างกันเป๊ะรายวัน
            day_ret = -self.ONE_WAY * turn
            if held and i >= 1:
                dp = dates[i - 1]
                contribs = []
                for s in held:
                    f = data.funding[s].get(d, 0.0)
                    sc, sp = spot.get(s, {}).get(d), spot.get(s, {}).get(dp)
                    pc, pp = perp.get(s, {}).get(d), perp.get(s, {}).get(dp)
                    basis_chg = ((pc / pp - 1) - (sc / sp - 1)) if (sc and sp and pc and pp) else 0.0
                    contribs.append(f - basis_chg)
                if contribs:
                    day_ret += sum(contribs) / len(contribs)
            t.append(d); r.append(day_ret); pos.append(1.0 if held else 0.0)
        return t, r, pos


# ---------------------------------------------------------------- real strategy
def _trades_to_daily(trades, dates: list[int], equity: float):
    """แปลงไม้จาก backtest → ผลตอบแทน "รายวัน" บนแกนวันเดียวกับ benchmark

    กำไร/ขาดทุนของไม้ผูกกับ "วันที่ปิด" (mark-to-close) หารด้วยทุน = return เศษส่วน
    วันไหนไม่มีไม้ปิด = 0 (พอร์ตนิ่ง) ตำแหน่ง = 1 ถ้ามีไม้เปิดคาบวันนั้น เพื่อให้
    exposure สะท้อนว่าอยู่ในตลาดจริงกี่ % ของเวลา
    """
    ret = {d: 0.0 for d in dates}
    held = {d: 0.0 for d in dates}
    for t in trades:
        if t.closed_at is not None and (t.pnl_amount is not None):
            day = (t.closed_at // DAY_MS) * DAY_MS
            if day in ret:
                ret[day] += t.pnl_amount / equity
        if t.opened_at is not None:
            lo = (t.opened_at // DAY_MS) * DAY_MS
            hi = (t.closed_at // DAY_MS) * DAY_MS if t.closed_at else lo
            for d in dates:
                if lo <= d <= hi:
                    held[d] = 1.0
    return dates, [ret[d] for d in dates], [held[d] for d in dates]


class TrendFollowHTF(Hypothesis):
    """กลยุทธ์ production จริง (EMA/ADX/MACD + ATR stop + trailing) บนกรอบเวลาที่
    ค่าธรรมเนียมไม่กลืน — 15m ตายเพราะ fee 0.70R/ไม้ แต่ 4h stop กว้างพอให้เหลือ
    ~0.09R คำถามคือ: พอ fee ไม่ฆ่า สัญญาณเดิมมี edge เหลือไหม?

    ยืนยันด้วย 1d (สูงกว่า 4h) เพราะ HTF gate ของกลยุทธ์บังคับให้เทรดตามเทรนด์ใหญ่
    """
    name = "trend_follow_4h"
    question = "กลยุทธ์เดิมบน 4h (ยืนยันด้วย 1d) ที่ค่าธรรมเนียมไม่กลืน มี edge ไหม?"
    neutral = False
    cost_note = "taker+slippage จริงต่อไม้ (ตามค่า default ของระบบ)"

    UNIVERSE = ["BTC", "ETH", "BNB"]      # เหมือนที่รันสดจริง
    BASE_TF = "4h"
    CONFIRM = ("1d",)
    BARS = 12_000                         # ~5.5 ปีของ 4h → ได้หลาย fold ครอบหลาย regime
    EQUITY = 10_000.0                      # Sharpe ไม่ขึ้นกับค่าทุน แต่เลี่ยงเลขเศษ

    def param_grid(self):
        # ประกาศล่วงหน้า · เล็ก · คร่อมค่าที่ระบบใช้จริง (sl 1.5 / tp 3.0)
        return [{"sl": sl, "tp": tp} for sl in (1.5, 2.0) for tp in (3.0, 4.0)]

    def load(self):
        bars = load_ohlcv(self.UNIVERSE, self.BASE_TF, bars=self.BARS,
                          max_age_hours=self.max_age_hours)
        prices = load_prices(self.UNIVERSE, max_age_hours=self.max_age_hours)
        # จัดแกนวันให้ตรงกับช่วงที่แท่ง 4h ครอบคลุมจริง — ไม่งั้น fold ช่วงต้นจะเป็น
        # ศูนย์ล้วน (กลยุทธ์ยังไม่มีข้อมูลให้เทรด) แล้วสถิติเพี้ยน
        spans = [ (c[0].open_time, c[-1].open_time) for c in bars.values() if c ]
        if spans:
            lo = min(s[0] for s in spans); hi = max(s[1] for s in spans)
            prices = {sym: {d: v for d, v in m.items() if lo <= d <= hi}
                      for sym, m in prices.items()}
        return DataBundle(prices=prices, bars=bars)

    def run(self, data, params):
        from worker.app.config import Fees, RiskPolicy
        from backtest.run_backtest import run as run_bt

        policy = RiskPolicy()
        object.__setattr__(policy, "account_equity", self.EQUITY)  # frozen dataclass
        fees = Fees()
        dates = data.dates
        per_symbol_ret = []
        held_any = {d: 0.0 for d in dates}
        for sym, candles in data.bars.items():
            if not candles:
                continue
            out = run_bt(candles, policy, fees, confirm_tfs=self.CONFIRM,
                         sl_mult=params["sl"], tp_mult=params["tp"])
            _, r, pos = _trades_to_daily(out.trades, dates, self.EQUITY)
            per_symbol_ret.append(r)
            for i, d in enumerate(dates):
                if pos[i]:
                    held_any[d] = 1.0
        if not per_symbol_ret:
            return [], [], []
        # ถ่วงน้ำหนักเท่ากันทุกเหรียญ = จัดสรรทุนเท่าๆ กัน (ไม่ใช่ 3 เท่าของเดิมพันเดียว)
        n = len(per_symbol_ret)
        port = [sum(col) / n for col in zip(*per_symbol_ret)]
        return dates, port, [held_any[d] for d in dates]

    def benchmark(self, data):
        return _equal_weight_market(data)


# ---------------------------------------------------------------- OI-confirmed
def _oi_confirmed_daily(px: dict[int, float], oi: dict[int, float],
                        dates: list[int], L: int, cost: float):
    """ผลตอบแทนรายวันของกลยุทธ์ "ราคา+OI ยืนยันทิศ" (Technical Paper §15)

      ราคาขึ้น + OI ขึ้น  → new money เข้า long  → +1 (long)
      ราคาลง  + OI ขึ้น  → new money เข้า short → −1 (short)
      OI ลง               → คนปิดสถานะ ไม่ชัด    →  0 (flat)

    position ตัดสินจากข้อมูลถึงวัน d แล้วถือข้ามวัน d+1 (ไม่ look-ahead)
    """
    r = [0.0] * len(dates)
    pos_out = [0.0] * len(dates)
    prev = 0
    held_seq = [0] * len(dates)          # position ที่ถือ "ระหว่างวัน" i
    for i in range(len(dates)):
        d = dates[i]
        target = prev
        if i >= L:
            d0 = dates[i - L]
            p, p0 = px.get(d), px.get(d0)
            o, o0 = oi.get(d), oi.get(d0)
            if p and p0 and o and o0:
                price_up = p > p0
                oi_up = o > o0
                target = (1 if price_up else -1) if oi_up else 0
        held_seq[i] = target
        prev = target
    for i in range(1, len(dates)):
        d, dp = dates[i], dates[i - 1]
        held = held_seq[i - 1]           # ตัดสินเมื่อวาน ถือวันนี้
        before = held_seq[i - 2] if i >= 2 else 0
        p, pp = px.get(d), px.get(dp)
        ret = (p / pp - 1) if (p and pp) else 0.0
        r[i] = held * ret - cost * abs(held - before)
        pos_out[i] = 1.0 if held != 0 else 0.0
    return r, pos_out


class PriceOIConfirm(Hypothesis):
    """ใช้ Open Interest ยืนยันทิศทางราคา — มิติข้อมูลจาก futures ที่อินดิเคเตอร์
    ราคาล้วนมองไม่เห็น (Technical Paper §15) เทรดทั้ง long/short ตาม new money
    ที่ไหลเข้า ต้องชนะการถือเฉยๆ หลังหักต้นทุนถึงจะนับว่ามี edge
    """
    name = "price_oi_confirm"
    question = "ใช้ OI ยืนยันทิศราคา (new money เข้า long/short) ชนะ buy-and-hold ไหม?"
    neutral = False
    cost_note = f"{TRADE_COST*100:.2f}% ต่อการสลับสถานะ"
    UNIVERSE = ["BTC", "ETH", "BNB"]

    def param_grid(self):
        return [{"lookback": L} for L in (3, 7, 14, 30)]

    def load(self):
        prices = load_prices(self.UNIVERSE, max_age_hours=self.max_age_hours)
        oi = load_oi(self.UNIVERSE)
        # จำกัดแกนวันให้อยู่ในช่วงที่มี OI (OI สั้นกว่าราคา)
        odays = [d for m in oi.values() for d in m]
        if odays:
            lo, hi = min(odays), max(odays)
            prices = {s: {d: v for d, v in m.items() if lo <= d <= hi} for s, m in prices.items()}
        return DataBundle(prices=prices, funding=oi)   # เก็บ OI ใน funding slot (dict เหมือนกัน)

    def run(self, data, params):
        dates = data.dates
        L = params["lookback"]
        per_sym = []
        held_any = [0.0] * len(dates)
        for sym in self.UNIVERSE:
            if sym not in data.prices or sym not in data.funding:
                continue
            r, pos = _oi_confirmed_daily(data.prices[sym], data.funding[sym],
                                         dates, L, TRADE_COST)
            per_sym.append(r)
            for i, p in enumerate(pos):
                if p:
                    held_any[i] = 1.0
        if not per_sym:
            return [], [], []
        n = len(per_sym)
        return dates, [sum(c) / n for c in zip(*per_sym)], held_any

    def benchmark(self, data):
        return _equal_weight_market(data)


REGISTRY: dict[str, type[Hypothesis]] = {
    h.name: h for h in (TSMomentum, XSMomentumMajors, XSMomentumSmallCap,
                        FundingCarry, TrendFollowHTF, PriceOIConfirm)
}

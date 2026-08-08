"""สมมติฐาน edge ทั้งหมด — เพิ่มอันใหม่โดยเขียน subclass แล้วใส่ใน REGISTRY

กติกา: param_grid ต้องประกาศ "ก่อน" เห็นผล และควรเล็ก (grid ใหญ่ = จูนจนหลอกตัวเอง)
"""
from __future__ import annotations

from .core import ANNUAL, DAY_MS, DataBundle, Hypothesis, load_funding, load_prices

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

    def param_grid(self):
        return [{"lookback": L, "top_n": n, "floor": f}
                for L in (7, 14, 30) for n in (3, 5) for f in (0.0, 0.0002, 0.0005)]

    def load(self):
        return DataBundle(funding=load_funding(MAJORS, max_age_hours=self.max_age_hours))

    def run(self, data, params):
        L, N, floor = params["lookback"], params["top_n"], params["floor"]
        dates = data.dates
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
            if held:
                t.append(d)
                r.append(sum(data.funding[s][d] for s in held) / len(held) - self.ONE_WAY * turn)
                pos.append(1.0)
        return t, r, pos


REGISTRY: dict[str, type[Hypothesis]] = {
    h.name: h for h in (TSMomentum, XSMomentumMajors, XSMomentumSmallCap, FundingCarry)
}

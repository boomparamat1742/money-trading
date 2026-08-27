"""Paper portfolio accounting สำหรับ RTE (สเปก §12–15).

ถือ cash + long positions หลายเหรียญ · rebalance ตาม target weights ด้วยราคา fill
(paper: next-bar-open ± slippage) · คิด fee/slippage/funding · ติดตาม equity/peak/
drawdown · circuit breaker: single-symbol weight cap + DD → RISK_HALT

⚠️ paper เท่านั้น — ไม่เคยส่งคำสั่งจริง (cfg.live_trading_enabled ต้องเป็น False)
Decimal ไม่ได้ใช้ (paper ไม่มี tick-size จริง) — float พอสำหรับจำลอง
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import RTEConfig


@dataclass
class Position:
    qty: float = 0.0
    avg_entry: float = 0.0


@dataclass
class RebalanceFill:
    symbol: str
    side: str          # buy | sell
    delta_qty: float
    fill_price: float  # รวม slippage แล้ว
    notional: float
    fee: float
    slippage: float


@dataclass
class PaperPortfolio:
    cfg: RTEConfig
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    peak_equity: float = 0.0
    realized_pnl: float = 0.0
    cumulative_fee: float = 0.0
    cumulative_slippage: float = 0.0
    cumulative_funding: float = 0.0
    last_rebalance_time: int | None = None
    halted: bool = False
    started_at: int | None = None

    @classmethod
    def new(cls, cfg: RTEConfig, ts: int | None = None) -> "PaperPortfolio":
        eq = cfg.starting_equity
        return cls(cfg=cfg, cash=eq, peak_equity=eq, started_at=ts)

    # ---- valuation ----
    def equity(self, marks: dict[str, float]) -> float:
        return self.cash + sum(p.qty * marks.get(s, p.avg_entry)
                               for s, p in self.positions.items())

    def gross_exposure(self, marks: dict[str, float]) -> float:
        eq = self.equity(marks)
        if eq <= 0:
            return 0.0
        return sum(abs(p.qty * marks.get(s, p.avg_entry))
                   for s, p in self.positions.items()) / eq

    def drawdown(self, marks: dict[str, float]) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return self.equity(marks) / self.peak_equity - 1

    # ---- risk overlay: single-symbol weight cap (สเปก §15) ----
    def _apply_caps(self, weights: dict[str, float]) -> dict[str, float]:
        """cap น้ำหนักต่อเหรียญไม่ให้เกิน max_single_symbol_weight แล้ว redistribute
        ส่วนเกินให้เหรียญที่ยังไม่ชนเพดาน (1 รอบ) ที่เหลือ (ถ้าชนหมด) ตกเป็น cash —
        gross ไม่มีทางเกินของเดิม (สเปก §15)"""
        cap = self.cfg.max_single_symbol_weight
        capped = {s: min(v, cap) for s, v in weights.items()}
        excess = sum(weights.values()) - sum(capped.values())
        room = {s: cap - capped[s] for s in capped if capped[s] < cap - 1e-12}
        tot_room = sum(room.values())
        if excess > 1e-12 and tot_room > 1e-12:
            for s in room:
                capped[s] += excess * room[s] / tot_room
        return capped

    # ---- rebalance (สเปก §12) ----
    def rebalance(self, target_weights: dict[str, float],
                  fill_prices: dict[str, float], ts: int) -> list[RebalanceFill]:
        """ปรับพอร์ตเข้าหา target weights ด้วยราคา fill (paper) · คืนรายการ fill

        ถ้า halted → บังคับ target = cash (ล้างพอร์ต) ไม่รับ entry ใหม่ (สเปก §15)
        """
        tw = {} if self.halted else self._apply_caps(dict(target_weights or {}))
        eq = self.equity(fill_prices)
        fills: list[RebalanceFill] = []

        universe = set(self.cfg.symbols) | set(self.positions)
        for sym in sorted(universe):
            price = fill_prices.get(sym)
            if not price or price <= 0:
                continue
            pos = self.positions.get(sym, Position())
            cur_w = (pos.qty * price / eq) if eq > 0 else 0.0
            tgt_w = tw.get(sym, 0.0)
            if abs(tgt_w - cur_w) < self.cfg.min_weight_change:
                continue    # HOLD — เปลี่ยนน้อยเกินไป (สเปก §11)

            tgt_qty = tgt_w * eq / price
            delta = tgt_qty - pos.qty
            notional = abs(delta) * price
            if notional < self.cfg.min_notional_usdt:
                continue

            buy = delta > 0
            fill_eff = price * (1 + self.cfg.slippage_rate) if buy \
                else price * (1 - self.cfg.slippage_rate)
            fee = notional * self.cfg.fee_rate
            slip = notional * self.cfg.slippage_rate

            self.cash -= delta * fill_eff   # buy (delta>0) หักเงินสด · sell คืนเงินสด
            self.cash -= fee
            self.cumulative_fee += fee
            self.cumulative_slippage += slip  # เพื่อรายงาน (ฝังใน fill_eff แล้ว ไม่หักซ้ำ)

            pos = self.positions.setdefault(sym, Position())
            if buy:
                new_qty = pos.qty + delta
                pos.avg_entry = ((pos.avg_entry * pos.qty + delta * fill_eff) / new_qty
                                 if new_qty else 0.0)
                pos.qty = new_qty
            else:
                sold = min(-delta, pos.qty)
                self.realized_pnl += sold * (fill_eff - pos.avg_entry)
                pos.qty += delta
                if abs(pos.qty) < 1e-12:
                    pos.qty = 0.0; pos.avg_entry = 0.0
            fills.append(RebalanceFill(sym, "buy" if buy else "sell",
                                       delta, fill_eff, notional, fee, slip))

        self.positions = {s: p for s, p in self.positions.items() if abs(p.qty) > 1e-12}
        self.last_rebalance_time = ts
        eq2 = self.equity(fill_prices)
        self.peak_equity = max(self.peak_equity, eq2)
        if self.peak_equity > 0 and (eq2 / self.peak_equity - 1) <= -self.cfg.max_strategy_drawdown:
            self.halted = True   # RISK_HALT — resume ต้อง manual (สเปก §15)
        return fills

    # ---- funding (สเปก §13) ----
    def apply_funding(self, funding_rates: dict[str, float],
                      marks: dict[str, float]) -> float:
        """long จ่าย funding เมื่อ rate เป็นบวก: funding_pnl = −notional × rate"""
        total = 0.0
        for s, p in self.positions.items():
            notional = p.qty * marks.get(s, p.avg_entry)
            total += -notional * funding_rates.get(s, 0.0)
        self.cash += total
        self.cumulative_funding += total
        return total

    # ---- snapshot (สเปก §14) ----
    def snapshot(self, marks: dict[str, float]) -> dict:
        eq = self.equity(marks)
        unreal = sum(p.qty * (marks.get(s, p.avg_entry) - p.avg_entry)
                     for s, p in self.positions.items())
        return {
            "cash": self.cash,
            "equity": eq,
            "peak_equity": self.peak_equity,
            "drawdown": (eq / self.peak_equity - 1) if self.peak_equity > 0 else 0.0,
            "gross_exposure": self.gross_exposure(marks),
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": unreal,   # Σ qty × (mark − avg_entry)
            "cumulative_fee": self.cumulative_fee,
            "cumulative_slippage": self.cumulative_slippage,
            "cumulative_funding": self.cumulative_funding,
            "halted": self.halted,
            "positions": {s: {"qty": p.qty, "avg_entry": p.avg_entry}
                          for s, p in self.positions.items()},
        }

    # ---- persistence (Railway ดิสก์ ephemeral → เก็บ state ที่ Supabase) ----
    def to_dict(self) -> dict:
        return {
            "cash": self.cash, "peak_equity": self.peak_equity,
            "realized_pnl": self.realized_pnl, "cumulative_fee": self.cumulative_fee,
            "cumulative_slippage": self.cumulative_slippage,
            "cumulative_funding": self.cumulative_funding,
            "last_rebalance_time": self.last_rebalance_time, "halted": self.halted,
            "started_at": self.started_at,
            "positions": {s: {"qty": p.qty, "avg_entry": p.avg_entry}
                          for s, p in self.positions.items()},
        }

    @classmethod
    def from_dict(cls, cfg: RTEConfig, d: dict) -> "PaperPortfolio":
        p = cls(cfg=cfg, cash=d["cash"], peak_equity=d["peak_equity"],
                realized_pnl=d.get("realized_pnl", 0.0),
                cumulative_fee=d.get("cumulative_fee", 0.0),
                cumulative_slippage=d.get("cumulative_slippage", 0.0),
                cumulative_funding=d.get("cumulative_funding", 0.0),
                last_rebalance_time=d.get("last_rebalance_time"),
                halted=d.get("halted", False), started_at=d.get("started_at"))
        p.positions = {s: Position(qty=v["qty"], avg_entry=v["avg_entry"])
                       for s, v in d.get("positions", {}).items()}
        return p

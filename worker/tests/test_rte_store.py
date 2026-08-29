"""RTE SQLite store — round-trip ของ state, rebalance, และ A3 positions log."""
from worker.app.rte.config import RTEConfig
from worker.app.rte.portfolio import PaperPortfolio
from worker.app.rte.store import _SqliteStore
from worker.app.rte import ensemble as ens


def _decision():
    d = ens.RebalanceDecision(bar_close_time=1_700_000_000_000, btc_close=1.0, btc_ema100=1.0,
                              btc_return_21=0.01, btc_trend_ok=True, crash_filter_ok=True,
                              breadth=0.5, gross_exposure=0.4, to_cash=False)
    d.selected = ["BTCUSDT", "ETHUSDT"]
    d.target_weights = {"BTCUSDT": 0.25, "ETHUSDT": 0.15}
    d.reason = "test"
    return d


def test_sqlite_state_and_rebalance_roundtrip(tmp_path):
    cfg = RTEConfig()
    st = _SqliteStore(str(tmp_path / "rte.db"), cfg)
    assert st.load_state("h1") is None
    pf = PaperPortfolio.new(cfg, ts=1)
    pf.rebalance({"BTCUSDT": 0.25, "ETHUSDT": 0.15}, {"BTCUSDT": 100.0, "ETHUSDT": 50.0}, ts=2)
    st.save_state("h1", pf, {"BTCUSDT": 0.25, "ETHUSDT": 0.15})
    loaded = st.load_state("h1")
    assert loaded["config_hash"] == "h1"
    assert set(loaded["state"]["positions"]) == {"BTCUSDT", "ETHUSDT"}

    dec = _decision()
    st.record_rebalance("h1", dec, 10000.0, 0.0, False)
    st.record_rebalance("h1", dec, 10000.0, 0.0, False)  # idempotent — bar_time ซ้ำ
    n = st.conn.execute("SELECT COUNT(*) c FROM rte_rebalances").fetchone()["c"]
    assert n == 1


def test_sqlite_positions_log(tmp_path):
    cfg = RTEConfig()
    st = _SqliteStore(str(tmp_path / "rte.db"), cfg)
    rows = [
        {"symbol": "BTCUSDT", "qty": 0.5, "avg_entry": 100.0, "mark_price": 110.0,
         "notional": 55.0, "unrealized_pnl": 5.0, "weight": 0.3},
        {"symbol": "ETHUSDT", "qty": 2.0, "avg_entry": 50.0, "mark_price": 45.0,
         "notional": 90.0, "unrealized_pnl": -10.0, "weight": 0.2},
    ]
    st.record_positions("h1", 1_700_000_000_000, rows)
    st.record_positions("h1", 1_700_000_000_000, rows)  # idempotent ต่อ (bar_time, symbol)
    got = st.conn.execute(
        "SELECT symbol, unrealized_pnl, weight FROM rte_positions_log ORDER BY symbol").fetchall()
    assert len(got) == 2                              # ไม่ซ้ำ
    d = {r["symbol"]: (r["unrealized_pnl"], r["weight"]) for r in got}
    assert d["BTCUSDT"] == (5.0, 0.3)
    assert d["ETHUSDT"] == (-10.0, 0.2)

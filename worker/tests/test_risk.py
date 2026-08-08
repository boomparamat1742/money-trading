from worker.app.config import RiskPolicy
from worker.app.models import Direction, IndicatorSnapshot, RiskStatus
from worker.app.risk import PortfolioState, evaluate_risk


def _snap(close=100.0, atr=2.0):
    return IndicatorSnapshot(ready=True, values={"close": close, "atr": atr})


def test_position_size_formula():
    policy = RiskPolicy(account_equity=10_000, risk_per_trade_pct=1.0, min_reward_risk=1.5)
    d = evaluate_risk(Direction.LONG, _snap(100, 2.0), policy, PortfolioState())
    assert d.status == RiskStatus.APPROVED
    # stop distance = 1.5*ATR = 3.0 ; risk_amount = 100 ; size = 100/3 = 33.33
    assert abs(d.risk_amount - 100.0) < 1e-6
    assert abs(d.position_size - (100.0 / 3.0)) < 1e-3
    assert d.stop_loss < d.entry_price < d.take_profit
    assert d.expected_rr >= 1.5


def test_kill_switch_rejects():
    policy = RiskPolicy(kill_switch=True)
    d = evaluate_risk(Direction.LONG, _snap(), policy, PortfolioState())
    assert d.status == RiskStatus.REJECTED and d.rejection_reason == "kill_switch"


def test_max_open_trades_rejects():
    policy = RiskPolicy(max_open_trades=1)
    d = evaluate_risk(Direction.LONG, _snap(), policy, PortfolioState(open_trades=1))
    assert d.status == RiskStatus.REJECTED and d.rejection_reason == "max_open_trades"


def test_daily_loss_limit_rejects():
    policy = RiskPolicy(daily_loss_limit_pct=3.0)
    d = evaluate_risk(Direction.LONG, _snap(), policy, PortfolioState(daily_loss_pct=3.0))
    assert d.status == RiskStatus.REJECTED and d.rejection_reason == "daily_loss_limit"


def test_insufficient_data_rejects():
    policy = RiskPolicy()
    snap = IndicatorSnapshot(ready=True, values={"close": 100.0})  # no ATR
    d = evaluate_risk(Direction.LONG, snap, policy, PortfolioState())
    assert d.status == RiskStatus.REJECTED and d.rejection_reason == "insufficient_data"


def test_loss_streak_cooldown_expires_next_day():
    """Regression: the streak cooldown must expire on its own. Resetting it only
    on a WIN deadlocks the system — blocked trades can never produce the win that
    would unblock them, so trading stops forever."""
    policy = RiskPolicy(max_consecutive_losses=4)
    p = PortfolioState()
    day1 = 1_700_000_000_000
    p.roll_day(day1)
    p.consecutive_losses = 4
    assert evaluate_risk(Direction.LONG, _snap(), policy, p).rejection_reason == "consecutive_loss_cooldown"

    p.roll_day(day1 + 86_400_000)  # next UTC day
    assert p.consecutive_losses == 0
    assert evaluate_risk(Direction.LONG, _snap(), policy, p).status == RiskStatus.APPROVED


def test_daily_loss_resets_next_day():
    policy = RiskPolicy(daily_loss_limit_pct=3.0)
    p = PortfolioState()
    day1 = 1_700_000_000_000
    p.roll_day(day1)
    p.daily_loss_pct = 3.0
    assert evaluate_risk(Direction.LONG, _snap(), policy, p).rejection_reason == "daily_loss_limit"
    p.roll_day(day1 + 86_400_000)
    assert p.daily_loss_pct == 0.0
    assert evaluate_risk(Direction.LONG, _snap(), policy, p).status == RiskStatus.APPROVED

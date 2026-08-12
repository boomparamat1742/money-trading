"""Real-time entry point (design §5) — live market watch + notifications.

    Binance WebSocket → Data Quality → Pipeline (Fast Path) → Paper Trade
    → LINE notify → (Slow Path) AI context → follow-up notify

Runs in PAPER-TRADING mode only — it never sends real orders. The quant signals
are NOT a proven edge (walk-forward showed none); this is a market-watch and
alerting tool.

Run:  python -m worker.app.main
Env:  see .env.example  (LINE_CHANNEL_TOKEN + LINE_TO for alerts; else console)
"""
from __future__ import annotations

import asyncio
import os

from .ai_context import get_context
from .config import load_settings
from .data_quality import DataQualityChecker
from .futures_data import fetch_oi_funding
from .htf import MultiTimeframeTrend
from .store import database_url, open_journal
from .market_data import BinanceSource
from .models import Candle
from .news import NewsService
from .notifier import (ConsoleNotifier, DailyQuota, DiscordNotifier, LineNotifier,
                       TelegramNotifier, format_close, format_daily_summary, format_signal)
from .paper_trading import PaperBroker, attach_exit_market, entry_from_signal
from .pipeline import SignalPipeline
from .risk import PortfolioState
from .version import build_line

DEFAULT_CONFIRM_TFS = ["1h", "4h"]  # matches the validated backtest logic (v1.1)


def confirm_tfs(s) -> list[str]:
    """Higher timeframes that must agree before a signal is allowed.

    Read from settings so CONFIRM_TIMEFRAMES in the environment actually does
    something — it used to be declared in config and then ignored here, which
    meant setting it on Railway silently changed nothing.
    """
    tfs = [t.strip() for t in (s.confirm_timeframes or "").split(",") if t.strip()]
    return tfs or DEFAULT_CONFIRM_TFS


def build_notifier(s):
    import os
    cap = int(os.environ.get("NOTIFY_MAX_PER_DAY", 20))
    # Discord webhook มาก่อน: ตั้ง DISCORD_WEBHOOK_URL = สลับมาใช้ทันที (ฟรี ไม่จำกัด)
    # โดยไม่ต้องลบ LINE creds — เอาออกเมื่อไหร่ก็กลับไป LINE เอง
    discord = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if discord:
        print("[startup] notifier: Discord webhook (ฟรี ไม่จำกัดโควตา)")
        return DiscordNotifier(discord)
    if s.line_channel_token and s.line_to:
        print(f"[startup] notifier: LINE Messaging API (cap {cap} msgs/day)")
        return DailyQuota(LineNotifier(s.line_channel_token, s.line_to), cap)
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if tg_token and tg_chat:
        print("[startup] notifier: Telegram")
        return TelegramNotifier(tg_token, tg_chat)
    print("[startup] notifier: console (no LINE/Telegram credentials set)")
    return ConsoleNotifier()


def _warmup(symbol: str, tf: str, pipeline, htf, exchange: str, bars: int = 1500) -> int:
    """Feed recent historical candles into the indicator + HTF engines so the
    system can produce signals immediately instead of waiting days. No trades or
    alerts are generated here. Returns the last warmed candle open_time."""
    from backtest.fetch_binance import BASE, PERP_BASE, fetch as fetch_klines
    from .market_data import futures_mode

    base = PERP_BASE if futures_mode() else BASE   # warmup ต้องมาจากตลาดเดียวกับ live
    print(f"[startup] warming up from ~{bars} recent {tf} {'futures' if futures_mode() else 'spot'} candles ...")
    try:
        rows = fetch_klines(symbol, tf, bars, base=base)
    except SystemExit as e:
        print(f"[startup] warm-up skipped ({e}); starting cold (signals may take days)")
        return 0
    if len(rows) > 1:
        rows = rows[:-1]  # drop the currently-forming (not-yet-closed) candle
    last = 0
    for r in rows:
        c = Candle(exchange=exchange, symbol=symbol, timeframe=tf, open_time=int(r[0]),
                   open=float(r[1]), high=float(r[2]), low=float(r[3]),
                   close=float(r[4]), volume=float(r[5]), is_closed=True)
        trend = htf.update(c)
        pipeline.candidate(c, htf_trend=trend)  # advances indicators; result ignored
        last = c.open_time
    print(f"[startup] warmed {len(rows)} candles; indicator engine has {pipeline.ind.count} bars")
    return last


async def run_symbol(symbol, s, pipeline, htf, last_warm, broker, quality,
                     news, notifier, portfolio, open_trades, journal=None) -> None:
    """เฝ้า 1 เหรียญ: stream → settle → signal → notify. แชร์ risk/notifier
    กับเหรียญอื่น แต่ indicators/htf/open_trades แยกต่อเหรียญ."""
    tf = s.primary_timeframe
    source = BinanceSource(exchange=s.exchange)
    log_oi = os.environ.get("LOG_OI", "true").lower() != "false"   # ปิดได้ด้วย LOG_OI=false
    print(f"[startup] streaming {symbol}@{tf} [{source.market}/{source.feed}] ...")
    async for candle in source.stream_closed_candles(symbol, tf):
        if candle.open_time <= last_warm:
            continue  # already seen during warm-up
        q = quality.check(candle)
        if not q.ok:
            print(f"[quality] {symbol} skip: {q.reason}")
            continue

        # roll daily risk counters (also expires the loss-streak cooldown —
        # without this the system locks up permanently after a losing streak)
        portfolio.roll_day(candle.open_time)

        # เก็บ OI/funding ของแท่งนี้ไว้สะสมสำหรับวิจัยภายหลัง (Binance ให้ประวัติ OI
        # ฟรีแค่ 30 วัน จึงต้องเก็บสดเอง) best-effort ล้วน — ห้ามทำให้ loop พัง
        if journal and log_oi:
            try:
                oi = await fetch_oi_funding(symbol)
                if oi:
                    journal.record_snapshot(symbol, candle.open_time, candle.close, oi)
            except Exception as e:
                print(f"[oi] {symbol} snapshot skipped: {e!r}")

        # settle open paper trades on the new bar. Freeing the risk slot happens
        # here so a signal on this same bar can use it; the journal write and the
        # alert wait until the indicators for this candle exist, so the closed
        # trade can carry the market conditions it died in.
        closed_now = []
        for t in list(open_trades):
            broker.update(t, candle)
            if t.status.value != "open":
                open_trades.remove(t)
                portfolio.open_trades -= 1
                portfolio.open_risk_pct -= t.risk_pct
                closed_now.append(t)
            elif journal:
                journal.update_trade(t)   # persist trailing stop / bars held

        htf_trend = htf.update(candle)
        sig = pipeline.process(candle, portfolio, htf_trend=htf_trend)

        for t in closed_now:
            attach_exit_market(t, pipeline.last_snapshot)
            if journal:
                journal.close_trade(t)
            print(f"[close] {t.symbol} {t.exit_reason} "
                  f"({t.exit_context.get('pattern')}) pnl={t.pnl_amount}")
            await notifier.send(format_close(t), priority="high")   # ปิด/SL สำคัญ ต้องได้เสมอ

        if not sig or sig.status != "approved":
            continue

        signal_id = journal.record_signal(sig) if journal else None
        decision = sig._decision  # type: ignore[attr-defined]
        trade = broker.open(decision, sig.direction, symbol, None, candle,
                            entry=entry_from_signal(sig))
        if journal:
            journal.open_trade(trade, signal_id)
        open_trades.append(trade)
        portfolio.open_trades += 1
        portfolio.open_risk_pct += decision.risk_pct or 0.0
        await notifier.send(format_signal(sig, ref=trade.db_id), priority="normal")  # เปิด = งดก่อนถ้าโควตาตึง
        print(f"[signal] ไม้ #{trade.db_id} {sig.direction.value} {symbol} "
              f"score={sig.signal_score} @ {sig.entry_price}")

        if s.ai_enabled:
            try:
                headlines = await news.recent_headlines(symbol)
                ctx = await get_context(sig, headlines, s)
                if ctx:
                    if journal and signal_id:
                        journal.attach_ai_context(signal_id, ctx)
                    note = ctx.get("summary", "")
                    if ctx.get("conflict_with_signal"):
                        note = "⚠️ ข่าวขัดแย้งกับสัญญาณ — " + note
                    await notifier.send(format_signal(sig, ai_note=note, ref=trade.db_id),
                                        priority="low")   # AI context เป็นข้อมูลเสริม งดได้ก่อน
            except Exception as e:
                print(f"[ai] {symbol} context skipped: {e!r}")


async def supervise(symbol, s, pipeline, htf, last_warm, broker, quality,
                    news, notifier, portfolio, open_trades, journal=None) -> None:
    """Keep one symbol's watcher alive. A monitor that dies silently is worse
    than no monitor, so unexpected errors are reported to LINE and the stream is
    restarted with backoff instead of taking the whole process down."""
    backoff = 5
    while True:
        try:
            await run_symbol(symbol, s, pipeline, htf, last_warm, broker, quality,
                             news, notifier, portfolio, open_trades, journal)
            return  # stream ended cleanly
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[error] {symbol} watcher crashed: {e!r}; restart in {backoff}s")
            try:
                await notifier.send(f"⚠️ ตัวเฝ้า {symbol} ขัดข้อง: {type(e).__name__} "
                                    f"— กำลังรีสตาร์ทใน {backoff} วินาที", priority="high")
            except Exception:
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)


def _edge_lab_tick(interval_hours: float) -> bool:
    """เช็คว่าถึงเวลารัน Edge Lab หรือยัง (จาก DB) ถ้าถึงก็รัน 1 รอบ คืน True ถ้ารัน

    ตั้งใจเป็น sync (บล็อกได้) — ผู้เรียกจะโยนเข้า thread ด้วย asyncio.to_thread
    เพราะ Edge Lab หนักและมี time.sleep ห้ามให้ไปแช่ event loop ของ WebSocket
    """
    from research.lab.watch import run_once
    from .store import open_registry

    reg = open_registry()
    try:
        h = reg.hours_since_last_run()
        if h is not None and h < interval_hours:
            return False
        print(f"[edgelab] ครบรอบ (ล่าสุด {'ไม่เคย' if h is None else f'{h:.0f} ชม.ก่อน'}) — รัน Edge Lab", flush=True)
        run_once(reg, notify=True, verbose=True)
        return True
    finally:
        reg.close()


def _fetch_daily_summary(since_ms: int, until_ms: int) -> dict:
    """เปิด journal ใหม่ในเธรด (ไม่ชนกับ connection ของ loop หลัก) แล้วดึงสรุป"""
    from .store import open_journal
    j = open_journal()
    try:
        return j.daily_summary(since_ms, until_ms)
    finally:
        j.close()


async def _daily_summary_loop(notifier, hour_utc: int = 11) -> None:
    """ส่งสรุปเหตุการณ์รอบวันเข้า LINE ทุกวันเวลา hour_utc (default 11:00 UTC = 18:00 ไทย)

    ตื่นที่เวลาถัดไปเสมอ: ถ้าตอนนี้เลยเวลาวันนี้แล้ว → นัดพรุ่งนี้ (ทน restart ไม่ส่งซ้ำ
    เพราะรีสตาร์ทหลังส่ง = now ≥ เวลา → เด้งไปพรุ่งนี้)
    """
    from datetime import datetime, timedelta, timezone
    while True:
        now = datetime.now(timezone.utc)
        nxt = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
        if now >= nxt:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            until = int(datetime.now(timezone.utc).timestamp() * 1000)
            since = until - 86_400_000
            summ = await asyncio.to_thread(_fetch_daily_summary, since, until)
            supp = notifier.pop_suppressed() if hasattr(notifier, "pop_suppressed") else 0
            await notifier.send(format_daily_summary(summ, since, until, supp), priority="high")
            print(f"[summary] ส่งสรุปรอบวันแล้ว (ปิด {summ['closed']} ไม้)")
        except Exception as e:
            print(f"[summary] ข้าม: {e!r}")


def _merge_oi_tick() -> int:
    """รวม OI สด (market_snapshots) → oi_history รายวัน — sync (บล็อกได้ โยนเข้าเธรด)"""
    from scripts.merge_live_oi import merge_oi
    from .store import database_url
    dsn = database_url()
    return merge_oi(dsn) if dsn else 0


async def _merge_oi_loop(hour_utc: int = 23) -> None:
    """รวม OI สดเข้า oi_history รายวัน ให้ series ต่อเนื่องโดยไม่ต้องรันมือ

    ตั้งเวลาใกล้จบวัน UTC (23:00) เพื่อให้ OHLC ของวันนั้นเกือบครบ · ตื่นเวลาถัดไป
    เสมอ ทน restart เหมือน _daily_summary_loop (upsert อยู่แล้ว รันซ้ำไม่พัง)
    """
    from datetime import datetime, timedelta, timezone
    while True:
        now = datetime.now(timezone.utc)
        nxt = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
        if now >= nxt:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            n = await asyncio.to_thread(_merge_oi_tick)
            print(f"[mergeoi] รวม OI สด → oi_history: {n} วัน-เหรียญ", flush=True)
        except Exception as e:
            print(f"[mergeoi] ข้าม: {e!r}")


async def _edge_lab_loop(interval_hours: float) -> None:
    """เฝ้าเวลาในโปรเซส worker (ทางเลือกแทน cron service แยก ที่ Railway trial
    ทำไม่ได้เพราะจำกัด 1 service) — poll ทุกชั่วโมง รันเมื่อครบรอบ best-effort"""
    while True:
        try:
            await asyncio.to_thread(_edge_lab_tick, interval_hours)
        except Exception as e:
            print(f"[edgelab] รอบนี้ข้าม: {e!r}")
        await asyncio.sleep(3600)   # เช็คทุก 1 ชม. (ตัวตัดสินจริงคือเวลาใน DB)


async def run() -> None:
    s = load_settings()
    print(f"[startup] {build_line()}")
    tf = s.primary_timeframe
    symbols = s.symbols[:s.max_symbols]  # respect MAX_SYMBOLS cap
    tfs = confirm_tfs(s)

    # shared across symbols: risk budget, broker, notifier, quality, news
    broker = PaperBroker(s.fees, trail_r_activate=1.0, trail_r_dist=1.0)
    quality = DataQualityChecker()
    news = NewsService(s.news_provider, s.news_api_key or None)
    notifier = build_notifier(s)
    portfolio = PortfolioState()

    journal = open_journal()

    # per-symbol: own pipeline + htf + open_trades, warmed from history.
    # Open trades are restored from the journal so a restart resumes them.
    per_symbol = []
    restored_total = 0
    for sym in symbols:
        pipeline = SignalPipeline(s.risk)
        htf = MultiTimeframeTrend(tf, tfs)
        last_warm = _warmup(sym, tf, pipeline, htf, s.exchange)
        restored = journal.load_open_trades(sym)
        restored_total += len(restored)
        portfolio.open_trades += len(restored)
        portfolio.open_risk_pct += sum(t.risk_pct for t in restored)
        if restored:
            print(f"[startup] {sym}: restored {len(restored)} open paper trade(s)")
        per_symbol.append((sym, pipeline, htf, last_warm, restored))

    await notifier.send(
        f"🟢 ระบบเฝ้าตลาดเริ่มทำงาน\n"
        f"สัญลักษณ์: {', '.join(symbols)} @ {tf} (ยืนยันด้วย {'+'.join(tfs)})\n"
        f"อุ่นเครื่องด้วยข้อมูลย้อนหลังแล้ว — อินดิเคเตอร์พร้อม\n"
        f"risk รวมทุกเหรียญ: สูงสุด {s.risk.max_open_trades} ไม้พร้อมกัน\n"
        f"โหมด: Paper Trading (ไม่ส่งคำสั่งจริง)\nAI context: {'เปิด' if s.ai_enabled else 'ปิด'}\n"
        f"{build_line()}",         # บอกว่า deploy ตัวไหนกำลังรัน — เช็คได้จากในไลน์เลย
        priority="normal",
    )
    print(f"[startup] streaming {len(symbols)} symbols ... (Ctrl+C to stop)")

    tasks = [supervise(sym, s, pipeline, htf, last_warm, broker, quality,
                       news, notifier, portfolio, open_trades, journal)
             for (sym, pipeline, htf, last_warm, open_trades) in per_symbol]

    # รัน Edge Lab ในโปรเซสเดียวกัน (Railway trial จำกัด 1 service ต่อ project
    # จึงตั้ง cron service แยกไม่ได้) — เปิดด้วย RUN_EDGE_LAB=true
    if os.environ.get("RUN_EDGE_LAB", "false").lower() == "true":
        interval = float(os.environ.get("EDGE_LAB_INTERVAL_HOURS", 168))  # สัปดาห์ละครั้ง
        print(f"[startup] Edge Lab ในโปรเซส: เปิด (ทุก {interval:.0f} ชม.)")
        tasks.append(_edge_lab_loop(interval))

    # สรุปเหตุการณ์รอบวันเข้า LINE (default 11:00 UTC = 18:00 ไทย) ปิดด้วย DAILY_SUMMARY=false
    if journal and os.environ.get("DAILY_SUMMARY", "true").lower() != "false":
        hour = int(os.environ.get("DAILY_SUMMARY_HOUR_UTC", 11))
        print(f"[startup] สรุปรอบวัน: เปิด (ส่ง {hour:02d}:00 UTC = {(hour+7) % 24:02d}:00 ไทย)")
        tasks.append(_daily_summary_loop(notifier, hour))

    # รวม OI สด → oi_history รายวัน ให้ series ยาวขึ้นเอง (ต้องมี DATABASE_URL/Supabase)
    # ปิดด้วย MERGE_OI=false
    if database_url() and os.environ.get("MERGE_OI", "true").lower() != "false":
        moi_hour = int(os.environ.get("MERGE_OI_HOUR_UTC", 23))
        print(f"[startup] รวม OI สดรายวัน: เปิด (ทุก {moi_hour:02d}:00 UTC)")
        tasks.append(_merge_oi_loop(moi_hour))

    await asyncio.gather(*tasks, return_exceptions=True)  # one failing task must not kill others


if __name__ == "__main__":  # pragma: no cover
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[shutdown] stopped by user")

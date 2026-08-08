"""Edge Lab watcher — รัน Edge Lab เป็นรอบเพื่อเก็บผลต่อเนื่อง.

ทำไมต้องรันต่อเนื่อง: edge ไม่คงที่ ตลาดเปลี่ยน regime — funding carry ที่ตอนนี้
"หลับ" อาจตื่นเมื่อกระทิงกลับมา การรันเป็นรอบทำให้เห็น "แนวโน้ม" ไม่ใช่ภาพนิ่ง

⚠️ กับดักที่ระบบนี้กันไว้ให้: **การรันสมมติฐานเดิมซ้ำๆ คือการทดสอบซ้ำในมิติเวลา**
ถ้ารันทุกวัน 365 ครั้ง ยังไงก็มีวันที่ "ผ่าน" ด้วยความบังเอิญ ระบบจึงรายงาน
"ผ่านกี่ครั้งจากกี่ครั้งล่าสุด" เสมอ และเตือนเมื่อเจอ "ผ่านครั้งแรกหลังไม่ผ่านมานาน"
ว่าเป็นสัญญาณที่ต้องสงสัย ไม่ใช่ข่าวดี

    python -m research.lab.watch                 # รันครั้งเดียว (เหมาะกับ scheduler)
    python -m research.lab.watch --loop 168      # วนทุก 168 ชม. (สัปดาห์ละครั้ง)
    python -m research.lab.watch --notify        # ส่ง LINE เมื่อผลเปลี่ยนอย่างมีนัย

ความถี่ที่เหมาะสม: ข้อมูลเป็นรายวัน → **สัปดาห์ละครั้งก็พอ** รันถี่กว่านั้นข้อมูล
ใหม่เพิ่มไม่กี่แท่ง ผลแทบไม่เปลี่ยน แต่เพิ่มโอกาสเจอผลบวกลวง
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Optional

from .evaluate import evaluate, format_evaluation
from .hypotheses import REGISTRY
from .registry import Registry

STABILITY_WINDOW = 5      # ดูผลย้อนหลังกี่ครั้งเมื่อประเมินความน่าเชื่อถือ
DATA_MAX_AGE_H = 12.0     # ข้อมูลเก่ากว่านี้ให้ดึงใหม่


def _notifier():
    from worker.app.config import load_settings
    from worker.app.notifier import ConsoleNotifier, LineNotifier
    s = load_settings()
    if s.line_channel_token and s.line_to:
        return LineNotifier(s.line_channel_token, s.line_to)
    return ConsoleNotifier()


def run_once(reg: Registry, only: Optional[str] = None,
             notify: bool = False, verbose: bool = True) -> list:
    names = [only] if only and only in REGISTRY else list(REGISTRY)
    results = []
    for name in names:
        h = REGISTRY[name]()
        h.max_age_hours = DATA_MAX_AGE_H       # ดึงข้อมูลใหม่ถ้า cache เก่า
        if verbose:
            print(f"\n▶ {name}", flush=True)
        try:
            data = h.load()
            ev = evaluate(h, data, trials_before=reg.trials(), verbose=verbose)
        except SystemExit as e:
            print(f"  ข้าม {name}: {e}")
            continue
        except Exception as e:
            print(f"  ผิดพลาด {name}: {e!r}")
            continue

        prev = reg.stability(name, STABILITY_WINDOW)   # ก่อนบันทึกครั้งนี้
        reg.record(ev)
        after = reg.stability(name, STABILITY_WINDOW)
        results.append((ev, prev, after))
        if verbose:
            print(format_evaluation(ev, h.cost_note))
            print(_stability_line(name, after))

    if notify and results:
        text = build_alert(results)
        if text:
            asyncio.run(_notifier().send(text))
    return results


def _stability_line(name: str, st: dict) -> str:
    return (f"  📈 ความเสถียร: ผ่าน {st['passed_recent']}/{st['runs_recent']} ครั้งล่าสุด "
            f"(ทดสอบสมมติฐานนี้มาแล้ว {st['total_runs']} ครั้ง)")


def build_alert(results: list) -> str:
    """แจ้งเฉพาะสิ่งที่ควรรู้ — ไม่ใช่รายงานทุกครั้งจนกลายเป็น noise."""
    lines = []
    for ev, prev, after in results:
        was_passing = prev["passed_recent"] > 0 and prev["runs_recent"] > 0
        if ev.passed and not was_passing:
            lines.append(
                f"🟡 {ev.hypothesis}: ผ่านเกณฑ์เป็นครั้งแรก (Sharpe {ev.oos.sharpe:.2f})\n"
                f"   ⚠️ ผ่านครั้งเดียวจาก {after['total_runs']} ครั้งที่ทดสอบ — "
                f"ยังน่าสงสัยว่าเป็นความบังเอิญ ต้องเห็นซ้ำหลายรอบก่อนเชื่อ")
        elif ev.passed and after["passed_recent"] >= 3:
            lines.append(
                f"🟢 {ev.hypothesis}: ผ่านต่อเนื่อง {after['passed_recent']}/"
                f"{after['runs_recent']} ครั้งล่าสุด (Sharpe {ev.oos.sharpe:.2f}) — "
                f"เริ่มน่าสนใจจริง")
        elif not ev.passed and prev["passed_recent"] >= 3:
            lines.append(
                f"🔻 {ev.hypothesis}: เคยผ่านต่อเนื่อง แต่รอบนี้ไม่ผ่าน "
                f"(Sharpe {ev.oos.sharpe:.2f}) — edge อาจกำลังเสื่อม")
    if not lines:
        return ""
    return ("🔬 Edge Lab — มีความเปลี่ยนแปลง\n\n" + "\n\n".join(lines) +
            "\n\n⚠️ ผ่านที่นี่ = คุ้มศึกษาต่อ ไม่ใช่เอาไปเทรดได้เลย")


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    loop_hours = None
    notify = "--notify" in argv
    only = None
    if "--loop" in argv:
        i = argv.index("--loop")
        loop_hours = float(argv[i + 1]) if len(argv) > i + 1 else 168.0
    if "--only" in argv:
        i = argv.index("--only")
        only = argv[i + 1] if len(argv) > i + 1 else None

    reg = Registry()
    try:
        while True:
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime())
            print(f"\n{'='*72}\n🔬 Edge Lab — รอบวันที่ {stamp}\n{'='*72}")
            run_once(reg, only=only, notify=notify)
            if loop_hours is None:
                break
            print(f"\n💤 รอบถัดไปอีก {loop_hours:.0f} ชั่วโมง (Ctrl+C เพื่อหยุด)")
            time.sleep(loop_hours * 3600)
    except KeyboardInterrupt:
        print("\nหยุดโดยผู้ใช้")
    finally:
        reg.close()


if __name__ == "__main__":
    main(sys.argv)

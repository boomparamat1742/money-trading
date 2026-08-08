"""Edge Lab CLI.

    python -m research.lab.run list                 # สมมติฐานที่มี
    python -m research.lab.run test xsmom_smallcap  # ทดสอบหนึ่งข้อ
    python -m research.lab.run test all             # ทดสอบทุกข้อ
    python -m research.lab.run history              # ผลที่เคยทดสอบมา
"""
from __future__ import annotations

import sys

from .evaluate import evaluate, format_evaluation
from .hypotheses import REGISTRY
from .registry import Registry


def cmd_list() -> None:
    print("\nสมมติฐานที่ลงทะเบียนไว้:\n")
    for name, cls in REGISTRY.items():
        h = cls()
        kind = "market-neutral" if h.neutral else "directional"
        print(f"  {name:<20} [{kind}]")
        print(f"    {h.question}")
        print(f"    grid: {len(h.param_grid())} ชุดพารามิเตอร์ · ต้นทุน: {h.cost_note}\n")


def cmd_test(which: str, reg: Registry) -> None:
    names = list(REGISTRY) if which == "all" else [which]
    for name in names:
        if name not in REGISTRY:
            print(f"ไม่รู้จักสมมติฐาน '{name}' — ดูรายการด้วย: python -m research.lab.run list")
            continue
        h = REGISTRY[name]()
        print(f"\n▶ ทดสอบ {name} ...")
        try:
            data = h.load()
        except Exception as e:
            print(f"  โหลดข้อมูลไม่สำเร็จ: {e!r}")
            continue
        try:
            ev = evaluate(h, data, trials_before=reg.trials())
        except SystemExit as e:
            print(f"  ข้าม: {e}")
            continue
        print(format_evaluation(ev, h.cost_note))
        reg.record(ev)


def cmd_history(reg: Registry) -> None:
    s = reg.summary()
    print(f"\n📚 Edge Lab registry — ทดสอบมาแล้ว {s['runs']} ครั้ง "
          f"({s['hypotheses']} สมมติฐาน) · ผ่าน {s['passed']}\n")
    print(f"  {'สมมติฐาน':<22}{'OOS Sharpe':>11}{'เกณฑ์':>8}{'fold+':>8}{'ผล':>6}  เมื่อ")
    for r in reg.history():
        mark = "🟢" if r["passed"] else "🔴"
        print(f"  {r['hypothesis']:<22}{r['oos_sharpe']:>11.2f}{r['required_sharpe']:>8.2f}"
              f"{r['folds_positive']}/{r['folds']:<6}{mark:>4}  {r['created_at'][:10]}")
    if s["runs"]:
        print("\n  ⓘ ยิ่งทดสอบหลายสมมติฐาน เกณฑ์ยิ่งสูงขึ้น — กันการเจอของดีด้วยความบังเอิญ")


def main(argv: list[str]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "list":
        cmd_list(); return
    reg = Registry()
    if cmd == "test":
        cmd_test(argv[2] if len(argv) > 2 else "all", reg)
    elif cmd == "history":
        cmd_history(reg)
    else:
        print(__doc__)
    reg.close()


if __name__ == "__main__":
    main(sys.argv)

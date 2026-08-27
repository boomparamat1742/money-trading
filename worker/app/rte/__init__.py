"""Robust Trend Ensemble (RTE) — paper-trading portfolio strategy.

โมดูลนี้ implement สเปก `robust_trend_ensemble_v1_frozen` ที่ผู้ใช้นำเข้ามา:
multi-horizon trend/momentum ensemble + BTC regime + crash filter + top-4
selection + breadth scaling + volatility targeting บน 4h, 8 majors, long-only.

⚠️ ยังไม่ใช่ edge ที่พิสูจน์แล้ว — walk-forward อิสระ (มี funding) ได้ OOS Sharpe
0.93 < bar 1.027 · จุดแข็งจริงคือคุมความเสี่ยง (DD 14% เทียบ BTC 53%) โมดูลนี้มีไว้
"forward paper-test" เก็บหลักฐาน live ข้าม regime — ไม่เคยส่งคำสั่งจริง (paper เท่านั้น)
"""

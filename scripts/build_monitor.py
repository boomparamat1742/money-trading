"""สร้างหน้า Monitor (dashboard สรุปสถานะระบบ) จากข้อมูลจริงใน Supabase

อ่าน trades / market_snapshots / edge_runs → เขียนไฟล์ HTML ที่ฝังข้อมูล ณ ตอนรัน
(snapshot) ไว้เปิดดู/แชร์ได้ รันซ้ำเพื่อรีเฟรช

    python -m scripts.build_monitor [output.html]

ไฟล์เป็น "เนื้อหา body" (style + markup ไม่มี doctype/head) — เปิดในเบราว์เซอร์ได้
และใช้เป็นแหล่งของ Artifact ได้ตรงๆ
"""
from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal

TH = dt.timezone(dt.timedelta(hours=7))


def f(x, nd=2):
    if x is None:
        return "–"
    return f"{float(x):.{nd}f}"


def ts_th(ms):
    if not ms:
        return "–"
    return dt.datetime.fromtimestamp(int(ms) / 1000, TH).strftime("%d/%m %H:%M")


def rr_bar(v, scale=2.0):
    """แถบ RR แบบกระจายรอบ 0 — ขวา=บวก(good) ซ้าย=ลบ(bad)"""
    v = float(v or 0)
    half = min(abs(v) / scale, 1.0) * 50
    if v >= 0:
        seg = f'<span style="left:50%;width:{half:.1f}%;background:linear-gradient(90deg,var(--good),var(--good-hi))"></span>'
    else:
        seg = f'<span style="left:{50-half:.1f}%;width:{half:.1f}%;background:linear-gradient(90deg,var(--bad-hi),var(--bad))"></span>'
    return f'<span class="track"><span class="zero"></span>{seg}</span>'


def svg_spark(vals, w=580, h=120):
    """เส้นทุนสะสม (equity curve) เป็น inline SVG — area + line + จุดปลาย"""
    n = len(vals)
    if n < 2:
        return '<p class="empty">ข้อมูลไม่พอวาดกราฟ</p>'
    mn, mx = min(min(vals), 0.0), max(max(vals), 0.0)
    rng = (mx - mn) or 1.0
    pad = 12
    up = vals[-1] >= 0
    col = "var(--good)" if up else "var(--bad)"

    def X(i):
        return pad + i / (n - 1) * (w - 2 * pad)

    def Y(v):
        return h - pad - (v - mn) / rng * (h - 2 * pad)

    pts = [(X(i), Y(v)) for i, v in enumerate(vals)]
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    y0 = Y(mn)
    area = (f"M{pts[0][0]:.1f},{y0:.1f} L" +
            " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) +
            f" L{pts[-1][0]:.1f},{y0:.1f} Z")
    zy = Y(0.0)
    lx, ly = pts[-1]
    return (
        f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="spark" '
        f'style="width:100%;height:{h}px">'
        f'<line x1="{pad}" x2="{w-pad}" y1="{zy:.1f}" y2="{zy:.1f}" '
        f'style="stroke:var(--border)" stroke-dasharray="2 4"/>'
        f'<path d="{area}" style="fill:{col}" fill-opacity="0.13"/>'
        f'<path d="{line}" fill="none" style="stroke:{col}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="7" style="fill:{col}" fill-opacity="0.22"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.4" style="fill:{col}"/>'
        f'</svg>')


def side_pill(side):
    s = str(side).lower()
    cls = "s-short" if s == "short" else "s-long"
    return f'<span class="pill {cls}">{s.upper()}</span>'


EXIT_TH = {"tp": "ถึงเป้า", "sl_initial": "SL เดิม", "sl_trailing": "trailing",
           "(none)": "ไม่ระบุ", "?": "?", "expired": "หมดเวลา"}
EXIT_CLS = {"tp": "good", "sl_trailing": "warn", "sl_initial": "bad", "(none)": "mut", "?": "mut"}

# แท็กเปิด legend (คำอธิบายศัพท์สั้นๆ ใต้ตาราง สำหรับคนทั่วไป)
LEG = '<p class="mut sm" style="margin:8px 2px 2px;line-height:1.5">'


def fetch():
    from worker.app.store import database_url
    import psycopg
    dsn = database_url()
    if not dsn:
        raise SystemExit("ต้องมี DATABASE_URL (Supabase)")
    d = {}
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT id,symbol,side,filled_entry,stop_loss,take_profit,opened_at,bars_held FROM trades WHERE status='open' ORDER BY opened_at")
        d["open"] = cur.fetchall()
        cur.execute("""SELECT COUNT(*),AVG(actual_rr),SUM(pnl_amount),SUM((actual_rr>0)::int),
                       SUM((actual_rr<=0)::int),SUM(COALESCE(entry_fee,0)+COALESCE(exit_fee,0))
                       FROM trades WHERE status IN('hit_tp','hit_sl','expired') AND actual_rr IS NOT NULL""")
        d["stats"] = cur.fetchone()
        cur.execute("""SELECT side,COUNT(*),AVG(actual_rr),SUM(pnl_amount) FROM trades
                       WHERE status IN('hit_tp','hit_sl','expired') AND actual_rr IS NOT NULL GROUP BY side ORDER BY 3 DESC""")
        d["by_dir"] = cur.fetchall()
        cur.execute("""SELECT COALESCE(exit_reason,'(none)'),COUNT(*),AVG(actual_rr) FROM trades
                       WHERE status IN('hit_tp','hit_sl','expired') AND actual_rr IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""")
        d["by_exit"] = cur.fetchall()
        cur.execute("""SELECT symbol,COUNT(*),AVG(actual_rr),SUM(pnl_amount) FROM trades
                       WHERE status IN('hit_tp','hit_sl','expired') AND actual_rr IS NOT NULL GROUP BY symbol ORDER BY 3 DESC""")
        d["by_sym"] = cur.fetchall()
        cur.execute("""SELECT id,symbol,side,actual_rr,pnl_amount,COALESCE(exit_reason,'?'),closed_at FROM trades
                       WHERE status IN('hit_tp','hit_sl','expired') AND closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 14""")
        d["recent"] = cur.fetchall()
        cur.execute("""SELECT DISTINCT ON(symbol) symbol,ts,price,open_interest_value,funding_rate,mark_price
                       FROM market_snapshots ORDER BY symbol,ts DESC""")
        d["oi"] = cur.fetchall()
        cur.execute("""SELECT DISTINCT ON(hypothesis) hypothesis,oos_sharpe,bench_sharpe,required_sharpe,
                       oos_maxdd,oos_n,passed FROM edge_runs ORDER BY hypothesis,created_at DESC""")
        d["edge"] = cur.fetchall()
        cur.execute("""SELECT pnl_amount FROM trades WHERE status IN('hit_tp','hit_sl','expired')
                       AND pnl_amount IS NOT NULL AND closed_at IS NOT NULL ORDER BY closed_at""")
        eq, run = [], 0.0
        for (p,) in cur.fetchall():
            run += float(p or 0)
            eq.append(run)
        d["equity"] = eq
        cur.execute("SELECT MIN(opened_at),MAX(opened_at),COUNT(*) FROM trades")
        d["span"] = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM trades WHERE status IN('hit_tp','hit_sl','expired') AND entry_context ? 'features'")
        d["feat"] = cur.fetchone()[0]
    return d


def render(d) -> str:
    n, avg, pnl, w, l, fees = d["stats"]
    n = n or 0
    avg = float(avg or 0)
    pnl = float(pnl or 0)
    fees = float(fees or 0)
    gross = pnl + fees
    winrate = (w / n * 100) if n else 0
    lo, hi, total = d["span"]
    pnl_cls = "good" if pnl > 0 else ("bad" if pnl < 0 else "mut")
    gen = dt.datetime.now(TH).strftime("%d/%m/%Y %H:%M")

    # ── KPI tiles ──
    kpis = [
        ("ปิดแล้ว (จบไม้)", str(n), f"จากทั้งหมด {total} · เปิดค้าง {len(d['open'])}"),
        ("Win rate (ชนะกี่ %)", f"{winrate:.1f}%", f"ชนะ {w} · แพ้ {l}"),
        ("Avg RR (กำไรเฉลี่ย/ที่เสี่ยง)", f"{avg:+.3f}R", "ต่อไม้ (ก่อนหักฟีในเงินจริง)"),
        ("Net PnL (กำไรสุทธิ)", f"{pnl:+.3f}", "หลังหักค่าธรรมเนียม"),
    ]
    kpi_html = "".join(
        f'<div class="tile"><div class="lbl">{lbl}</div>'
        f'<div class="big {"num-"+pnl_cls if lbl.startswith("Net") else ""}">{val}</div>'
        f'<div class="sub">{sub}</div></div>'
        for lbl, val, sub in kpis)

    # ── equity curve ──
    eq = d["equity"]
    eq_end = eq[-1] if eq else 0.0
    eq_peak = max(eq) if eq else 0.0
    eq_dd = (eq_peak - eq_end)
    equity_html = svg_spark(eq)
    eq_cls = "good" if eq_end > 0 else ("bad" if eq_end < 0 else "mut")

    # ── fee waterfall ──
    wscale = max(gross, fees, 0.01)
    def wbar(lbl, val, cls):
        wpct = min(abs(val) / wscale, 1.0) * 100
        return (f'<div class="wbar"><span class="wlbl mono">{lbl}</span>'
                f'<span class="wtrack"><span class="wfill" style="width:{wpct:.1f}%;'
                f'background:linear-gradient(90deg,var(--{cls}),var(--{cls}-hi))"></span></span>'
                f'<span class="wval mono num {cls}-ink">{val:+.3f}</span></div>')
    fee_bars = (wbar("Gross", gross, "good") +
                wbar("ค่าฟี", -fees, "bad"))

    # ── open positions ──
    if d["open"]:
        rows = "".join(
            f'<tr><td class="mono mut">#{i}</td><td class="mono">{sym}</td>'
            f'<td>{side_pill(sd)}</td><td class="mono num">{f(ent,6)}</td>'
            f'<td class="mono num bad-ink">{f(sl,6)}</td><td class="mono num good-ink">{f(tp,6)}</td>'
            f'<td class="mono num mut">{bars}</td></tr>'
            for i, sym, sd, ent, sl, tp, _op, bars in d["open"])
        open_html = (f'<table><thead><tr><th>#</th><th>เหรียญ</th><th>ทิศ</th><th>เข้า</th>'
                     f'<th>SL</th><th>TP</th><th>แท่ง</th></tr></thead><tbody>{rows}</tbody></table>'
                     f'{LEG}ทิศ = LONG (เดาขึ้น)/SHORT (เดาลง) · เข้า = ราคาที่เข้า · '
                     f'SL = จุดตัดขาดทุน · TP = จุดทำกำไร · แท่ง = ถือมากี่แท่ง 15 นาที</p>')
    else:
        open_html = '<p class="empty">ไม่มีไม้เปิดค้าง</p>'

    # ── recent closed ──
    rec = "".join(
        f'<tr><td class="mono mut">#{i}</td><td class="mono">{sym}</td><td>{side_pill(sd)}</td>'
        f'<td class="rrcell">{rr_bar(rr)}<span class="mono num rrval {"good-ink" if float(rr or 0)>=0 else "bad-ink"}">{float(rr or 0):+.2f}R</span></td>'
        f'<td><span class="pill p-{EXIT_CLS.get(er,"mut")}">{EXIT_TH.get(er,er)}</span></td>'
        f'<td class="mono mut sm">{ts_th(ca)}</td></tr>'
        for i, sym, sd, rr, _p, er, ca in d["recent"])
    recent_html = (f'<table><thead><tr><th>#</th><th>เหรียญ</th><th>ทิศ</th><th>RR</th>'
                   f'<th>ปิดเพราะ</th><th>เมื่อ</th></tr></thead><tbody>{rec}</tbody></table>'
                   f'{LEG}RR = กำไรเทียบเงินที่เสี่ยง (+2R = ได้ 2 เท่าที่เสี่ยง · −1R = เสียเต็มที่เสี่ยง) · '
                   f'ปิดเพราะ = เหตุผลที่ปิดไม้ (ถึงเป้า/โดน SL/trailing)</p>')

    # ── breakdown bars (by direction / exit / symbol) ──
    def bars(rows, label_i=0, n_i=1, rr_i=2, scale=2.0):
        out = []
        for r in rows:
            lbl, cnt, rr = r[label_i], r[n_i], float(r[rr_i] or 0)
            out.append(
                f'<div class="brow"><span class="blbl mono">{lbl}</span>'
                f'<span class="bn mono mut">{cnt}</span>{rr_bar(rr, scale)}'
                f'<span class="bv mono num {"good-ink" if rr>=0 else "bad-ink"}">{rr:+.2f}</span></div>')
        return "".join(out)

    dir_html = bars(d["by_dir"])
    exit_html = bars(d["by_exit"])
    sym_html = bars(d["by_sym"])

    # ── edge lab ──
    erows = []
    for hyp, sh, bench, req, dd, oos_n, passed in d["edge"]:
        sh = float(sh or 0); req = float(req or 0); bench = float(bench or 0)
        pill = '<span class="pill p-good">ผ่าน</span>' if passed else '<span class="pill p-bad">ไม่ผ่าน</span>'
        beat = "good-ink" if sh > bench else "mut"
        erows.append(
            f'<tr><td class="mono">{hyp}</td>'
            f'<td class="mono num {beat}">{sh:.2f}</td>'
            f'<td class="mono num mut">{bench:.2f}</td>'
            f'<td class="mono num mut">{req:.2f}</td>'
            f'<td class="mono num mut sm">{oos_n or "–"}</td><td>{pill}</td></tr>')
    edge_html = (f'<table><thead><tr><th>สมมติฐาน</th><th>OOS</th><th>bench</th><th>เกณฑ์</th>'
                 f'<th>n</th><th></th></tr></thead><tbody>{"".join(erows)}</tbody></table>'
                 f'{LEG}ตัวเลขคือ Sharpe (ผลตอบแทนเทียบความเสี่ยง — ยิ่งสูงยิ่งดี) · '
                 f'OOS = คะแนนนอกช่วงที่จูน (ของจริง) · bench = ถือเฉยๆ · '
                 f'เกณฑ์ = ต้องเกินเท่านี้ถึงผ่าน · n = จำนวนวันที่ทดสอบ</p>')

    # ── funding / carry / basis (ดูเจ้าใหญ่ + carry monitor) ──
    rows_oi = []
    for sym, tss, price, oiv, fund, mark in d["oi"]:
        fr = float(fund or 0)
        ann = fr * 1095 * 100          # funding รายปี = ทุก 8ชม × 3 × 365
        basis = ((float(mark) / float(price) - 1) * 100) if (mark and price) else 0.0
        rows_oi.append((sym, float(oiv or 0), fr * 100, ann, basis))
    orows = []
    for sym, oiv, fr8, ann, basis in sorted(rows_oi, key=lambda r: -r[3]):
        acls = "good-ink" if ann >= 0 else "bad-ink"
        bcls = "mut" if abs(basis) < 0.05 else ("good-ink" if basis > 0 else "bad-ink")
        carry = "เก็บได้ (short)" if ann > 3 else ("จ่าย" if ann < 0 else "—")
        orows.append(
            f'<tr><td class="mono">{sym}</td>'
            f'<td class="mono num {acls}">{ann:+.1f}%</td>'
            f'<td class="mono num mut sm">{fr8:+.4f}%</td>'
            f'<td class="mono num {bcls} sm">{basis:+.3f}%</td>'
            f'<td class="mono num">${oiv/1e6:,.0f}M</td>'
            f'<td class="mut sm">{carry}</td></tr>')
    oi_html = (
        f'<table><thead><tr><th>เหรียญ</th><th>funding/ปี</th><th>ต่อ8ชม</th>'
        f'<th>basis</th><th>OI</th><th>carry</th></tr></thead><tbody>{"".join(orows)}</tbody></table>'
        f'<p class="mut sm" style="margin:9px 2px 2px;line-height:1.5">'
        f'funding บวก = คนแห่ long → <b>คุณเก็บได้ถ้า short perp (carry)</b> · '
        f'basis = perp ห่าง spot (≈0 ปกติ · ห่างมาก = ตลาดเครียด/arbitrage) · '
        f'⚠️ funding สูง = คนแน่นฝั่งเดียว ระวัง squeeze</p>')

    return TEMPLATE.format(
        gen=gen, span_lo=ts_th(lo), span_hi=ts_th(hi), feat=d["feat"], nclosed=n,
        kpi=kpi_html, net=f"{pnl:+.3f}", net_cls=pnl_cls,
        fee_pct=f"{fees/gross*100:.0f}" if gross else "–",
        equity=equity_html, eq_end=f"{eq_end:+.3f}", eq_cls=eq_cls,
        eq_peak=f"{eq_peak:+.3f}", eq_dd=f"{eq_dd:.3f}", fee_bars=fee_bars,
        open_count=len(d["open"]),
        open=open_html, recent=recent_html, dir=dir_html, exit=exit_html,
        sym=sym_html, edge=edge_html, oi=oi_html)


TEMPLATE = """<meta charset="utf-8">
<style>
:root{{
  --ground:#eaeef3; --surface:#ffffff; --surface-2:#eef1f6; --ink:#151a22;
  --muted:#5d6675; --border:#e0e4ec; --hair:#eef0f5;
  --accent:#9a6a1f; --accent-hi:#bd8630;
  --good:#2b8a66; --good-hi:#3aa87c; --good-ink:#217a58;
  --bad:#bf5139; --bad-hi:#d16a4f; --bad-ink:#ad4530; --warn:#b5842a;
  --shadow:0 1px 2px rgba(20,26,38,.05),0 6px 20px rgba(20,26,38,.05);
  --shadow-hov:0 2px 6px rgba(20,26,38,.08),0 14px 34px rgba(20,26,38,.1);
  --glow:rgba(154,106,31,.10);
}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
  --ground:#090c12; --surface:#12161f; --surface-2:#1b212c; --ink:#e8ebf2;
  --muted:#828c9d; --border:#242c39; --hair:#1a212c;
  --accent:#e0aa4a; --accent-hi:#f2c46e;
  --good:#3fb488; --good-hi:#57c99d; --good-ink:#5cc79b;
  --bad:#e07d63; --bad-hi:#ec9077; --bad-ink:#ec8e73; --warn:#dfb154;
  --shadow:0 1px 2px rgba(0,0,0,.45),0 8px 26px rgba(0,0,0,.4);
  --shadow-hov:0 2px 8px rgba(0,0,0,.55),0 18px 44px rgba(0,0,0,.55);
  --glow:rgba(224,170,74,.11);
}}}}
:root[data-theme="dark"]{{
  --ground:#090c12; --surface:#12161f; --surface-2:#1b212c; --ink:#e8ebf2;
  --muted:#828c9d; --border:#242c39; --hair:#1a212c;
  --accent:#e0aa4a; --accent-hi:#f2c46e;
  --good:#3fb488; --good-hi:#57c99d; --good-ink:#5cc79b;
  --bad:#e07d63; --bad-hi:#ec9077; --bad-ink:#ec8e73; --warn:#dfb154;
  --shadow:0 1px 2px rgba(0,0,0,.45),0 8px 26px rgba(0,0,0,.4);
  --shadow-hov:0 2px 8px rgba(0,0,0,.55),0 18px 44px rgba(0,0,0,.55);
  --glow:rgba(224,170,74,.11);
}}
*{{box-sizing:border-box}}
.mono{{font-family:ui-monospace,"Cascadia Code","SF Mono",Menlo,Consolas,monospace}}
body{{margin:0;color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased;
  background:radial-gradient(900px 460px at 88% -14%,var(--glow),transparent 62%),var(--ground)}}
.wrap{{max-width:1140px;margin:0 auto;padding:0 20px 64px}}
.num{{font-variant-numeric:tabular-nums}}
.mut{{color:var(--muted)}} .good-ink{{color:var(--good-ink)}} .bad-ink{{color:var(--bad-ink)}}
.sm{{font-size:12px}}
.eyebrow{{font-family:ui-monospace,monospace;font-size:10.5px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--muted)}}

/* top accent rule + masthead */
.rule{{height:3px;border-radius:0 0 3px 3px;margin-bottom:24px;
  background:linear-gradient(90deg,var(--accent),var(--accent-hi),transparent 70%)}}
.top{{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:16px;margin-bottom:20px}}
.brand h1{{font-family:ui-monospace,monospace;font-size:25px;letter-spacing:.2em;margin:0;font-weight:700}}
.brand h1 .dot{{color:var(--accent)}}
.brand .subtitle{{font-family:ui-monospace,monospace;font-size:10.5px;letter-spacing:.22em;
  text-transform:uppercase;color:var(--muted);margin-top:6px}}
.meta{{text-align:right;font-size:12px;color:var(--muted)}}
.meta .pills{{display:flex;gap:6px;justify-content:flex-end;margin-bottom:7px}}

/* honesty banner */
.warnbar{{display:flex;gap:11px;align-items:flex-start;
  background:color-mix(in srgb,var(--warn) 11%,var(--surface));
  border:1px solid color-mix(in srgb,var(--warn) 34%,var(--border));border-left:3px solid var(--warn);
  border-radius:10px;padding:12px 15px;margin-bottom:22px;font-size:13px;box-shadow:var(--shadow)}}
.warnbar b{{color:var(--warn)}}

/* cards */
.card{{background:var(--surface);border:1px solid var(--border);border-radius:14px;
  padding:17px 18px;box-shadow:var(--shadow);min-width:0;
  transition:transform .16s ease,box-shadow .16s ease,border-color .16s ease}}
.card:hover{{transform:translateY(-2px);box-shadow:var(--shadow-hov);
  border-color:color-mix(in srgb,var(--accent) 32%,var(--border))}}
.card h2{{font-family:ui-monospace,monospace;font-size:11px;letter-spacing:.13em;text-transform:uppercase;
  color:var(--muted);margin:0 0 13px;font-weight:600;display:flex;align-items:center;gap:8px}}
.card h2::before{{content:"";width:7px;height:7px;border-radius:2px;background:var(--accent);flex:none}}

/* kpi */
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:16px}}
.tile{{background:linear-gradient(180deg,var(--surface),color-mix(in srgb,var(--surface-2) 45%,var(--surface)));
  border:1px solid var(--border);border-radius:14px;padding:15px 17px;box-shadow:var(--shadow);
  position:relative;overflow:hidden}}
.tile::after{{content:"";position:absolute;left:0;right:0;top:0;height:2px;
  background:linear-gradient(90deg,var(--accent),transparent 65%);opacity:.5}}
.tile .lbl{{font-family:ui-monospace,monospace;font-size:10.5px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--muted);margin-bottom:9px}}
.tile .big{{font-family:ui-monospace,monospace;font-size:29px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1}}
.tile .sub{{font-size:11.5px;color:var(--muted);margin-top:8px}}
.num-good{{color:var(--good-ink)}} .num-bad{{color:var(--bad-ink)}} .num-mut{{color:var(--muted)}}

/* hero: equity + fee */
.hero{{display:grid;grid-template-columns:1.55fr 1fr;gap:16px;margin-bottom:16px}}
.eqhead{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:6px}}
.eqbig{{font-family:ui-monospace,monospace;font-size:28px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.1;margin-top:3px}}
.eqmeta{{text-align:right;font-size:11px;color:var(--muted);line-height:1.7}}
.spark{{display:block;margin-top:4px;overflow:visible}}
.wbar{{display:grid;grid-template-columns:44px 1fr 62px;align-items:center;gap:10px;padding:5px 0}}
.wlbl{{font-size:12px;color:var(--muted)}}
.wtrack{{height:15px;background:var(--surface-2);border-radius:5px;position:relative;overflow:hidden}}
.wfill{{position:absolute;left:0;top:0;height:100%;border-radius:5px}}
.wval{{font-size:13px;text-align:right;font-weight:600}}
.feenet{{margin-top:11px;padding-top:11px;border-top:1px solid var(--border);font-size:12.5px;color:var(--muted)}}
.feenet b{{font-size:15px}}

/* grid of cards */
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.card.span2{{grid-column:1/-1}}
.tblwrap{{overflow-x:auto;margin:0 -6px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{font-family:ui-monospace,monospace;font-size:10px;letter-spacing:.07em;text-transform:uppercase;
  color:var(--muted);text-align:left;font-weight:600;padding:0 9px 9px;border-bottom:1px solid var(--border);white-space:nowrap}}
td{{padding:8px 9px;border-bottom:1px solid var(--hair);white-space:nowrap}}
tbody tr:last-child td{{border-bottom:none}}
tbody tr{{transition:background .12s ease}}
tbody tr:hover{{background:color-mix(in srgb,var(--accent) 5%,transparent)}}
td.num,th{{text-align:left}}
.empty{{color:var(--muted);font-style:italic;padding:10px 4px 18px}}

/* pills */
.pill{{display:inline-block;font-family:ui-monospace,monospace;font-size:10.5px;font-weight:600;
  letter-spacing:.03em;padding:2px 8px;border-radius:20px}}
.s-short{{color:var(--bad-ink);background:color-mix(in srgb,var(--bad) 15%,transparent)}}
.s-long{{color:var(--good-ink);background:color-mix(in srgb,var(--good) 15%,transparent)}}
.p-good{{color:var(--good-ink);background:color-mix(in srgb,var(--good) 15%,transparent)}}
.p-bad{{color:var(--bad-ink);background:color-mix(in srgb,var(--bad) 15%,transparent)}}
.p-warn{{color:var(--warn);background:color-mix(in srgb,var(--warn) 16%,transparent)}}
.p-mut{{color:var(--muted);background:color-mix(in srgb,var(--muted) 14%,transparent)}}

/* rr track */
.track{{position:relative;display:inline-block;width:100%;min-width:70px;height:10px;
  background:var(--surface-2);border-radius:5px;vertical-align:middle;overflow:hidden}}
.track .zero{{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--border);z-index:1}}
.track>span:not(.zero){{position:absolute;top:0;height:10px;border-radius:5px}}
.rrcell{{display:flex;align-items:center;gap:10px;min-width:150px}}
.rrval{{font-size:12px;min-width:52px;text-align:right}}

/* breakdown rows */
.brow{{display:grid;grid-template-columns:92px 28px 1fr 50px;align-items:center;gap:10px;padding:5px 0}}
.brow .blbl{{font-size:12.5px;overflow:hidden;text-overflow:ellipsis}}
.brow .bn{{font-size:11px;text-align:right}}
.brow .bv{{font-size:12px;text-align:right;font-variant-numeric:tabular-nums}}
.brow .track{{min-width:60px}}

.foot{{margin-top:28px;padding-top:15px;border-top:1px solid var(--border);
  font-size:11.5px;color:var(--muted);display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}}

@media (max-width:760px){{
  .kpis{{grid-template-columns:repeat(2,1fr)}}
  .hero,.grid{{grid-template-columns:1fr}}
}}
@media (prefers-reduced-motion:reduce){{.card{{transition:none}}.card:hover{{transform:none}}}}
</style>

<div class="wrap">
  <div class="rule"></div>
  <div class="top">
    <div class="brand">
      <h1>JARVIS<span class="dot">.</span></h1>
      <div class="subtitle">Paper Trading · Market Monitor</div>
    </div>
    <div class="meta">
      <div class="pills">
        <span class="pill p-warn">PAPER</span>
        <span class="pill p-mut">Discord</span>
      </div>
      snapshot · {gen} น.
    </div>
  </div>

  <div class="warnbar">
    <span>⚠️</span>
    <span><b>ยังไม่พิสูจน์ว่ามี edge</b> (ความได้เปรียบที่ทำกำไรได้จริง) — ตัวเลขทั้งหมดเป็น
    paper trading (จำลอง ไม่ใช่เงินจริง) บนข้อมูล {span_lo} → {span_hi}
    (สัปดาห์เดียว / regime เดียว = สภาพตลาดแบบเดียว) ยังสรุปไม่ได้ · ไม่ใช่คำแนะนำการลงทุน</span>
  </div>

  <div class="kpis">{kpi}</div>

  <div class="hero">
    <div class="card">
      <div class="eqhead">
        <div>
          <div class="eyebrow">Equity curve — เส้นทุนสะสม (paper = จำลอง ไม่ใช่เงินจริง)</div>
          <div class="eqbig num-{eq_cls}">{eq_end}</div>
        </div>
        <div class="eqmeta mono">peak (สูงสุด) {eq_peak}<br>drawdown (หดจากพีค) {eq_dd}</div>
      </div>
      {equity}
    </div>
    <div class="card">
      <h2>Net (กำไรสุทธิ) หลังหักค่าธรรมเนียม</h2>
      {fee_bars}
      <div class="feenet">Net (สุทธิ) = <b class="num-{net_cls}">{net}</b> · ค่าฟีกิน ~{fee_pct}% ของ
      gross (กำไรก่อนหักฟี) — เทรดถี่เกินไป ค่าธรรมเนียมคือตัวชี้ขาด ไม่ใช่กลยุทธ์</div>
    </div>
  </div>

  <div class="grid">
    <div class="card"><h2>ไม้เปิดค้าง ({open_count})</h2><div class="tblwrap">{open}</div></div>
    <div class="card"><h2>ปิดล่าสุด</h2><div class="tblwrap">{recent}</div></div>

    <div class="card"><h2>แยกทิศทาง · avg RR (กำไรเฉลี่ย/ที่เสี่ยง)</h2>{dir}</div>
    <div class="card"><h2>แยกสาเหตุปิด · avg RR</h2>{exit}</div>

    <div class="card"><h2>แยกเหรียญ · avg RR</h2>{sym}</div>
    <div class="card"><h2>Funding · Carry · Basis (ดูเจ้าใหญ่)</h2><div class="tblwrap">{oi}</div></div>

    <div class="card span2"><h2>Edge Lab (ห้องแล็บทดสอบว่ากลยุทธ์มี edge จริงไหม) — สถานะสมมติฐาน</h2><div class="tblwrap">{edge}</div></div>
  </div>

  <div class="foot">
    <span>ปิดแล้ว {nclosed} ไม้ · เก็บ feature แล้ว {feat} ไม้ (รอครบหลายร้อย + ข้าม regime ค่อยวิเคราะห์)</span>
    <span>snapshot — รัน <span class="mono">python -m scripts.build_monitor</span> เพื่อรีเฟรช</span>
  </div>
</div>
"""


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    out = sys.argv[1] if len(sys.argv) > 1 else "monitor.html"
    html = render(fetch())
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"✅ เขียน {out} ({len(html):,} ตัวอักษร)")


if __name__ == "__main__":
    main()

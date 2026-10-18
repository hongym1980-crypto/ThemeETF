"""ETF 매수 신호 이후 주가 흐름 — 자체완결형 HTML 대시보드.

`analyze_post_event.py` 가 만든 signals.csv / events.csv / prices/*.csv 를 읽어
`dashboard.html` 하나로 심는다. 비교 계산(기간 필터, 이전 기간 대비, 구분별 집계)은
모두 브라우저에서 하므로 파일 하나로 오프라인에서도 열린다.

    python price_data/build_dashboard.py            # dashboard.html 생성
    python price_data/build_dashboard.py --open     # 생성 후 브라우저로 열기

디자인 규칙
- 미래에셋 디자인 시스템: 흰 캔버스, 1px 오렌지 섹션 룰, FAB072 표 헤더, sharp 모서리,
  Noto Sans KR / Inter. 그라데이션·이모지·그림자 없음.
- 색 의미(사용자 규칙): 긍정 = 청색 #0086B8, 경고 = 오렌지 #F58220, 위험 = 적색 #C62828.
  수익률 발산형(청/적)은 dataviz 검증 통과(CVD ΔE 20.7). 녹색 #2E8540 은 상태 점+라벨로만.
- 오렌지는 브랜드 구조(섹션 룰·활성 필터·T0 마커)와 '경고' 상태에만 쓰고 데이터 시리즈로는
  쓰지 않는다. 텍스트는 잉크 토큰만 입고, 색은 옆의 마크가 진다.
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from datetime import datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
PRICES = HERE / "prices"
OUT = HERE / "dashboard.html"
PRICE_FROM = "20260701"   # 드릴다운 차트가 T-15 영업일까지 보이도록 여유


# ------------------------------------------------------------------ 데이터

def _num(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return round(float(v), 2)


def build_payload() -> dict:
    sig = pd.read_csv(HERE / "signals.csv", dtype={"std_dt": str, "stock_code": str})
    ev = pd.read_csv(HERE / "events.csv", dtype={"std_dt": str, "stock_code": str,
                                                 "etf_code": str})
    unc_path = HERE / "events_uncertain_new.csv"
    n_unc = len(pd.read_csv(unc_path)) if unc_path.exists() else 0

    ret_cols = ["pre5", "r1", "x1", "r3", "x3", "r5", "x5", "r10", "x10", "r1to5", "r1to10"]
    signals = []
    for r in sig.itertuples(index=False):
        d = r._asdict()
        row = {
            "dt": d["std_dt"], "code": d["stock_code"], "name": d["stock_name"],
            "kind": d["kind"], "n": int(d["n_etfs"]), "nNew": int(d["n_new"]),
            "nAdd": int(d["n_add"]),
            "themes": [x.strip() for x in str(d["themes"]).split("|") if x.strip()],
            "flow": _num(d["flow_bil"]), "close0": _num(d["close_t0"]),
        }
        for c in ret_cols:
            row[c] = _num(d.get(c))
        signals.append(row)

    events: dict[str, list] = {}
    for r in ev.itertuples(index=False):
        d = r._asdict()
        key = "{}|{}".format(d["std_dt"], d["stock_code"])
        events.setdefault(key, []).append({
            "etf": d["etf_name"], "etfCode": d["etf_code"], "theme": d["theme"],
            "kind": d["kind"], "w": _num(d["weight"]), "pw": _num(d["prev_weight"]),
            "dw": _num(d["d_weight"]), "dq": _num(d["d_qty_pct"]),
            "flow": _num(d["d_value_bil"]),
        })

    def load_px(symbol: str):
        p = PRICES / "{}.csv".format(symbol)
        if not p.exists():
            return None
        df = pd.read_csv(p, dtype={"date": str})
        df = df[df["date"] >= PRICE_FROM]
        return {"d": df["date"].tolist(), "c": [float(x) for x in df["close"]]}

    prices = {}
    for code in sorted({s["code"] for s in signals}):
        px = load_px(code)
        if px:
            prices[code] = px
    kospi = load_px("KOSPI") or {"d": [], "c": []}

    dates = sorted({s["dt"] for s in signals})
    k = dict(zip(kospi["d"], kospi["c"]))
    k_ret = None
    if dates and dates[0] in k and dates[-1] in k:
        k_ret = round((k[dates[-1]] / k[dates[0]] - 1) * 100, 2)

    return {
        "signals": signals,
        "events": events,
        "prices": prices,
        "kospi": kospi,
        "dates": dates,
        "meta": {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "priceEnd": kospi["d"][-1] if kospi["d"] else "-",
            "nUncertain": n_unc,
            "kospiRet": k_ret,
            "nStocks": len({s["code"] for s in signals}),
        },
    }


# ------------------------------------------------------------------ 템플릿

_CSS = r"""
:root{
  --orange:#F58220; --orange-active:#CB6015; --orange-soft:#FAB072; --navy:#043B72;
  --canvas:#FFFFFF; --soft:#ECEFF4; --subtle:#F7F8FA; --hair:#CDCECB; --hair-soft:#E5E4E1;
  --ink:#1A1A1A; --body:#3D3D3D; --muted:#6C6C6C; --muted-soft:#84888B;
  --line-dark:#49535B; --line-mid:#84888B; --line-soft:#A0A6A8;
  --pos:#0086B8; --neg:#C62828; --warn:#F58220; --good:#2E8540; --neutral:#84888B;
  --pos-soft:#D6EBF4; --neg-soft:#F6DADA; --seq:#0086B8; --seq-track:#D6EBF4;
  --font-kr:'Noto Sans KR','Spoqa Han Sans Neo','Apple SD Gothic Neo','Malgun Gothic',sans-serif;
  --font-en:'Inter','Aptos','Segoe UI',system-ui,sans-serif;
  --r-xs:2px; --r-sm:4px;
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--canvas)}
body{color:var(--body);font:16px/1.55 var(--font-kr);-webkit-font-smoothing:antialiased}
html[lang=en] body{font-family:var(--font-en)}
h1,h2,h3{margin:0;color:var(--ink);letter-spacing:-.2px}
.page{max-width:1280px;margin:0 auto;padding:0 32px 72px}
@media (max-width:768px){.page{padding:0 16px 56px}}
.num{font-family:var(--font-en);font-variant-numeric:tabular-nums}
button,select,input{font:inherit;color:var(--ink)}
button{cursor:pointer}
button:focus-visible,select:focus-visible,input:focus-visible,[tabindex]:focus-visible{
  outline:2px solid var(--orange);outline-offset:1px}

/* 헤더 */
.masthead{display:flex;align-items:flex-start;gap:16px;padding:28px 0 18px;
  border-bottom:1px solid var(--orange)}
.masthead .tag{font-size:12px;letter-spacing:.6px;color:var(--muted);text-transform:uppercase;
  font-family:var(--font-en);margin-bottom:6px}
.masthead h1{font-size:30px;font-weight:700;line-height:1.2}
.masthead .sub{margin-top:6px;font-size:14px;color:var(--muted)}
.masthead .sub b{color:var(--ink);font-weight:600}
.masthead .fresh{margin-left:auto;display:flex;flex-direction:column;align-items:flex-end;gap:10px}
.lang{display:inline-flex;border:1px solid var(--hair);border-radius:var(--r-xs);
  box-shadow:0 2px 8px rgba(0,0,0,.06);overflow:hidden}
.lang button{border:0;background:#fff;color:var(--muted);padding:9px 17px;font:500 14px/1 var(--font-en);
  letter-spacing:.5px}
.lang button+button{border-left:1px solid var(--hair)}
.lang button[aria-checked=true]{background:var(--orange);color:#fff}
.lang button:not([aria-checked=true]):hover{background:var(--subtle);color:var(--ink)}
.status{display:inline-flex;align-items:center;gap:8px;font-size:13px;color:var(--ink);
  border:1px solid var(--hair);border-radius:var(--r-xs);padding:6px 12px;white-space:nowrap}
.status::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--good)}
.status.stale::before{background:var(--warn)}
@media (max-width:768px){.masthead{flex-wrap:wrap}.masthead .fresh{margin-left:0;align-items:flex-start;flex-direction:row;flex-wrap:wrap}}

/* 필터 행 — 아래 모든 것을 스코프 */
.filters{display:flex;flex-wrap:wrap;gap:14px 22px;align-items:flex-end;padding:18px 0 16px;
  border-bottom:1px solid var(--hair-soft)}
.field{display:flex;flex-direction:column;gap:6px}
.field>label{font-size:12px;color:var(--muted);letter-spacing:.2px}
.seg{display:inline-flex;border:1px solid var(--hair);border-radius:var(--r-xs);overflow-x:auto;max-width:100%}
.seg button{white-space:nowrap}
.seg button{border:0;background:#fff;color:var(--body);padding:8px 13px;font-size:14px;line-height:1}
.seg button+button{border-left:1px solid var(--hair)}
.seg button.on{background:var(--orange);color:#fff;font-weight:600}
.seg button:not(.on):hover{background:var(--subtle);color:var(--ink)}
select,input[type=search]{background:#fff;border:1px solid var(--hair);border-radius:var(--r-xs);
  padding:7px 10px;font-size:14px;height:34px}
select:hover,input:hover{border-color:var(--line-mid)}
.range{display:flex;align-items:center;gap:6px}
.range .to{color:var(--muted)}
input[type=search]{min-width:200px}
.filters .note{flex-basis:100%;font-size:13px;color:var(--muted)}
.filters .note b{color:var(--ink);font-weight:600}
.chip-x{border:1px solid var(--hair);background:#fff;border-radius:var(--r-xs);padding:5px 10px;font-size:13px;color:var(--body)}
.chip-x:hover{border-color:var(--orange);color:var(--ink)}

/* 섹션 */
.section{margin-top:44px}
.rule{height:1px;background:var(--orange);margin-bottom:14px}
.section-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.section-head h2{font-size:21px;font-weight:700}
.section-head .hint{font-size:13px;color:var(--muted)}
.section-head .right{margin-left:auto;font-size:13px;color:var(--muted)}

/* 이상 징후 */
.alerts{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-top:20px}
.alert{border:1px solid var(--hair);border-left:4px solid var(--neutral);border-radius:var(--r-sm);
  padding:14px 16px;background:#fff;display:flex;flex-direction:column;gap:6px}
.alert.danger{border-left-color:var(--neg)}
.alert.warn{border-left-color:var(--warn)}
.alert.good{border-left-color:var(--good)}
.alert.info{border-left-color:var(--pos)}
.alert .lvl{display:inline-flex;align-items:center;gap:7px;font-size:12px;letter-spacing:.4px;
  color:var(--muted);text-transform:uppercase;font-family:var(--font-en);font-weight:600}
.alert .lvl::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--neutral)}
.alert.danger .lvl::before{background:var(--neg)}
.alert.warn .lvl::before{background:var(--warn)}
.alert.good .lvl::before{background:var(--good)}
.alert.info .lvl::before{background:var(--pos)}
.alert .msg{font-size:15px;color:var(--ink);line-height:1.5}
.alert .msg b{font-weight:700}
.alert .act{font-size:13px;color:var(--muted)}
.alert .act button{border:0;background:none;padding:0;color:var(--navy);font-weight:600;text-decoration:underline;text-underline-offset:3px}

/* KPI */
.kpis{display:grid;grid-template-columns:2fr repeat(4,1fr);gap:12px;margin-top:20px}
@media (max-width:1100px){.kpis{grid-template-columns:repeat(3,1fr)}.kpi.hero{grid-column:span 3}}
@media (max-width:640px){.kpis{grid-template-columns:1fr 1fr}.kpi.hero{grid-column:span 2}}
.kpi{border:1px solid var(--hair);border-radius:var(--r-sm);padding:16px 18px;background:#fff;
  display:flex;flex-direction:column;gap:6px;min-width:0}
.kpi .k{font-size:13px;color:var(--muted);letter-spacing:.2px}
.kpi .v{font-size:30px;font-weight:700;color:var(--ink);line-height:1.05;font-family:var(--font-en)}
.kpi.hero .v{font-size:56px;color:var(--navy)}
.kpi .v small{font-size:14px;font-weight:500;color:var(--muted);margin-left:4px;font-family:var(--font-kr)}
html[lang=en] .kpi .v small{font-family:var(--font-en)}
.kpi .d{font-size:13px;color:var(--muted);display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center}
.kpi .d .delta{font-weight:600;font-family:var(--font-en);font-variant-numeric:tabular-nums}
.delta.up{color:var(--pos)}.delta.down{color:var(--neg)}.delta.flat{color:var(--muted)}
.delta.bad{color:var(--neg)}.delta.okay{color:var(--pos)}
.kpi .sub{font-size:13px;color:var(--body)}
.meter{position:relative;height:8px;background:var(--soft);border-radius:var(--r-xs);overflow:hidden;margin-top:4px}
.meter i{position:absolute;left:0;top:0;height:100%;background:var(--pos)}
.meter i.warn{background:var(--warn)}.meter i.danger{background:var(--neg)}
.meter::after{content:"";position:absolute;left:50%;top:-2px;bottom:-2px;width:2px;background:var(--line-dark)}
.meter-note{font-size:12px;color:var(--muted);display:flex;justify-content:space-between}

/* 그리드 카드 */
.grid2{display:grid;grid-template-columns:3fr 2fr;gap:16px}
@media (max-width:1000px){.grid2{grid-template-columns:1fr}}
.card{border:1px solid var(--hair);border-radius:var(--r-sm);background:#fff;padding:16px 18px 14px;min-width:0}
.card>header{display:flex;align-items:baseline;gap:10px;margin-bottom:10px;flex-wrap:wrap}
.card h3{font-size:16px;font-weight:700}
.card .hint{font-size:13px;color:var(--muted)}
.card .right{margin-left:auto;font-size:12.5px;color:var(--muted)}
.plot{width:100%;overflow:hidden}
svg{display:block;max-width:100%}
.axis{stroke:var(--line-soft);stroke-width:1;shape-rendering:crispEdges}
.grid{stroke:var(--hair-soft);stroke-width:1;shape-rendering:crispEdges}
.tick{fill:var(--muted);font-size:11.5px;font-family:var(--font-en);font-variant-numeric:tabular-nums}
.cat{fill:var(--body);font-size:13px}
.val{fill:var(--body);font-size:12px;font-family:var(--font-en);font-variant-numeric:tabular-nums}
.hit{fill:transparent;cursor:pointer}
.hit:hover+.bar,.bar.hov{opacity:.75}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--body);margin:2px 0 8px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:10px;height:10px;display:inline-block}
.legend i.line{width:16px;height:2px}
.legend i.tri{width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-bottom:9px solid var(--ink)}
.empty{color:var(--muted);font-size:14px;padding:22px 0;text-align:center}

/* 표 */
.tbl-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:8px 10px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--hair-soft)}
th{background:var(--orange-soft);color:var(--ink);font-weight:700;font-size:13px;text-align:right;
  position:sticky;top:0;z-index:1;user-select:none}
th.t,td.t{text-align:left}
td{font-variant-numeric:tabular-nums;font-family:var(--font-en);color:var(--body)}
td.t{font-family:inherit}
td.k{font-family:var(--font-kr)}
html[lang=en] td.k{font-family:var(--font-en)}
th.sortable{cursor:pointer}
th.sortable:hover{background:#f7a35a}
th .arr{font-size:10px;color:var(--ink);margin-left:3px}
tbody tr:hover{background:var(--subtle)}
tbody tr.clickable{cursor:pointer}
tbody tr.sel{background:var(--soft)}
tbody tr.total td{background:var(--soft);font-weight:700;color:var(--ink)}
td .name{color:var(--ink);font-weight:600}
td .code{color:var(--muted);font-size:12px;margin-left:6px;font-family:var(--font-en)}
td.hl,th.hl{background:var(--subtle)}
th.hl{background:#f7a35a}
.chip{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border:1px solid var(--hair);
  border-radius:var(--r-xs);font-size:12px;color:var(--ink);font-family:var(--font-kr);font-weight:600}
html[lang=en] .chip{font-family:var(--font-en)}
.chip::before{content:"";width:7px;height:7px;background:var(--neutral)}
.chip.NEW::before{background:var(--navy)}
.chip.ADD::before{background:var(--pos)}
.mini{display:inline-block;position:relative;width:64px;height:8px;background:var(--soft);vertical-align:middle;margin-right:8px}
.mini::after{content:"";position:absolute;left:50%;top:-1px;bottom:-1px;width:1px;background:var(--line-mid)}
.mini i{position:absolute;top:0;height:100%}
.mini i.p{left:50%;background:var(--pos)}
.mini i.n{right:50%;background:var(--neg)}
.wmeter{display:inline-block;position:relative;width:72px;height:8px;background:var(--soft);vertical-align:middle;margin-right:8px}
.wmeter::after{content:"";position:absolute;left:50%;top:-2px;bottom:-2px;width:2px;background:var(--line-dark)}
.wmeter i{position:absolute;left:0;top:0;height:100%;background:var(--pos)}
.wmeter i.warn{background:var(--warn)}.wmeter i.danger{background:var(--neg)}
.more{margin-top:10px;text-align:center}
.more button{border:1px solid var(--hair);background:#fff;padding:7px 16px;border-radius:var(--r-xs);font-size:13px}
.tabs{display:inline-flex;border:1px solid var(--hair);border-radius:var(--r-xs);overflow:hidden;margin-bottom:12px}
.tabs button{border:0;background:#fff;padding:7px 13px;font-size:13px;color:var(--body)}
.tabs button+button{border-left:1px solid var(--hair)}
.tabs button.on{background:var(--navy);color:#fff;font-weight:600}
.pos-t{color:var(--pos)}.neg-t{color:var(--neg)}

/* 드릴다운 */
.drill{margin-top:16px;border:1px solid var(--hair);border-radius:var(--r-sm);background:#fff;padding:18px}
.drill .head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.drill .head h3{font-size:19px}
.drill .head .code{color:var(--muted);font-family:var(--font-en)}
.drill .head .close{margin-left:auto;border:1px solid var(--hair);background:#fff;padding:5px 11px;border-radius:var(--r-xs);font-size:13px}
.drill .meta{font-size:13.5px;color:var(--muted);margin-bottom:12px;display:flex;gap:14px;flex-wrap:wrap}
.drill .meta b{color:var(--ink);font-weight:600}
.drill-grid{display:grid;grid-template-columns:3fr 2fr;gap:18px}
@media (max-width:1000px){.drill-grid{grid-template-columns:1fr}}
.drill h4{margin:0 0 8px;font-size:14px;color:var(--ink);font-weight:700}
.hist{margin-top:14px}
.crosshair{stroke:var(--line-dark);stroke-width:1;shape-rendering:crispEdges}

/* 툴팁 */
#tip{position:fixed;z-index:50;pointer-events:none;opacity:0;transition:opacity .08s;
  background:var(--ink);color:#fff;border-radius:var(--r-xs);padding:8px 11px;font-size:13px;line-height:1.45;max-width:320px}
#tip b{font-weight:700;font-family:var(--font-en);font-variant-numeric:tabular-nums}
#tip .row{display:flex;gap:10px;justify-content:space-between}
#tip .row i{display:inline-block;width:12px;height:2px;vertical-align:middle;margin-right:6px}
#tip .dim{color:#c9c9c9}

footer{margin-top:56px;padding-top:16px;border-top:1px solid var(--hair-soft);color:var(--muted);font-size:13px;line-height:1.6}
footer h4{margin:0 0 6px;color:var(--ink);font-size:14px}
footer ul{margin:0;padding-left:18px}
footer .cols{display:grid;grid-template-columns:1fr 1fr;gap:24px}
@media (max-width:768px){footer .cols{grid-template-columns:1fr}}
.swatch{display:inline-block;width:10px;height:10px;vertical-align:-1px;margin-right:5px}

@media print{
  .lang,.filters .chip-x,.drill .close{display:none!important}
  .page{max-width:100%;padding:0}
  .card,.kpi,.alert,.drill{page-break-inside:avoid;border-color:#bbb}
  th{position:static}
}
"""

_I18N = {
    "ko": {
        "tag": "ThemeETF · 사후 성과 분석",
        "title": "ETF 매수 신호 이후 주가 흐름",
        "sub": "테마 ETF 구성종목(PDF)에서 감지한 <b>신규편입(NEW)</b>·<b>비중확대(ADD)</b> 신호 이후 종목 수익률. 기준일(T0) 종가 → T+k 영업일 종가, KOSPI 대비 초과수익 병기.",
        "fresh_ok": "시세 최신", "fresh_stale": "시세 갱신 필요",
        "price_end": "시세 마지막", "signal_range": "신호 기준일", "generated": "생성",
        "f_period": "신호 기준일 구간", "p5": "최근 5영업일", "p10": "최근 10영업일", "p20": "최근 20영업일", "pall": "전체", "pcustom": "직접 지정",
        "f_h": "성과 지표", "f_kind": "신호 종류", "k_all": "전체", "k_new": "신규편입", "k_add": "비중확대",
        "f_theme": "테마", "t_all": "모든 테마", "f_q": "종목 검색", "q_ph": "종목명 또는 코드",
        "reset": "필터 초기화",
        "note": "선택 구간 <b>{n}</b>건 신호 · 종목 <b>{s}</b>개 · 기준일 <b>{d}</b>일 · 이전 비교구간 {prev} · 같은 기간 KOSPI <b>{k}</b>",
        "note_noprev": "(이전 구간 없음)",
        "sec_alert": "지금 볼 것", "sec_alert_hint": "선택 구간 신호에서 자동 추출한 이상 징후와 참고 포인트",
        "sec_kpi": "핵심 지표", "sec_kpi_hint": "▲▼ 는 같은 길이의 직전 구간 대비 변화",
        "sec_charts": "일자별 · 테마별 성과", "sec_seg": "어떤 신호가 통했나", "sec_seg_hint": "구분별 T+{k} 성과 · 승률 막대의 세로선 = 50%",
        "sec_table": "신호 목록", "sec_table_hint": "행을 클릭하면 종목 주가 흐름과 매수 ETF 내역으로 드릴다운",
        "kpi_mean": "T+{k} 평균 수익률", "kpi_mean_sub": "확정 {nk}/{n}건 · KOSPI 대비 {x}",
        "kpi_win": "T+{k} 승률", "kpi_win_sub": "기준 50%", "kpi_n": "신호 수", "kpi_n_sub": "NEW {a} · ADD {b}",
        "kpi_chase": "추격매수 비율", "kpi_chase_sub": "직전 5일 +5% 이상 오른 뒤 매수", "kpi_multi": "동시매수 신호", "kpi_multi_sub": "ETF 2개 이상 · 평균 {m}",
        "kpi_pend": "T+{k} 미확정", "kpi_pend_sub": "시세 갱신 후 재실행하면 채워짐",
        "vs_prev": "직전 구간 대비", "pp": "pp", "cnt": "건",
        "ch_daily": "일자별 신호 수와 T+{k} 평균 수익률", "ch_daily_hint": "막대 클릭 = 해당 일자만 보기",
        "ch_theme": "테마별 T+{k} 평균 수익률", "ch_theme_hint": "막대 클릭 = 테마 필터",
        "lg_count": "신호 수", "lg_ret": "평균 수익률 (+ 청 / − 적)", "lg_stock": "종목 (T0=100)", "lg_kospi": "KOSPI (T0=100)", "lg_sig": "이 종목의 다른 신호",
        "seg_kind": "신호 종류", "seg_multi": "동시매수 ETF 수", "seg_flow": "추정 순매수 규모", "seg_pre": "매수 전 5일 흐름", "seg_theme": "테마",
        "s_new": "NEW 신규편입", "s_add": "ADD 비중확대", "s_one": "ETF 1개", "s_two": "ETF 2개 이상", "s_three": "ETF 3개 이상",
        "s_f50": "50억 이상", "s_f10": "10 ~ 50억", "s_f0": "10억 미만",
        "s_chase": "+5% 이상 (추격)", "s_flat": "±5% 이내", "s_dip": "−5% 이하 (저가매수)",
        "th_group": "구분", "th_n": "확정 / 전체", "th_mean": "평균 %", "th_med": "중앙 %", "th_win": "승률", "th_x": "KOSPI 대비 pp", "th_pend": "미확정",
        "th_dt": "기준일", "th_stock": "종목", "th_kind": "종류", "th_etfs": "ETF", "th_flow": "순매수 억", "th_theme": "테마", "th_pre": "직전5일 %",
        "th_r1": "T+1 %", "th_r3": "T+3 %", "th_r5": "T+5 %", "th_r10": "T+10 %", "th_xk": "KOSPI 대비 T+{k}",
        "more": "{n}건 더 보기", "no_rows": "조건에 맞는 신호가 없습니다.",
        "dr_title": "종목 드릴다운", "dr_close": "닫기", "dr_pick": "위 표에서 행을 클릭하면 여기에 주가 흐름이 표시됩니다.",
        "dr_meta": "기준일 <b>{dt}</b> · <b>{kind}</b> · ETF <b>{n}</b>개 · 추정 순매수 <b>{flow}억</b> · T0 종가 <b>{c}</b>",
        "dr_chart": "T0 = 100 으로 환산한 주가 vs KOSPI", "dr_etfs": "이 날 매수한 ETF", "dr_hist": "이 종목의 신호 이력",
        "th_etf": "ETF", "th_w": "비중 %", "th_pw": "전일 비중", "th_dq": "1CU 수량 Δ%", "th_dflow": "순매수 억",
        "tip_n": "신호", "tip_mean": "평균", "tip_win": "승률", "tip_pend": "미확정", "tip_since": "T0 대비",
        "kind_NEW": "신규편입", "kind_ADD": "비중확대",
        "a_win_bad": "T+{k} 승률 <b>{w}%</b> — 기준 50%에 크게 미달. 이 구간에서 신호를 그대로 따라 사는 전략은 불리했습니다.",
        "a_win_warn": "T+{k} 승률 <b>{w}%</b> — 50% 아래. 종목 선별 없이 따라가면 손실 확률이 더 높았습니다.",
        "a_win_good": "T+{k} 승률 <b>{w}%</b>, 평균 <b>{m}%</b> — 신호가 평균적으로 유효했던 구간입니다.",
        "a_chase": "신호 <b>{p}%</b>({c}건)가 직전 5일 +5% 이상 급등 후 매수(추격). 이 유형의 T+{k} 평균은 <b>{m}%</b>입니다.",
        "a_multi_bad": "2개 이상 ETF가 동시에 산 종목 <b>{c}</b>건의 T+{k} 평균 <b>{m}%</b>로 전체({all}%)보다 부진 — 패시브 정기변경 매수가 단기 고점일 가능성.",
        "a_multi_good": "2개 이상 ETF 동시매수 <b>{c}</b>건의 T+{k} 평균 <b>{m}%</b> — 전체({all}%)보다 양호.",
        "a_theme_best": "<b>{t}</b> 테마 T+{k} 평균 <b>{m}%</b>, 승률 {w}% (n={n}) — 선택 구간 최상위.",
        "a_theme_worst": "<b>{t}</b> 테마 T+{k} 평균 <b>{m}%</b>, 승률 {w}% (n={n}) — 신호 후 대부분 하락.",
        "a_pend": "T+{k} 수익률 미확정 신호 <b>{c}</b>건 (최근 기준일). 시세 갱신 후 재실행하면 채워집니다.",
        "a_follow": "공개 후 실제 따라 살 수 있는 시점(T+1 종가)에 들어가 T+5까지 보유하면 평균 <b>{m}%</b>, 승률 {w}% (n={n}).",
        "act_theme": "이 테마만 보기", "lvl_danger": "위험", "lvl_warn": "주의", "lvl_good": "양호", "lvl_info": "참고",
        "ft_method": "산출 방법", "ft_caveat": "해석 시 유의",
        "ft_m1": "연속 기준일 쌍을 모두 비교해 1CU 수량 기준 NEW/ADD 이벤트를 추출하고 (기준일, 종목) 단위로 묶었습니다. 여러 ETF가 같은 날 사면 ETF 수가 커집니다.",
        "ft_m2": "수익률 = 기준일(T0) 종가 대비 T+k 영업일 종가. KOSPI 대비 = 같은 기간 KOSPI 수익률을 뺀 값(pp). 시세 출처: 네이버 금융 일별 종가.",
        "ft_m3": "색 규칙 — <span class=swatch style=background:#0086B8></span>긍정(청) · <span class=swatch style=background:#F58220></span>주의(주황) · <span class=swatch style=background:#C62828></span>위험(적). 국내 시세 관행(상승=적)과 반대이므로 유의.",
        "ft_c1": "기준일 D의 PDF는 D 장 마감 후 확정되어 D+1에 공개됩니다. 현실적으로 따라 살 수 있는 성과는 'T+1 진입' 지표입니다.",
        "ft_c2": "역인덱스 수집은 조회한 종목만 저장하므로 직전일에 조회 기록이 없는 종목의 NEW는 제외했습니다 ({u}건, events_uncertain_new.csv).",
        "ft_c3": "최근 기준일 신호는 T+5/T+10이 아직 없습니다. 표본이 1~2개월이므로 특정 장세의 관찰치로 읽어야 합니다.",
    },
    "en": {
        "tag": "ThemeETF · Post-signal performance",
        "title": "Stock Performance After ETF Buy Signals",
        "sub": "Returns after <b>NEW</b> (new holding) and <b>ADD</b> (weight increase) signals detected in thematic ETF holdings. T0 close → T+k trading-day close, with KOSPI excess return.",
        "fresh_ok": "Prices current", "fresh_stale": "Prices need refresh",
        "price_end": "Last price", "signal_range": "Signal dates", "generated": "Built",
        "f_period": "Signal date range", "p5": "Last 5 days", "p10": "Last 10 days", "p20": "Last 20 days", "pall": "All", "pcustom": "Custom",
        "f_h": "Horizon", "f_kind": "Signal type", "k_all": "All", "k_new": "NEW", "k_add": "ADD",
        "f_theme": "Theme", "t_all": "All themes", "f_q": "Search", "q_ph": "Name or code",
        "reset": "Reset filters",
        "note": "<b>{n}</b> signals · <b>{s}</b> stocks · <b>{d}</b> signal dates · prior window {prev} · KOSPI over same span <b>{k}</b>",
        "note_noprev": "(none)",
        "sec_alert": "What to look at now", "sec_alert_hint": "Anomalies and reference points extracted from the selected window",
        "sec_kpi": "Key metrics", "sec_kpi_hint": "▲▼ = change vs the preceding window of equal length",
        "sec_charts": "By date · by theme", "sec_seg": "Which signals worked", "sec_seg_hint": "T+{k} performance by segment · vertical mark on win-rate bar = 50%",
        "sec_table": "Signal list", "sec_table_hint": "Click a row to drill into the price path and the buying ETFs",
        "kpi_mean": "Avg. T+{k} return", "kpi_mean_sub": "settled {nk}/{n} · vs KOSPI {x}",
        "kpi_win": "T+{k} win rate", "kpi_win_sub": "baseline 50%", "kpi_n": "Signals", "kpi_n_sub": "NEW {a} · ADD {b}",
        "kpi_chase": "Chasing share", "kpi_chase_sub": "bought after +5% or more in prior 5 days", "kpi_multi": "Multi-ETF buys", "kpi_multi_sub": "2+ ETFs · avg {m}",
        "kpi_pend": "T+{k} pending", "kpi_pend_sub": "fills in after a price refresh",
        "vs_prev": "vs prior window", "pp": "pp", "cnt": "",
        "ch_daily": "Signals per date and avg. T+{k} return", "ch_daily_hint": "click a bar = that date only",
        "ch_theme": "Avg. T+{k} return by theme", "ch_theme_hint": "click a bar = theme filter",
        "lg_count": "Signals", "lg_ret": "Avg. return (+ blue / − red)", "lg_stock": "Stock (T0=100)", "lg_kospi": "KOSPI (T0=100)", "lg_sig": "Other signals on this stock",
        "seg_kind": "Signal type", "seg_multi": "Simultaneous ETFs", "seg_flow": "Est. net buy size", "seg_pre": "5-day move before buy", "seg_theme": "Theme",
        "s_new": "NEW", "s_add": "ADD", "s_one": "1 ETF", "s_two": "2+ ETFs", "s_three": "3+ ETFs",
        "s_f50": "≥ KRW 5bn", "s_f10": "1 – 5bn", "s_f0": "< 1bn",
        "s_chase": "≥ +5% (chasing)", "s_flat": "within ±5%", "s_dip": "≤ −5% (dip buy)",
        "th_group": "Segment", "th_n": "settled / total", "th_mean": "Mean %", "th_med": "Median %", "th_win": "Win rate", "th_x": "vs KOSPI pp", "th_pend": "Pending",
        "th_dt": "Date", "th_stock": "Stock", "th_kind": "Type", "th_etfs": "ETFs", "th_flow": "Net buy (0.1bn)", "th_theme": "Theme", "th_pre": "Prior 5d %",
        "th_r1": "T+1 %", "th_r3": "T+3 %", "th_r5": "T+5 %", "th_r10": "T+10 %", "th_xk": "vs KOSPI T+{k}",
        "more": "Show {n} more", "no_rows": "No signals match the filters.",
        "dr_title": "Stock drill-down", "dr_close": "Close", "dr_pick": "Click a row above to see the price path here.",
        "dr_meta": "Signal date <b>{dt}</b> · <b>{kind}</b> · <b>{n}</b> ETFs · est. net buy <b>KRW {flow}×0.1bn</b> · T0 close <b>{c}</b>",
        "dr_chart": "Price rebased to 100 at T0 vs KOSPI", "dr_etfs": "ETFs that bought on this date", "dr_hist": "Signal history for this stock",
        "th_etf": "ETF", "th_w": "Weight %", "th_pw": "Prev weight", "th_dq": "Δ qty per CU %", "th_dflow": "Net buy (0.1bn)",
        "tip_n": "signals", "tip_mean": "mean", "tip_win": "win", "tip_pend": "pending", "tip_since": "vs T0",
        "kind_NEW": "NEW", "kind_ADD": "ADD",
        "a_win_bad": "T+{k} win rate <b>{w}%</b> — far below the 50% baseline. Blindly following signals lost money in this window.",
        "a_win_warn": "T+{k} win rate <b>{w}%</b> — under 50%. Following signals without selection was more likely to lose.",
        "a_win_good": "T+{k} win rate <b>{w}%</b>, mean <b>{m}%</b> — signals were effective on average in this window.",
        "a_chase": "<b>{p}%</b> of signals ({c}) were buys after a ≥ +5% run in the prior 5 days (chasing). Their T+{k} mean is <b>{m}%</b>.",
        "a_multi_bad": "<b>{c}</b> stocks bought by 2+ ETFs on the same day averaged <b>{m}%</b> at T+{k}, worse than overall ({all}%) — passive rebalancing buys may mark short-term tops.",
        "a_multi_good": "<b>{c}</b> multi-ETF buys averaged <b>{m}%</b> at T+{k} — better than overall ({all}%).",
        "a_theme_best": "<b>{t}</b> theme: T+{k} mean <b>{m}%</b>, win rate {w}% (n={n}) — best in window.",
        "a_theme_worst": "<b>{t}</b> theme: T+{k} mean <b>{m}%</b>, win rate {w}% (n={n}) — most fell after the signal.",
        "a_pend": "<b>{c}</b> signals have no T+{k} return yet (recent dates). Re-run after a price refresh.",
        "a_follow": "Entering at the T+1 close (first realistic entry) and holding to T+5 averaged <b>{m}%</b>, win rate {w}% (n={n}).",
        "act_theme": "Show this theme", "lvl_danger": "Risk", "lvl_warn": "Caution", "lvl_good": "Good", "lvl_info": "Note",
        "ft_method": "Method", "ft_caveat": "Caveats",
        "ft_m1": "Every consecutive pair of snapshot dates is compared; NEW/ADD events are based on quantity per creation unit and grouped by (date, stock). Several ETFs buying the same day raise the ETF count.",
        "ft_m2": "Return = T+k trading-day close vs the signal-date (T0) close. vs KOSPI = minus the KOSPI return over the same span (pp). Prices: Naver Finance daily closes.",
        "ft_m3": "Color rule — <span class=swatch style=background:#0086B8></span>positive (blue) · <span class=swatch style=background:#F58220></span>caution (orange) · <span class=swatch style=background:#C62828></span>risk (red). Note this is the reverse of the Korean market convention (red = up).",
        "ft_c1": "A date-D holdings file is finalised after the D close and published on D+1. The realistic follow-along result is the 'T+1 entry' metric.",
        "ft_c2": "Reverse-index collection stores only queried stocks, so NEW events for stocks with no query record on the prior date were excluded ({u}, events_uncertain_new.csv).",
        "ft_c3": "The most recent signal dates have no T+5/T+10 yet. The sample spans one to two months, so read it as an observation of this market regime.",
    },
}

_JS = r"""
const D = window.__DATA__;
const I18N = window.__I18N__;
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));
const el = (t, c, txt) => { const n = document.createElement(t); if (c) n.className = c; if (txt != null) n.textContent = txt; return n; };
const NS = "http://www.w3.org/2000/svg";
const svgEl = (t, a={}) => { const n = document.createElementNS(NS, t); for (const k in a) n.setAttribute(k, a[k]); return n; };

let lang = "ko";
try { lang = localStorage.getItem("themeetf.lang") || "ko"; } catch (e) {}
const t = (k, vars) => {
  let s = (I18N[lang] && I18N[lang][k]) ?? I18N.ko[k] ?? k;
  if (vars) for (const v in vars) s = s.split("{" + v + "}").join(vars[v]);
  return s;
};
const fmtD = d => d.length === 8 ? d.slice(0,4) + "-" + d.slice(4,6) + "-" + d.slice(6) : d;
const fmtMD = d => d.length === 8 ? d.slice(4,6) + "-" + d.slice(6) : d;
const nf = (v, d=1) => (v == null || isNaN(v)) ? "–" : Number(v).toLocaleString("en-US", {minimumFractionDigits:d, maximumFractionDigits:d});
const sf = (v, d=1) => (v == null || isNaN(v)) ? "–" : (v > 0 ? "+" : "") + nf(v, d);
const mean = a => a.length ? a.reduce((x,y)=>x+y,0)/a.length : null;
const median = a => { if (!a.length) return null; const s=[...a].sort((x,y)=>x-y); const m=s.length>>1; return s.length%2 ? s[m] : (s[m-1]+s[m])/2; };
const pct = (num, den) => den ? Math.round(num/den*100) : null;

/* ------------------------------------------------------------ 상태 */
const DATES = D.dates;                    // 오름차순
const THEMES = Array.from(new Set(D.signals.flatMap(s => s.themes))).sort();
const state = { preset:"20", start:null, end:null, k:"5", kind:"ALL", theme:"ALL", q:"",
                sortKey:"dt", sortDir:-1, sel:null, limit:80, segTab:"kind" };
function applyPreset(p) {
  state.preset = p;
  state.end = DATES[DATES.length-1];
  if (p === "all") state.start = DATES[0];
  else { const n = +p; state.start = DATES[Math.max(0, DATES.length - n)]; }
}
applyPreset("20");

/* ------------------------------------------------------------ 계산 */
function baseRows() {
  const q = state.q.trim().toLowerCase();
  return D.signals.filter(s =>
    s.dt >= state.start && s.dt <= state.end &&
    (state.kind === "ALL" || s.kind === state.kind) &&
    (state.theme === "ALL" || s.themes.includes(state.theme)) &&
    (!q || s.name.toLowerCase().includes(q) || s.code.includes(q)));
}
function rowsIn(start, end) {
  const q = state.q.trim().toLowerCase();
  return D.signals.filter(s =>
    s.dt >= start && s.dt <= end &&
    (state.kind === "ALL" || s.kind === state.kind) &&
    (state.theme === "ALL" || s.themes.includes(state.theme)) &&
    (!q || s.name.toLowerCase().includes(q) || s.code.includes(q)));
}
function stats(rows, k) {
  const rk = "r" + k, xk = "x" + k;
  const r = rows.map(s => s[rk]).filter(v => v != null);
  const x = rows.map(s => s[xk]).filter(v => v != null);
  return { n: rows.length, nk: r.length, pend: rows.length - r.length,
           mean: mean(r), med: median(r), win: r.length ? pct(r.filter(v=>v>0).length, r.length) : null,
           xmean: mean(x) };
}
function prevWindow() {
  const i0 = DATES.indexOf(state.start), i1 = DATES.indexOf(state.end);
  if (i0 <= 0) return null;
  const len = i1 - i0 + 1;
  const pEnd = DATES[i0-1], pStart = DATES[Math.max(0, i0-len)];
  return { start: pStart, end: pEnd };
}
function kospiRet(start, end) {
  const idx = Object.fromEntries(D.kospi.d.map((d,i)=>[d, D.kospi.c[i]]));
  if (!(start in idx) || !(end in idx)) return null;
  return (idx[end]/idx[start]-1)*100;
}
function severity(win) { return win == null ? "" : win >= 50 ? "" : win >= 40 ? "warn" : "danger"; }

/* ------------------------------------------------------------ 툴팁 */
const tip = $("#tip");
function showTip(ev, html) {
  tip.innerHTML = html; tip.style.opacity = 1;
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > innerWidth - 8) x = ev.clientX - w - pad;
  if (y + h > innerHeight - 8) y = ev.clientY - h - pad;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
const hideTip = () => { tip.style.opacity = 0; };
function attachTip(node, htmlFn) {
  node.addEventListener("pointermove", ev => showTip(ev, htmlFn()));
  node.addEventListener("pointerleave", hideTip);
  node.addEventListener("focus", () => { const r = node.getBoundingClientRect(); showTip({clientX:r.left+r.width/2, clientY:r.top}, htmlFn()); });
  node.addEventListener("blur", hideTip);
}
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));

/* ------------------------------------------------------------ SVG 도형 */
function vBar(x, y0, y1, w, r=4) {          // 세로 막대, 데이터 끝 4px 라운드, 기준선 쪽 직각
  const top = Math.min(y0, y1), bot = Math.max(y0, y1), h = bot - top;
  r = Math.min(r, w/2, h);
  if (h <= 0.5) return "";
  if (y1 <= y0)  // 위로 자람 → 위쪽 라운드
    return `M${x},${bot} V${top+r} Q${x},${top} ${x+r},${top} H${x+w-r} Q${x+w},${top} ${x+w},${top+r} V${bot} Z`;
  return `M${x},${top} V${bot-r} Q${x},${bot} ${x+r},${bot} H${x+w-r} Q${x+w},${bot} ${x+w},${bot-r} V${top} Z`;
}
function hBar(x0, x1, y, h, r=4) {           // 가로 막대
  const left = Math.min(x0, x1), right = Math.max(x0, x1), w = right - left;
  r = Math.min(r, h/2, w);
  if (w <= 0.5) return "";
  if (x1 >= x0)
    return `M${left},${y} H${right-r} Q${right},${y} ${right},${y+r} V${y+h-r} Q${right},${y+h} ${right-r},${y+h} H${left} Z`;
  return `M${right},${y} H${left+r} Q${left},${y} ${left},${y+r} V${y+h-r} Q${left},${y+h} ${left+r},${y+h} H${right} Z`;
}
function niceMax(v, ticks=4) {
  if (!v || v <= 0) return 1;
  const raw = v / ticks, p = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1,2,2.5,5,10].map(m=>m*p).find(s => s >= raw) || 10*p;
  return step * ticks;
}
function text(cls, x, y, s, anchor="start") {
  const n = svgEl("text", {class:cls, x, y, "text-anchor":anchor}); n.textContent = s; return n;
}

/* ------------------------------------------------------------ 렌더: 헤더/필터 */
function renderStatic() {
  document.documentElement.lang = lang;
  $$("[data-i18n]").forEach(n => { n.innerHTML = t(n.dataset.i18n); });
  $$("[data-i18n-ph]").forEach(n => { n.placeholder = t(n.dataset.i18nPh); });
  $$(".lang button").forEach(b => b.setAttribute("aria-checked", b.dataset.lang === lang ? "true" : "false"));
  $("#priceEnd").textContent = fmtD(D.meta.priceEnd);
  $("#sigRange").textContent = fmtD(DATES[0]) + " ~ " + fmtD(DATES[DATES.length-1]);
  $("#generated").textContent = D.meta.generated;
  const stale = D.meta.priceEnd < DATES[DATES.length-1];
  const st = $("#status"); st.className = "status" + (stale ? " stale" : ""); st.textContent = t(stale ? "fresh_stale" : "fresh_ok");

  // 테마 select
  const sel = $("#theme"); sel.innerHTML = "";
  const o0 = el("option", null, t("t_all")); o0.value = "ALL"; sel.appendChild(o0);
  THEMES.forEach(th => { const o = el("option", null, th); o.value = th; sel.appendChild(o); });
  sel.value = state.theme;
  // 날짜 select
  for (const id of ["start", "end"]) {
    const s = $("#" + id); s.innerHTML = "";
    DATES.forEach(d => { const o = el("option", null, fmtD(d)); o.value = d; s.appendChild(o); });
    s.value = state[id];
  }
  $$(".seg[data-for=preset] button").forEach(b => b.classList.toggle("on", b.dataset.v === state.preset));
  $$(".seg[data-for=k] button").forEach(b => b.classList.toggle("on", b.dataset.v === state.k));
  $$(".seg[data-for=kind] button").forEach(b => b.classList.toggle("on", b.dataset.v === state.kind));
  $("#q").value = state.q;
}

/* ------------------------------------------------------------ 렌더: 알림 */
function renderAlerts(rows, S) {
  const host = $("#alerts"); host.innerHTML = "";
  const k = state.k, rk = "r" + k;
  const cards = [];
  if (S.nk >= 5) {
    if (S.win < 40) cards.push({lvl:"danger", pr:1, msg:t("a_win_bad", {k, w:S.win})});
    else if (S.win < 50) cards.push({lvl:"warn", pr:2, msg:t("a_win_warn", {k, w:S.win})});
    else cards.push({lvl:"good", pr:2, msg:t("a_win_good", {k, w:S.win, m:sf(S.mean)})});
  }
  const withPre = rows.filter(s => s.pre5 != null);
  const chase = withPre.filter(s => s.pre5 > 5);
  const chaseR = chase.map(s=>s[rk]).filter(v=>v!=null);
  if (withPre.length >= 5 && chase.length/withPre.length >= 0.25 && chaseR.length >= 3)
    cards.push({lvl: mean(chaseR) < 0 ? "warn" : "info", pr:3,
      msg:t("a_chase", {p:pct(chase.length, withPre.length), c:chase.length, k, m:sf(mean(chaseR))})});
  const multi = rows.filter(s => s.n >= 2), multiR = multi.map(s=>s[rk]).filter(v=>v!=null);
  if (multiR.length >= 3 && S.mean != null) {
    const m = mean(multiR);
    cards.push(m < S.mean ? {lvl:"warn", pr:4, msg:t("a_multi_bad", {c:multiR.length, k, m:sf(m), all:sf(S.mean)})}
                          : {lvl:"good", pr:5, msg:t("a_multi_good", {c:multiR.length, k, m:sf(m), all:sf(S.mean)})});
  }
  if (state.theme === "ALL") {
    const byTheme = {};
    rows.forEach(s => s.themes.forEach(th => { (byTheme[th] ??= []).push(s); }));
    const ts = Object.entries(byTheme).map(([th, rs]) => ({th, ...stats(rs, k)})).filter(x => x.nk >= 5);
    ts.sort((a,b) => b.mean - a.mean);
    if (ts.length >= 2) {
      const best = ts[0], worst = ts[ts.length-1];
      if (best.mean > 0) cards.push({lvl:"good", pr:6, theme:best.th, msg:t("a_theme_best", {t:best.th, k, m:sf(best.mean), w:best.win, n:best.nk})});
      if (worst.mean < -2) cards.push({lvl:"danger", pr:2.5, theme:worst.th, msg:t("a_theme_worst", {t:worst.th, k, m:sf(worst.mean), w:worst.win, n:worst.nk})});
    }
  }
  const f5 = rows.map(s=>s.r1to5).filter(v=>v!=null);
  if (f5.length >= 5) cards.push({lvl:"info", pr:7, msg:t("a_follow", {m:sf(mean(f5)), w:pct(f5.filter(v=>v>0).length, f5.length), n:f5.length})});
  if (S.pend > 0) cards.push({lvl:"info", pr:8, msg:t("a_pend", {c:S.pend, k})});

  cards.sort((a,b)=>a.pr-b.pr).slice(0, 4).forEach(c => {
    const card = el("div", "alert " + c.lvl);
    card.appendChild(el("span", "lvl", t("lvl_" + c.lvl)));
    const m = el("div", "msg"); m.innerHTML = c.msg; card.appendChild(m);
    if (c.theme) {
      const a = el("div", "act"); const b = el("button", null, t("act_theme"));
      b.onclick = () => { state.theme = c.theme; $("#theme").value = c.theme; render(); };
      a.appendChild(b); card.appendChild(a);
    }
    host.appendChild(card);
  });
  if (!cards.length) host.appendChild(el("div", "empty", t("no_rows")));
}

/* ------------------------------------------------------------ 렌더: KPI */
function deltaSpan(cur, prev, {unit="pp", upGood=true, d=1, isCount=false} = {}) {
  const sp = el("span", "delta");
  if (cur == null || prev == null) { sp.textContent = "–"; sp.classList.add("flat"); return sp; }
  const diff = cur - prev;
  const arrow = diff > 0 ? "▲" : diff < 0 ? "▼" : "▬";
  sp.textContent = arrow + " " + (isCount ? sf(diff, 0) : sf(diff, d) + unit);
  if (isCount || Math.abs(diff) < 1e-9) sp.classList.add("flat");
  else sp.classList.add((diff > 0) === upGood ? "okay" : "bad");
  return sp;
}
function kpi(label, valueHtml, subNode, cls="") {
  const c = el("div", "kpi " + cls);
  c.appendChild(el("div", "k", label));
  const v = el("div", "v"); v.innerHTML = valueHtml; c.appendChild(v);
  if (subNode) c.appendChild(subNode);
  return c;
}
function renderKpis(rows, S, P) {
  const host = $("#kpis"); host.innerHTML = ""; const k = state.k;
  // hero
  const d1 = el("div", "d"); d1.appendChild(deltaSpan(S.mean, P && P.mean)); d1.appendChild(el("span", null, t("vs_prev")));
  const hero = kpi(t("kpi_mean", {k}), (S.mean == null ? "–" : sf(S.mean)) + "<small>%</small>", d1, "hero");
  hero.appendChild(el("div", "sub", t("kpi_mean_sub", {nk:S.nk, n:S.n, x: S.xmean == null ? "–" : sf(S.xmean) + "pp"})));
  host.appendChild(hero);
  // win
  const d2 = el("div", "d"); d2.appendChild(deltaSpan(S.win, P && P.win)); d2.appendChild(el("span", null, t("vs_prev")));
  const win = kpi(t("kpi_win", {k}), (S.win == null ? "–" : S.win) + "<small>%</small>", d2);
  const m = el("div", "meter"); const fill = el("i", severity(S.win)); fill.style.width = (S.win || 0) + "%"; m.appendChild(fill); win.appendChild(m);
  const mn = el("div", "meter-note"); mn.appendChild(el("span", null, "0")); mn.appendChild(el("span", null, t("kpi_win_sub"))); mn.appendChild(el("span", null, "100")); win.appendChild(mn);
  host.appendChild(win);
  // count
  const d3 = el("div", "d"); d3.appendChild(deltaSpan(S.n, P && P.n, {isCount:true})); d3.appendChild(el("span", null, t("vs_prev")));
  const nNew = rows.filter(s=>s.kind==="NEW").length;
  const cnt = kpi(t("kpi_n"), S.n + "<small>" + t("cnt") + "</small>", d3);
  cnt.appendChild(el("div", "sub", t("kpi_n_sub", {a:nNew, b:S.n-nNew}))); host.appendChild(cnt);
  // chase
  const wp = rows.filter(s=>s.pre5!=null), ch = wp.filter(s=>s.pre5>5);
  const chaseP = wp.length ? ch.length/wp.length*100 : null;
  let prevChase = null;
  if (P) { const pr = P.rows.filter(s=>s.pre5!=null); prevChase = pr.length ? pr.filter(s=>s.pre5>5).length/pr.length*100 : null; }
  const d4 = el("div", "d"); d4.appendChild(deltaSpan(chaseP, prevChase, {upGood:false, d:0})); d4.appendChild(el("span", null, t("vs_prev")));
  const chase = kpi(t("kpi_chase"), (chaseP == null ? "–" : Math.round(chaseP)) + "<small>%</small>", d4);
  chase.appendChild(el("div", "sub", t("kpi_chase_sub"))); host.appendChild(chase);
  // multi
  const multi = rows.filter(s=>s.n>=2), mr = multi.map(s=>s["r"+k]).filter(v=>v!=null);
  const d5 = el("div", "d"); d5.appendChild(deltaSpan(multi.length, P && P.rows.filter(s=>s.n>=2).length, {isCount:true})); d5.appendChild(el("span", null, t("vs_prev")));
  const mu = kpi(t("kpi_multi"), multi.length + "<small>" + t("cnt") + "</small>", d5);
  mu.appendChild(el("div", "sub", t("kpi_multi_sub", {m: mr.length ? sf(mean(mr)) + "%" : "–"}))); host.appendChild(mu);
}

/* ------------------------------------------------------------ 렌더: 일자별 */
function renderDaily(rows) {
  const host = $("#daily"); host.innerHTML = "";
  const k = state.k, rk = "r" + k;
  const days = DATES.filter(d => d >= state.start && d <= state.end);
  const by = {}; days.forEach(d => by[d] = []); rows.forEach(s => by[s.dt].push(s));
  const data = days.map(d => { const rs = by[d]; const r = rs.map(s=>s[rk]).filter(v=>v!=null);
    return { d, n: rs.length, nk: r.length, mean: mean(r), win: r.length ? pct(r.filter(v=>v>0).length, r.length) : null }; });
  if (!days.length) { host.appendChild(el("div", "empty", t("no_rows"))); return; }

  const W = Math.max(320, host.clientWidth), L = 46, R = 14;
  const H1 = 96, GAP = 26, H2 = 120, XB = 24, H = H1 + GAP + H2 + XB;
  const band = (W - L - R) / days.length, bw = Math.min(24, band * 0.62);
  const svg = svgEl("svg", {width:W, height:H, role:"img", "aria-label": t("ch_daily", {k})});
  // 위: 신호 수
  const maxN = niceMax(Math.max(...data.map(x=>x.n), 1), 3);
  const y1 = v => 8 + (H1-8) * (1 - v/maxN);
  for (let i=0;i<=3;i++) { const v = maxN/3*i, y = y1(v);
    svg.appendChild(svgEl("line", {class: i? "grid":"axis", x1:L, x2:W-R, y1:y, y2:y}));
    svg.appendChild(text("tick", L-8, y+4, nf(v,0), "end")); }
  svg.appendChild(text("tick", L, 0, "", "start"));
  // 아래: 평균 수익률 (발산)
  const maxAbs = niceMax(Math.max(...data.map(x=>Math.abs(x.mean||0)), 0.5), 2);
  const top2 = H1 + GAP, zero = top2 + H2/2;
  const y2 = v => zero - (v/maxAbs) * (H2/2);
  for (const v of [-maxAbs, -maxAbs/2, 0, maxAbs/2, maxAbs]) { const y = y2(v);
    svg.appendChild(svgEl("line", {class: v===0 ? "axis":"grid", x1:L, x2:W-R, y1:y, y2:y}));
    svg.appendChild(text("tick", L-8, y+4, sf(v, maxAbs<2?1:0), "end")); }
  const every = band >= 44 ? 1 : band >= 30 ? 2 : Math.ceil(44/band);
  data.forEach((x, i) => {
    const cx = L + band*i + band/2, bx = cx - bw/2;
    const g = svgEl("g");
    const isSel = state.preset === "custom" && state.start === state.end && x.d === state.start;
    if (x.n > 0) g.appendChild(svgEl("path", {class:"bar", d:vBar(bx, y1(0), y1(x.n), bw), fill:"var(--seq)"}));
    if (x.mean != null) g.appendChild(svgEl("path", {class:"bar", d:vBar(bx, zero, y2(x.mean), bw), fill: x.mean >= 0 ? "var(--pos)" : "var(--neg)"}));
    if (i % every === 0 || days.length <= 12) g.appendChild(text("tick", cx, H-6, fmtMD(x.d), "middle"));
    if (isSel) g.appendChild(svgEl("rect", {x:L+band*i, y:0, width:band, height:H-XB+4, fill:"none", stroke:"var(--orange)", "stroke-width":1.5}));
    const hit = svgEl("rect", {class:"hit", x:L+band*i, y:0, width:band, height:H, tabindex:0, role:"button", "aria-label":fmtD(x.d)});
    attachTip(hit, () => `<div><b>${fmtD(x.d)}</b></div>
      <div class=row><span class=dim>${t("tip_n")}</span><b>${x.n}</b></div>
      <div class=row><span class=dim>T+${k} ${t("tip_mean")}</span><b>${x.mean==null?"–":sf(x.mean)+"%"}</b></div>
      <div class=row><span class=dim>${t("tip_win")}</span><b>${x.win==null?"–":x.win+"%"}</b></div>
      ${x.n-x.nk ? `<div class=row><span class=dim>${t("tip_pend")}</span><b>${x.n-x.nk}</b></div>` : ""}`);
    const pick = () => { if (isSel) { applyPreset("20"); } else { state.preset = "custom"; state.start = x.d; state.end = x.d; } hideTip(); render(); };
    hit.addEventListener("click", pick);
    hit.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    g.appendChild(hit);
    svg.appendChild(g);
  });
  host.appendChild(svg);
}

/* ------------------------------------------------------------ 렌더: 테마별 */
function renderThemes(rows) {
  const host = $("#themes"); host.innerHTML = "";
  const k = state.k;
  const by = {}; rows.forEach(s => s.themes.forEach(th => (by[th] ??= []).push(s)));
  const data = Object.entries(by).map(([th, rs]) => ({th, ...stats(rs, k)})).filter(x => x.nk > 0)
    .sort((a,b) => b.mean - a.mean);
  if (!data.length) { host.appendChild(el("div", "empty", t("no_rows"))); return; }
  const W = Math.max(280, host.clientWidth), narrow = W < 560;
  const GUT = narrow ? 106 : 118, R = narrow ? 12 : 96, ROW = 34, BAR = 14, XB = 22;
  const H = data.length * ROW + XB + 6;
  const maxAbs = niceMax(Math.max(...data.map(x=>Math.abs(x.mean)), 0.5), 2);
  const half = (W - GUT - R)/2, LAB = narrow ? 42 : 52;   // 값 라벨(예: +12.3%) 자리
  const zero = GUT + half, sx = v => zero + (v/maxAbs) * (half - LAB);
  const svg = svgEl("svg", {width:W, height:H, role:"img", "aria-label": t("ch_theme", {k})});
  for (const v of [-maxAbs, -maxAbs/2, 0, maxAbs/2, maxAbs]) { const x = sx(v);
    svg.appendChild(svgEl("line", {class: v===0?"axis":"grid", x1:x, x2:x, y1:2, y2:H-XB}));
    svg.appendChild(text("tick", x, H-6, sf(v, maxAbs<2?1:0), "middle")); }
  data.forEach((x, i) => {
    const y = 4 + i*ROW + (ROW-BAR)/2;
    const g = svgEl("g");
    const on = state.theme === x.th;
    g.appendChild(svgEl("path", {class:"bar", d:hBar(zero, sx(x.mean), y, BAR), fill: x.mean >= 0 ? "var(--pos)" : "var(--neg)"}));
    const name = text("cat", GUT-10, y+BAR/2+4, x.th, "end"); if (on) name.setAttribute("font-weight", "700"); g.appendChild(name);
    const lab = text("val", x.mean >= 0 ? sx(x.mean)+7 : sx(x.mean)-7, y+BAR/2+4, sf(x.mean)+"%", x.mean >= 0 ? "start" : "end"); g.appendChild(lab);
    if (!narrow) g.appendChild(text("tick", W-R+8, y+BAR/2+4, `n=${x.nk} · ${x.win}%`, "start"));
    if (on) g.appendChild(svgEl("rect", {x:2, y:4+i*ROW, width:W-4, height:ROW, fill:"none", stroke:"var(--orange)", "stroke-width":1.5}));
    const hit = svgEl("rect", {class:"hit", x:0, y:4+i*ROW, width:W, height:ROW, tabindex:0, role:"button", "aria-label":x.th});
    attachTip(hit, () => `<div><b>${esc(x.th)}</b></div>
      <div class=row><span class=dim>${t("tip_n")}</span><b>${x.n}</b></div>
      <div class=row><span class=dim>T+${k} ${t("tip_mean")}</span><b>${sf(x.mean)}%</b></div>
      <div class=row><span class=dim>${t("tip_win")}</span><b>${x.win}%</b></div>
      <div class=row><span class=dim>vs KOSPI</span><b>${sf(x.xmean)}pp</b></div>`);
    const pick = () => { state.theme = on ? "ALL" : x.th; $("#theme").value = state.theme; hideTip(); render(); };
    hit.addEventListener("click", pick);
    hit.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    g.appendChild(hit); svg.appendChild(g);
  });
  host.appendChild(svg);
}

/* ------------------------------------------------------------ 렌더: 구분별 */
const SEGMENTS = {
  kind:  rows => [["s_new", rows.filter(s=>s.kind==="NEW")], ["s_add", rows.filter(s=>s.kind==="ADD")]],
  multi: rows => [["s_one", rows.filter(s=>s.n===1)], ["s_two", rows.filter(s=>s.n>=2)], ["s_three", rows.filter(s=>s.n>=3)]],
  flow:  rows => [["s_f50", rows.filter(s=>s.flow>=50)], ["s_f10", rows.filter(s=>s.flow>=10&&s.flow<50)], ["s_f0", rows.filter(s=>s.flow<10)]],
  pre:   rows => [["s_chase", rows.filter(s=>s.pre5>5)], ["s_flat", rows.filter(s=>s.pre5!=null&&s.pre5<=5&&s.pre5>=-5)], ["s_dip", rows.filter(s=>s.pre5<-5)]],
  theme: rows => { const by={}; rows.forEach(s=>s.themes.forEach(th=>(by[th]??=[]).push(s)));
                   return Object.entries(by).sort((a,b)=>b[1].length-a[1].length).map(([th,rs])=>[th, rs, true]); },
};
function miniBar(v, maxAbs) {
  const m = el("span", "mini"); if (v == null) return m;
  const i = el("i", v >= 0 ? "p" : "n"); i.style.width = Math.min(50, Math.abs(v)/maxAbs*50) + "%"; m.appendChild(i); return m;
}
function winMeter(w) {
  const m = el("span", "wmeter"); if (w == null) return m;
  const i = el("i", severity(w)); i.style.width = w + "%"; m.appendChild(i); return m;
}
function renderSegments(rows, S) {
  const k = state.k; const host = $("#segments"); host.innerHTML = "";
  $$(".tabs button").forEach(b => { b.classList.toggle("on", b.dataset.v === state.segTab); b.textContent = t("seg_" + b.dataset.v); });
  const groups = SEGMENTS[state.segTab](rows).map(([key, rs, raw]) => ({label: raw ? key : t(key), ...stats(rs, k)}));
  const all = {label: t("k_all"), ...S};
  const maxAbs = Math.max(0.5, ...groups.map(g => Math.abs(g.mean || 0)), Math.abs(S.mean || 0));
  const tbl = el("table"); const thead = el("thead"); const tr = el("tr");
  [["th_group","t"],["th_n",""],["th_mean",""],["th_med",""],["th_win",""],["th_x",""],["th_pend",""]].forEach(([key, cls]) => {
    const th = el("th", cls, t(key)); tr.appendChild(th); });
  thead.appendChild(tr); tbl.appendChild(thead);
  const tb = el("tbody");
  const row = (g, cls) => {
    const r = el("tr", cls);
    r.appendChild(el("td", "t k", g.label));
    r.appendChild(el("td", null, g.nk + (g.pend ? " / " + g.n : "")));
    const tm = el("td"); tm.appendChild(miniBar(g.mean, maxAbs)); tm.appendChild(document.createTextNode(g.mean==null?"–":sf(g.mean))); r.appendChild(tm);
    r.appendChild(el("td", null, g.med==null?"–":sf(g.med)));
    const tw = el("td"); tw.appendChild(winMeter(g.win)); tw.appendChild(document.createTextNode(g.win==null?"–":g.win+"%")); r.appendChild(tw);
    r.appendChild(el("td", null, g.xmean==null?"–":sf(g.xmean)));
    r.appendChild(el("td", null, g.pend || "–"));
    return r;
  };
  groups.filter(g => g.n > 0).forEach(g => tb.appendChild(row(g)));
  tb.appendChild(row(all, "total"));
  tbl.appendChild(tb); host.appendChild(tbl);
}

/* ------------------------------------------------------------ 렌더: 신호 표 */
const COLS = [
  {key:"dt", i:"th_dt", cls:"t", fmt:s=>fmtD(s.dt)},
  {key:"name", i:"th_stock", cls:"t", html:s=>`<span class=name>${esc(s.name)}</span><span class=code>${esc(s.code)}</span>`},
  {key:"kind", i:"th_kind", cls:"t", html:s=>`<span class="chip ${s.kind}">${t("kind_"+s.kind)}</span>`},
  {key:"n", i:"th_etfs", fmt:s=>s.n},
  {key:"flow", i:"th_flow", fmt:s=>nf(s.flow,1)},
  {key:"themes", i:"th_theme", cls:"t k", fmt:s=>s.themes.join(" · ")},
  {key:"pre5", i:"th_pre", fmt:s=>sf(s.pre5)},
  {key:"r1", i:"th_r1", fmt:s=>sf(s.r1)},
  {key:"r3", i:"th_r3", fmt:s=>sf(s.r3)},
  {key:"r5", i:"th_r5", fmt:s=>sf(s.r5)},
  {key:"r10", i:"th_r10", fmt:s=>sf(s.r10)},
  {key:"xk", i:"th_xk", fmt:s=>sf(s["x"+state.k])},
];
function renderTable(rows) {
  const host = $("#table"); host.innerHTML = "";
  const k = state.k, rk = "r"+k;
  const key = state.sortKey === "xk" ? "x"+k : state.sortKey;
  const sorted = [...rows].sort((a,b) => {
    let va = a[key], vb = b[key];
    if (Array.isArray(va)) { va = va.join(); vb = vb.join(); }
    if (va == null && vb == null) return 0; if (va == null) return 1; if (vb == null) return -1;
    if (typeof va === "string") return va.localeCompare(vb) * state.sortDir;
    return (va - vb) * state.sortDir;
  });
  if (!sorted.length) { host.appendChild(el("div", "empty", t("no_rows"))); return; }
  const tbl = el("table"), thead = el("thead"), tr = el("tr");
  COLS.forEach(c => {
    const th = el("th", (c.cls||"") + " sortable" + (c.key===rk||c.key==="xk" ? " hl" : ""));
    th.textContent = t(c.i, {k}) + " ";
    if (state.sortKey === c.key) { const a = el("span", "arr", state.sortDir < 0 ? "▼" : "▲"); th.appendChild(a); }
    th.onclick = () => { if (state.sortKey === c.key) state.sortDir *= -1; else { state.sortKey = c.key; state.sortDir = c.key==="dt"||c.key==="name"||c.key==="kind"||c.key==="themes" ? 1 : -1; } renderTable(rows); };
    tr.appendChild(th);
  });
  thead.appendChild(tr); tbl.appendChild(thead);
  const tb = el("tbody");
  sorted.slice(0, state.limit).forEach(s => {
    const r = el("tr", "clickable" + (state.sel && state.sel.dt===s.dt && state.sel.code===s.code ? " sel" : ""));
    r.tabIndex = 0;
    COLS.forEach(c => {
      const td = el("td", (c.cls||"") + (c.key===rk||c.key==="xk" ? " hl" : ""));
      if (c.html) td.innerHTML = c.html(s); else td.textContent = c.fmt(s);
      if (["pre5","r1","r3","r5","r10","xk"].includes(c.key)) { const v = c.key==="xk" ? s["x"+k] : s[c.key]; if (v != null && v !== 0) td.classList.add(v > 0 ? "pos-t" : "neg-t"); }
      r.appendChild(td);
    });
    const pick = () => { state.sel = {dt:s.dt, code:s.code}; renderTable(rows); renderDrill(); $("#drill").scrollIntoView({behavior:"smooth", block:"nearest"}); };
    r.onclick = pick; r.onkeydown = e => { if (e.key === "Enter") pick(); };
    tb.appendChild(r);
  });
  tbl.appendChild(tb);
  const wrap = el("div", "tbl-wrap"); wrap.appendChild(tbl); host.appendChild(wrap);
  if (sorted.length > state.limit) {
    const m = el("div", "more"); const b = el("button", null, t("more", {n: Math.min(80, sorted.length - state.limit)}));
    b.onclick = () => { state.limit += 80; renderTable(rows); }; m.appendChild(b); host.appendChild(m);
  }
}

/* ------------------------------------------------------------ 렌더: 드릴다운 */
function renderDrill() {
  const host = $("#drill"); host.innerHTML = "";
  const sel = state.sel && D.signals.find(s => s.dt===state.sel.dt && s.code===state.sel.code);
  if (!sel) { host.appendChild(el("div", "empty", t("dr_pick"))); return; }
  const k = +state.k;
  const head = el("div", "head");
  const h3 = el("h3", null, sel.name); head.appendChild(h3);
  head.appendChild(el("span", "code", sel.code));
  const chip = el("span", "chip " + sel.kind, t("kind_"+sel.kind)); head.appendChild(chip);
  const close = el("button", "close", t("dr_close")); close.onclick = () => { state.sel = null; renderTable(baseRows()); renderDrill(); }; head.appendChild(close);
  host.appendChild(head);
  const meta = el("div", "meta"); meta.innerHTML = t("dr_meta", {dt:fmtD(sel.dt), kind:t("kind_"+sel.kind), n:sel.n, flow:nf(sel.flow,1), c:nf(sel.close0,0)});
  host.appendChild(meta);

  const grid = el("div", "drill-grid"); host.appendChild(grid);
  const left = el("div"); grid.appendChild(left);
  left.appendChild(el("h4", null, t("dr_chart")));
  const lg = el("div", "legend");
  lg.innerHTML = `<span><i class=line style="background:var(--pos)"></i>${t("lg_stock")}</span><span><i class=line style="background:var(--neutral)"></i>${t("lg_kospi")}</span><span><i class=tri></i>${t("lg_sig")}</span>`;
  left.appendChild(lg);
  const plot = el("div", "plot"); left.appendChild(plot);
  drawPrice(plot, sel, k);

  const right = el("div"); grid.appendChild(right);
  right.appendChild(el("h4", null, t("dr_etfs")));
  const evs = D.events[sel.dt + "|" + sel.code] || [];
  const tbl = el("table"); const th = el("thead"); const tr = el("tr");
  [["th_etf","t"],["th_kind","t"],["th_pw",""],["th_w",""],["th_dq",""],["th_dflow",""]].forEach(([key,cls]) => tr.appendChild(el("th", cls, t(key))));
  th.appendChild(tr); tbl.appendChild(th);
  const tb = el("tbody");
  evs.sort((a,b)=>(b.flow||0)-(a.flow||0)).forEach(e => {
    const r = el("tr");
    const c1 = el("td", "t k"); c1.innerHTML = `<span class=name>${esc(e.etf)}</span><span class=code>${esc(e.theme)}</span>`; r.appendChild(c1);
    const c2 = el("td", "t"); c2.innerHTML = `<span class="chip ${e.kind}">${t("kind_"+e.kind)}</span>`; r.appendChild(c2);
    r.appendChild(el("td", null, e.pw==null?"–":nf(e.pw,2)));
    r.appendChild(el("td", null, e.w==null?"–":nf(e.w,2)));
    r.appendChild(el("td", null, e.dq==null?"–":sf(e.dq,1)));
    r.appendChild(el("td", null, e.flow==null?"–":nf(e.flow,1)));
    tb.appendChild(r);
  });
  tbl.appendChild(tb); const w = el("div", "tbl-wrap"); w.appendChild(tbl); right.appendChild(w);

  const hist = D.signals.filter(s => s.code === sel.code).sort((a,b)=>b.dt.localeCompare(a.dt));
  if (hist.length > 1) {
    const hs = el("div", "hist"); hs.appendChild(el("h4", null, t("dr_hist")));
    const t2 = el("table"); const h2 = el("thead"); const r2 = el("tr");
    [["th_dt","t"],["th_kind","t"],["th_etfs",""],["th_flow",""],["th_r1",""],["th_r5",""],["th_r10",""]].forEach(([key,cls]) => r2.appendChild(el("th", cls, t(key))));
    h2.appendChild(r2); t2.appendChild(h2); const b2 = el("tbody");
    hist.forEach(s => {
      const r = el("tr", "clickable" + (s.dt===sel.dt ? " sel" : "")); r.tabIndex = 0;
      r.appendChild(el("td", "t", fmtD(s.dt)));
      const ck = el("td", "t"); ck.innerHTML = `<span class="chip ${s.kind}">${t("kind_"+s.kind)}</span>`; r.appendChild(ck);
      r.appendChild(el("td", null, s.n)); r.appendChild(el("td", null, nf(s.flow,1)));
      [s.r1, s.r5, s.r10].forEach(v => { const td = el("td", null, sf(v)); if (v!=null && v!==0) td.classList.add(v>0?"pos-t":"neg-t"); r.appendChild(td); });
      const pick = () => { state.sel = {dt:s.dt, code:s.code}; renderTable(baseRows()); renderDrill(); };
      r.onclick = pick; r.onkeydown = e => { if (e.key==="Enter") pick(); };
      b2.appendChild(r);
    });
    t2.appendChild(b2); const w2 = el("div", "tbl-wrap"); w2.appendChild(t2); hs.appendChild(w2); right.appendChild(hs);
  }
}

function drawPrice(host, sel, k) {
  const px = D.prices[sel.code];
  if (!px || !px.d.length) { host.appendChild(el("div", "empty", "–")); return; }
  const kIdx = Object.fromEntries(D.kospi.d.map((d,i)=>[d, D.kospi.c[i]]));
  const i0 = px.d.indexOf(sel.dt);
  if (i0 < 0) { host.appendChild(el("div", "empty", "–")); return; }
  const from = Math.max(0, i0 - 15), to = Math.min(px.d.length - 1, i0 + 20);
  const dates = px.d.slice(from, to+1), base = px.c[i0], kb = kIdx[sel.dt];
  const stock = px.c.slice(from, to+1).map(v => v/base*100);
  const kospi = dates.map(d => kb && kIdx[d] ? kIdx[d]/kb*100 : null);
  const W = Math.max(320, host.clientWidth), L = 44, R = 16, T = 12, XB = 26, H = 280;
  const plotH = H - T - XB;
  const vals = stock.concat(kospi.filter(v=>v!=null));
  let lo = Math.min(...vals), hi = Math.max(...vals); const pad = (hi-lo)*0.12 || 2; lo -= pad; hi += pad;
  const x = i => L + (W-L-R) * (dates.length===1 ? 0.5 : i/(dates.length-1));
  const y = v => T + plotH * (1 - (v-lo)/(hi-lo));
  const svg = svgEl("svg", {width:W, height:H, role:"img", "aria-label": t("dr_chart")});
  // 보유 구간 T0..T+k 음영
  const iSel = i0 - from, iK = Math.min(dates.length-1, iSel + k);
  if (iK > iSel) svg.appendChild(svgEl("rect", {x:x(iSel), y:T, width:x(iK)-x(iSel), height:plotH, fill:"var(--soft)"}));
  // 그리드
  const step = niceMax(hi-lo, 4)/4;
  for (let v = Math.ceil(lo/step)*step; v <= hi; v += step) {
    svg.appendChild(svgEl("line", {class: Math.abs(v-100)<1e-6 ? "axis":"grid", x1:L, x2:W-R, y1:y(v), y2:y(v)}));
    svg.appendChild(text("tick", L-8, y(v)+4, nf(v,0), "end"));
  }
  svg.appendChild(svgEl("line", {class:"axis", x1:L, x2:W-R, y1:y(100), y2:y(100)}));
  // T0 세로선
  svg.appendChild(svgEl("line", {x1:x(iSel), x2:x(iSel), y1:T, y2:T+plotH, stroke:"var(--orange)", "stroke-width":1.5}));
  svg.appendChild(text("tick", x(iSel), T-2, "T0", "middle"));
  // 라인
  const path = arr => arr.map((v,i) => v==null ? null : (i===0||arr[i-1]==null ? "M" : "L") + x(i).toFixed(1) + "," + y(v).toFixed(1)).filter(Boolean).join(" ");
  svg.appendChild(svgEl("path", {d:path(kospi), fill:"none", stroke:"var(--neutral)", "stroke-width":2, "stroke-linejoin":"round", "stroke-linecap":"round"}));
  svg.appendChild(svgEl("path", {d:path(stock), fill:"none", stroke:"var(--pos)", "stroke-width":2, "stroke-linejoin":"round", "stroke-linecap":"round"}));
  // 끝점 라벨
  const last = stock.length-1;
  svg.appendChild(svgEl("circle", {cx:x(last), cy:y(stock[last]), r:4.5, fill:"var(--pos)", stroke:"#fff", "stroke-width":2}));
  svg.appendChild(text("val", x(last)-8, y(stock[last])-9, sf(stock[last]-100)+"%", "end"));
  // 다른 신호 마커
  D.signals.filter(s => s.code===sel.code && s.dt!==sel.dt).forEach(s => {
    const i = dates.indexOf(s.dt); if (i < 0) return;
    const cx = x(i), cy = y(stock[i]) - 10;
    svg.appendChild(svgEl("path", {d:`M${cx},${cy-5} L${cx+5},${cy+4} L${cx-5},${cy+4} Z`, fill:"var(--ink)", stroke:"#fff", "stroke-width":1.5}));
  });
  // x 라벨
  const every = Math.max(1, Math.ceil(dates.length / Math.floor((W-L-R)/56)));
  dates.forEach((d,i) => { if (i%every===0 || i===iSel) svg.appendChild(text("tick", x(i), H-6, fmtMD(d), "middle")); });
  // 크로스헤어
  const ch = svgEl("line", {class:"crosshair", x1:0, x2:0, y1:T, y2:T+plotH, opacity:0});
  const dot = svgEl("circle", {r:4.5, fill:"var(--pos)", stroke:"#fff", "stroke-width":2, opacity:0});
  svg.appendChild(ch); svg.appendChild(dot);
  const hit = svgEl("rect", {x:L, y:0, width:W-L-R, height:H, fill:"transparent"});
  hit.addEventListener("pointermove", ev => {
    const r = svg.getBoundingClientRect(); const rel = (ev.clientX - r.left - L)/(W-L-R);
    const i = Math.max(0, Math.min(dates.length-1, Math.round(rel*(dates.length-1))));
    ch.setAttribute("x1", x(i)); ch.setAttribute("x2", x(i)); ch.setAttribute("opacity", 1);
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(stock[i])); dot.setAttribute("opacity", 1);
    const dd = i - iSel;
    showTip(ev, `<div><b>${fmtD(dates[i])}</b> <span class=dim>${dd===0?"T0":(dd>0?"T+":"T")+dd}</span></div>
      <div class=row><span><i style="background:var(--pos)"></i>${esc(sel.name)}</span><b>${nf(px.c[from+i],0)} · ${sf(stock[i]-100)}%</b></div>
      ${kospi[i]!=null?`<div class=row><span><i style="background:var(--neutral)"></i>KOSPI</span><b>${sf(kospi[i]-100)}%</b></div>`:""}`);
  });
  hit.addEventListener("pointerleave", () => { ch.setAttribute("opacity", 0); dot.setAttribute("opacity", 0); hideTip(); });
  svg.appendChild(hit);
  host.appendChild(svg);
}

/* ------------------------------------------------------------ 전체 */
function render() {
  renderStatic();
  const rows = baseRows(), S = stats(rows, state.k);
  const pw = prevWindow();
  let P = null;
  if (pw) { const pr = rowsIn(pw.start, pw.end); P = {...stats(pr, state.k), rows: pr}; }
  const kr = kospiRet(state.start, state.end);
  $("#note").innerHTML = t("note", {n:S.n, s:new Set(rows.map(s=>s.code)).size, d:DATES.filter(d=>d>=state.start&&d<=state.end).length,
    prev: pw ? fmtD(pw.start)+" ~ "+fmtD(pw.end) : t("note_noprev"), k: kr==null ? "–" : sf(kr)+"%"});
  $("#segHint").textContent = t("sec_seg_hint", {k:state.k});
  $("#dailyTitle").textContent = t("ch_daily", {k:state.k}); $("#themeTitle").textContent = t("ch_theme", {k:state.k});
  $("#dailyHint").textContent = t("ch_daily_hint"); $("#themeHint").textContent = t("ch_theme_hint");
  $("#dailyLegend").innerHTML = `<span><i style="background:var(--seq)"></i>${t("lg_count")}</span><span><i style="background:var(--pos)"></i>/<i style="background:var(--neg)"></i>${t("lg_ret")}</span>`;
  $("#ftC2").innerHTML = t("ft_c2", {u: D.meta.nUncertain});
  renderAlerts(rows, S);
  renderKpis(rows, S, P);
  renderDaily(rows);
  renderThemes(rows);
  renderSegments(rows, S);
  renderTable(rows);
  renderDrill();
}

/* ------------------------------------------------------------ 이벤트 */
$$(".seg[data-for=preset] button").forEach(b => b.onclick = () => { applyPreset(b.dataset.v); state.limit = 80; render(); });
$$(".seg[data-for=k] button").forEach(b => b.onclick = () => { state.k = b.dataset.v; render(); });
$$(".seg[data-for=kind] button").forEach(b => b.onclick = () => { state.kind = b.dataset.v; render(); });
$("#theme").onchange = e => { state.theme = e.target.value; render(); };
$("#start").onchange = e => { state.start = e.target.value; if (state.start > state.end) state.end = state.start; state.preset = "custom"; render(); };
$("#end").onchange = e => { state.end = e.target.value; if (state.end < state.start) state.start = state.end; state.preset = "custom"; render(); };
let qTimer; $("#q").oninput = e => { clearTimeout(qTimer); qTimer = setTimeout(() => { state.q = e.target.value; state.limit = 80; render(); }, 150); };
$("#reset").onclick = () => { applyPreset("20"); state.kind = "ALL"; state.theme = "ALL"; state.q = ""; state.k = "5"; state.sel = null; state.limit = 80; render(); };
$$(".tabs button").forEach(b => b.onclick = () => { state.segTab = b.dataset.v; renderSegments(baseRows(), stats(baseRows(), state.k)); });
$$(".lang button").forEach(b => b.onclick = () => { lang = b.dataset.lang; try { localStorage.setItem("themeetf.lang", lang); } catch (e) {} render(); });
let rz; addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => { renderDaily(baseRows()); renderThemes(baseRows()); renderDrill(); }, 120); });
render();
"""

_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ETF 매수 신호 이후 주가 흐름</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>__CSS__</style>
</head>
<body>
<div class="page">
  <header class="masthead">
    <div>
      <div class="tag" data-i18n="tag"></div>
      <h1 data-i18n="title"></h1>
      <div class="sub" data-i18n="sub"></div>
    </div>
    <div class="fresh">
      <div class="lang" role="radiogroup" aria-label="Language">
        <button type="button" role="radio" data-lang="ko" aria-checked="true">KO</button>
        <button type="button" role="radio" data-lang="en" aria-checked="false">EN</button>
      </div>
      <span id="status" class="status"></span>
      <div class="sub"><span data-i18n="price_end"></span> <b id="priceEnd" class="num"></b> · <span data-i18n="signal_range"></span> <b id="sigRange" class="num"></b> · <span data-i18n="generated"></span> <b id="generated" class="num"></b></div>
    </div>
  </header>

  <section class="filters" aria-label="filters">
    <div class="field"><label data-i18n="f_period"></label>
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
        <div class="seg" data-for="preset">
          <button type="button" data-v="5" data-i18n="p5"></button>
          <button type="button" data-v="10" data-i18n="p10"></button>
          <button type="button" data-v="20" data-i18n="p20"></button>
          <button type="button" data-v="all" data-i18n="pall"></button>
        </div>
        <div class="range"><select id="start" aria-label="start"></select><span class="to">→</span><select id="end" aria-label="end"></select></div>
      </div>
    </div>
    <div class="field"><label data-i18n="f_h"></label>
      <div class="seg" data-for="k">
        <button type="button" data-v="1">T+1</button><button type="button" data-v="3">T+3</button>
        <button type="button" data-v="5">T+5</button><button type="button" data-v="10">T+10</button>
      </div>
    </div>
    <div class="field"><label data-i18n="f_kind"></label>
      <div class="seg" data-for="kind">
        <button type="button" data-v="ALL" data-i18n="k_all"></button>
        <button type="button" data-v="NEW" data-i18n="k_new"></button>
        <button type="button" data-v="ADD" data-i18n="k_add"></button>
      </div>
    </div>
    <div class="field"><label data-i18n="f_theme"></label><select id="theme"></select></div>
    <div class="field"><label data-i18n="f_q"></label><input id="q" type="search" data-i18n-ph="q_ph"></div>
    <div class="field"><label>&nbsp;</label><button type="button" id="reset" class="chip-x" data-i18n="reset"></button></div>
    <div class="note" id="note"></div>
  </section>

  <section class="section" id="sec-alerts">
    <div class="rule"></div>
    <div class="section-head"><h2 data-i18n="sec_alert"></h2><span class="hint" data-i18n="sec_alert_hint"></span></div>
    <div class="alerts" id="alerts"></div>
  </section>

  <section class="section" id="sec-kpi">
    <div class="rule"></div>
    <div class="section-head"><h2 data-i18n="sec_kpi"></h2><span class="hint" data-i18n="sec_kpi_hint"></span></div>
    <div class="kpis" id="kpis"></div>
  </section>

  <section class="section" id="sec-charts">
    <div class="rule"></div>
    <div class="section-head"><h2 data-i18n="sec_charts"></h2></div>
    <div class="grid2">
      <div class="card">
        <header><h3 id="dailyTitle"></h3><span class="hint" id="dailyHint"></span></header>
        <div class="legend" id="dailyLegend"></div>
        <div class="plot" id="daily"></div>
      </div>
      <div class="card">
        <header><h3 id="themeTitle"></h3><span class="hint" id="themeHint"></span></header>
        <div class="plot" id="themes"></div>
      </div>
    </div>
  </section>

  <section class="section" id="sec-seg">
    <div class="rule"></div>
    <div class="section-head"><h2 data-i18n="sec_seg"></h2><span class="hint" id="segHint"></span></div>
    <div class="card">
      <div class="tabs">
        <button type="button" data-v="kind"></button><button type="button" data-v="multi"></button>
        <button type="button" data-v="flow"></button><button type="button" data-v="pre"></button>
        <button type="button" data-v="theme"></button>
      </div>
      <div class="tbl-wrap" id="segments"></div>
    </div>
  </section>

  <section class="section" id="sec-table">
    <div class="rule"></div>
    <div class="section-head"><h2 data-i18n="sec_table"></h2><span class="hint" data-i18n="sec_table_hint"></span></div>
    <div class="card" id="table"></div>
    <div class="drill" id="drill"></div>
  </section>

  <footer>
    <div class="cols">
      <div><h4 data-i18n="ft_method"></h4><ul><li data-i18n="ft_m1"></li><li data-i18n="ft_m2"></li><li data-i18n="ft_m3"></li></ul></div>
      <div><h4 data-i18n="ft_caveat"></h4><ul><li data-i18n="ft_c1"></li><li id="ftC2"></li><li data-i18n="ft_c3"></li></ul></div>
    </div>
  </footer>
</div>
<div id="tip" role="tooltip"></div>
<script>window.__DATA__ = @@DATA@@; window.__I18N__ = @@I18N@@;</script>
<script>__JS__</script>
</body>
</html>
"""


def _embed(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def build(out: Path = OUT) -> Path:
    payload = build_payload()
    html = (_HTML.replace("__CSS__", _CSS).replace("__JS__", _JS)
            .replace("@@DATA@@", _embed(payload)).replace("@@I18N@@", _embed(_I18N)))
    out.write_text(html, encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="사후 성과 대시보드 생성")
    ap.add_argument("-o", "--out", type=Path, default=OUT)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    out = build(args.out)
    print("대시보드: {}  ({:,.0f} KB)".format(out, out.stat().st_size / 1024))
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

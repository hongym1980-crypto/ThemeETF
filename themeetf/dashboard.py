"""자체완결형 HTML 대시보드 생성.

**일자별 원자료를 그대로 심고, 비교 계산은 브라우저에서 한다.** 덕분에 사용자가
시작일·종료일을 자유롭게 골라 그 사이 구간을 즉시 비교할 수 있다 (미리 계산해 둔
몇 개의 구간만 보는 방식이 아니다). 외부 CDN 을 쓰지 않으므로 오프라인·사내망에서도
파일 하나로 열린다.

색은 dataviz 기준을 따른다.
- 순매수(+/-)는 발산형: 국내 증시 관행대로 **유입=적색, 유출=청색**.
  (검증 통과: light #e34948/#2a78d6, dark #e66767/#3987e5 — 모든 체크 PASS)
- 회전율·비중처럼 크기만 나타내는 값은 단일 색조(블루) 한 가지.
- 테마는 색이 아니라 행 레이블로 구분한다 (9개 > 8슬롯 규칙 회피).
- 신선도 배지는 상태 팔레트(good/warning)를 점으로만 쓰고 글자는 잉크 색.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .detect import Thresholds
from .store import Store

#: 대시보드에 담을 최대 기준일 수. 원자료를 통째로 심으므로 파일 크기를 제한한다.
DEFAULT_MAX_DATES = 60


def _fmt(std_dt: str) -> str:
    if len(std_dt) == 8 and std_dt.isdigit():
        return "{}-{}-{}".format(std_dt[:4], std_dt[4:6], std_dt[6:])
    return std_dt


def build_payload(
    store: Store,
    *,
    thresholds: Thresholds | None = None,
    max_dates: int = DEFAULT_MAX_DATES,
    seibro_latest: str | None = None,
) -> dict[str, Any]:
    """일자별 구성종목 원자료 + 조회용 사전을 담은 페이로드."""
    th = thresholds or Thresholds()
    dates = store.available_dates()[:max_dates]
    if len(dates) < 2:
        raise SystemExit(
            "기준일이 2개 이상 필요합니다. `python -m themeetf daily` 를 실행하세요."
        )
    date_set = set(dates)
    marks = ",".join("?" * len(dates))

    # ---- ETF / 테마 사전 -------------------------------------------------
    etf_rows = store.query(
        "SELECT DISTINCT m.isin, m.code, m.name, m.mngco "
        "FROM etf_master m JOIN etf_theme t USING (isin) ORDER BY m.code"
    )
    etf_ix = {r["isin"]: i for i, r in enumerate(etf_rows)}
    etfs = [[r["code"], r["name"] or r["code"], r["mngco"] or ""] for r in etf_rows]

    theme_names: list[str] = []
    theme_ix: dict[str, int] = {}
    theme_etfs: list[list[int]] = []
    for r in store.query("SELECT theme, isin FROM etf_theme ORDER BY theme, isin"):
        if r["isin"] not in etf_ix:
            continue
        name = r["theme"]
        if name not in theme_ix:
            theme_ix[name] = len(theme_names)
            theme_names.append(name)
            theme_etfs.append([])
        theme_etfs[theme_ix[name]].append(etf_ix[r["isin"]])

    # ---- 일자별 보유 내역 -------------------------------------------------
    holding_rows = store.query(
        "SELECT h.isin, h.std_dt, h.stock_code, "
        "       COALESCE(h.stock_name, sm.stock_name, h.stock_code) AS stock_name, "
        "       h.weight, h.qty_per_cu, h.hold_value "
        "FROM holding h "
        "LEFT JOIN stock_market sm "
        "       ON sm.stock_code = h.stock_code AND sm.std_dt = h.std_dt "
        "WHERE h.std_dt IN ({})".format(marks),
        dates,
    )

    stock_ix: dict[str, int] = {}
    stock_name: dict[str, str] = {}
    holdings: dict[str, list[list[Any]]] = {d: [] for d in dates}
    for r in holding_rows:
        if r["std_dt"] not in date_set or r["isin"] not in etf_ix:
            continue
        code = r["stock_code"]
        if code not in stock_ix:
            stock_ix[code] = len(stock_ix)
        name = r["stock_name"]
        if name and name != code:
            stock_name.setdefault(code, name)
        holdings[r["std_dt"]].append([
            etf_ix[r["isin"]],
            stock_ix[code],
            round(r["weight"] or 0.0, 4),
            round(r["qty_per_cu"] or 0.0, 3),
            round((r["hold_value"] or 0.0) / 1e8, 3),   # 억원
        ])

    stocks = [[c, stock_name.get(c, c)]
              for c, _ in sorted(stock_ix.items(), key=lambda kv: kv[1])]

    # ---- 종목별 ETF 시장 전체 집계 (스파크라인 · 전체보유ETF 열) ----------
    market: dict[str, dict[str, list[Any]]] = {}
    for r in store.query(
        "SELECT stock_code, std_dt, etf_cnt, mngco_cnt, hold_value "
        "FROM stock_market WHERE std_dt IN ({})".format(marks), dates
    ):
        if r["stock_code"] not in stock_ix or r["std_dt"] not in date_set:
            continue
        market.setdefault(r["std_dt"], {})[str(stock_ix[r["stock_code"]])] = [
            r["etf_cnt"], round((r["hold_value"] or 0.0) / 1e8, 1), r["mngco_cnt"],
        ]

    coverage = store.query(
        "SELECT COUNT(*) AS rows_, COUNT(DISTINCT isin) AS etfs, "
        "       COUNT(DISTINCT stock_code) AS stocks "
        "FROM holding WHERE std_dt = ?", (dates[0],)
    )[0]

    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "seibro_latest": seibro_latest,
        "dates": dates,                       # 최신순
        "etfs": etfs,
        "stocks": stocks,
        "themes": theme_names,
        "themeEtfs": theme_etfs,
        "holdings": holdings,
        "market": market,
        "thresholds": {
            "qtyEps": th.qty_eps, "weightEps": th.weight_eps,
            "minWeight": th.min_weight, "etfTurnover": th.etf_turnover,
        },
        "coverage": {
            "std_dt": dates[0],
            "rows": coverage["rows_"],
            "etfs": coverage["etfs"],
            "stocks": coverage["stocks"],
            "etf_total": store.scalar("SELECT COUNT(*) FROM etf_master"),
        },
    }


# ---------------------------------------------------------------------- 렌더


_CSS = """
:root{
  color-scheme: light;
  --plane:#f9f9f7; --surface:#fcfcfb;
  --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,0.10);
  --pos:#e34948; --neg:#2a78d6;
  --seq:#2a78d6; --seq-track:#cde2fb; --seq-soft:#9ec5f4;
  --st-good:#0ca30c; --st-warn:#fab219;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme=light]){
    color-scheme: dark;
    --plane:#0d0d0d; --surface:#1a1a19;
    --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,0.10);
    --pos:#e66767; --neg:#3987e5;
    --seq:#3987e5; --seq-track:#184f95; --seq-soft:#256abf;
  }
}
:root[data-theme=dark]{
  color-scheme: dark;
  --plane:#0d0d0d; --surface:#1a1a19;
  --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,0.10);
  --pos:#e66767; --neg:#3987e5;
  --seq:#3987e5; --seq-track:#184f95; --seq-soft:#256abf;
}

*{box-sizing:border-box}
html,body{margin:0}
body{
  background:var(--plane); color:var(--ink);
  font:14px/1.5 system-ui,-apple-system,"Segoe UI","Pretendard",sans-serif;
  padding:26px 20px 80px;
}
.wrap{max-width:1240px;margin:0 auto}
h1{font-size:21px;margin:0 0 3px;letter-spacing:-.01em}
.sub{color:var(--ink2);font-size:13px}
.sub b{color:var(--ink);font-weight:600}

/* 필터 한 줄 — 전체를 스코프한다 */
.filters{
  display:flex;flex-wrap:wrap;gap:10px 12px;align-items:flex-end;
  margin:20px 0 0;padding:12px 14px;
  background:var(--surface);border:1px solid var(--ring);border-radius:12px;
}
.field{display:flex;flex-direction:column;gap:4px}
.field label{font-size:11px;color:var(--muted);letter-spacing:.02em}
select,button{
  font:13px/1.2 inherit;color:var(--ink);background:var(--surface);
  border:1px solid var(--axis);border-radius:8px;padding:7px 10px;cursor:pointer;
}
select{min-width:148px}
button:hover,select:hover{border-color:var(--seq)}
.spacer{flex:1}
.ghost{border-color:var(--ring);color:var(--ink2)}
.arrow{color:var(--muted);font-size:16px;padding:0 2px 8px}
.presets{display:flex;gap:6px;align-items:center}
.presets button{padding:6px 10px;font-size:12px}
.presets button.on{border-color:var(--seq);color:var(--seq);font-weight:650}
.span-note{flex-basis:100%;font-size:12px;color:var(--ink2);margin-top:2px}
.span-note b{color:var(--ink)}
.warn{color:var(--pos);font-weight:600}

/* 신선도 */
.fresh{display:flex;flex-wrap:wrap;gap:10px 20px;align-items:center;
  border:1px solid var(--ring);border-radius:12px;padding:12px 15px;margin:12px 0 0;
  background:var(--surface)}
.fresh .item{display:flex;flex-direction:column;gap:2px}
.fresh .k{font-size:11px;color:var(--muted);letter-spacing:.02em}
.fresh .v{font-size:15px;font-weight:650;font-variant-numeric:tabular-nums}
.fresh .badge{margin-left:auto;display:inline-flex;align-items:center;gap:7px;
  padding:5px 13px;border-radius:20px;font-size:12px;font-weight:650;
  border:1px solid var(--ring);color:var(--ink)}
.fresh .badge::before{content:"";width:9px;height:9px;border-radius:50%}
.fresh .ok::before{background:var(--st-good)}
.fresh .stale::before{background:var(--st-warn)}
.fresh .how{flex-basis:100%;font-size:12px;color:var(--ink2);margin-top:2px}
.fresh code{font-size:11.5px;padding:1px 5px;border-radius:4px;
  background:color-mix(in srgb,var(--muted) 18%,transparent)}

/* 스탯 */
.hero{background:var(--surface);border:1px solid var(--ring);border-radius:12px;
  padding:18px 20px;margin:12px 0}
.hero .k{font-size:11.5px;color:var(--muted);letter-spacing:.03em}
.hero .v{font-size:48px;line-height:1.05;font-weight:600;margin:4px 0 2px}
.hero .n{font-size:12.5px;color:var(--ink2)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(146px,1fr));gap:12px}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:13px 15px}
.tile .k{font-size:11px;color:var(--muted);letter-spacing:.02em}
.tile .v{font-size:22px;font-weight:600;margin-top:3px}
.tile .n{font-size:11.5px;color:var(--ink2);margin-top:2px}

/* 카드 */
.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px;
  padding:16px 18px 14px;margin-top:16px}
.card > header{display:flex;align-items:baseline;gap:10px;margin-bottom:4px}
.card h2{font-size:14.5px;margin:0;font-weight:650}
.card .hint{font-size:11.5px;color:var(--muted)}
.card .toggle{margin-left:auto}
.card .toggle button{padding:4px 9px;font-size:11.5px}
.plot{overflow-x:auto}
svg{display:block;max-width:100%}
.axis-line{stroke:var(--axis);stroke-width:1;shape-rendering:crispEdges}
.grid-line{stroke:var(--grid);stroke-width:1;shape-rendering:crispEdges}
.tick{fill:var(--muted);font-size:10.5px;font-variant-numeric:tabular-nums}
.cat{fill:var(--ink2);font-size:12px}
.val{fill:var(--ink2);font-size:11.5px;font-variant-numeric:tabular-nums}
.hit{fill:transparent;cursor:pointer}

/* 표 */
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{padding:6px 10px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--grid);
      font-variant-numeric:tabular-nums}
th{color:var(--muted);font-size:11px;font-weight:600;letter-spacing:.02em;
   text-align:right;position:sticky;top:0;background:var(--surface)}
td.t,th.t{text-align:left;font-variant-numeric:normal}
tbody tr:last-child td{border-bottom:none}
tbody tr.clickable{cursor:pointer}
tbody tr.clickable:hover{background:color-mix(in srgb,var(--seq) 8%,transparent)}
tbody tr.sel{background:color-mix(in srgb,var(--seq) 14%,transparent)}
.pos{color:var(--pos);font-weight:600}
.neg{color:var(--neg);font-weight:600}
.meter{display:inline-block;position:relative;width:76px;height:7px;border-radius:4px;
       background:var(--seq-track);vertical-align:middle}
.meter::after{content:"";position:absolute;left:50%;top:-1px;bottom:-1px;width:1px;
              background:var(--axis)}
.meter i{position:absolute;top:0;height:100%;border-radius:4px}
.meter i.p{left:50%;background:var(--pos)}
.meter i.n{right:50%;background:var(--neg)}
.chip{display:inline-flex;align-items:center;gap:4px;padding:1px 7px;border-radius:20px;
      font-size:11px;border:1px solid var(--ring);color:var(--ink2)}
.chip::before{content:"";width:7px;height:7px;border-radius:2px;background:var(--muted)}
.chip.NEW::before,.chip.ADD::before{background:var(--pos)}
.chip.DROP::before,.chip.TRIM::before{background:var(--neg)}
.chip.NEW,.chip.DROP{font-weight:650;color:var(--ink)}

/* 이벤트 카드 필터 바 */
.evbar{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:flex-end;
  padding:10px 0 12px;margin-bottom:6px;border-bottom:1px solid var(--grid)}
.evbar select{min-width:120px;max-width:260px}
.evbar input[type=search]{font:13px/1.2 inherit;color:var(--ink);background:var(--surface);
  border:1px solid var(--axis);border-radius:8px;padding:7px 10px;min-width:190px}
.evbar input[type=search]:hover,.evbar input[type=search]:focus{border-color:var(--seq);outline:none}
.evbar .presets button{padding:4px 8px}
.evbar .presets button:not(.on){opacity:.45}
.evbar .presets button:not(.on):hover{opacity:.8}
.evbar .presets button .chip{border:none;padding:0;font-size:11.5px}

/* 드릴다운 */
.drill{margin-top:12px;padding-top:12px;border-top:1px solid var(--grid)}
.drill .title{font-size:13px;font-weight:650;margin-bottom:2px}
.drill .meta{font-size:11.5px;color:var(--muted);margin-bottom:10px}
.empty{color:var(--muted);font-size:12.5px;padding:14px 0}

/* 툴팁 */
#tip{position:fixed;z-index:50;pointer-events:none;opacity:0;transition:opacity .08s;
  background:var(--surface);color:var(--ink);border:1px solid var(--axis);
  border-radius:8px;padding:7px 10px;font-size:12px;max-width:290px;
  box-shadow:0 6px 20px rgba(0,0,0,.14)}
#tip b{font-weight:650}
#tip .r{color:var(--ink2);font-variant-numeric:tabular-nums}
footer{margin-top:36px;padding-top:14px;border-top:1px solid var(--grid);
       color:var(--muted);font-size:11.5px}
.legend{display:flex;gap:14px;align-items:center;font-size:11.5px;color:var(--ink2);margin:2px 0 8px}
.legend span{display:inline-flex;align-items:center;gap:5px}
.legend i{width:9px;height:9px;border-radius:2px;display:inline-block}
"""


_JS = r"""
const D = window.__DATA__;
const TH = D.thresholds;
const $ = (s, r=document) => r.querySelector(s);
const el = (t, c) => { const n = document.createElement(t); if (c) n.className = c; return n; };
const NS = "http://www.w3.org/2000/svg";
const svgEl = (t, a={}) => { const n = document.createElementNS(NS, t);
  for (const k in a) n.setAttribute(k, a[k]); return n; };

/* D.dates 는 최신순. end = 종료일(기준), start = 시작일(과거). */
const state = {
  end: D.dates[0],
  start: D.dates[1],
  theme: "__ALL__",
  sel: null,
  preset: "prev",
  /* 구성종목 변경 이벤트 카드 전용 필터 (기간·테마 필터 아래에서 다시 좁힌다) */
  ev: { kinds: new Set(["NEW", "DROP", "ADD", "TRIM"]), q: "", mngco: "__ALL__",
        etf: "__ALL__", minFlow: 0 },
};
const tip = $("#tip");

const nf = (v, d=1) => (v==null||isNaN(v)) ? "-" :
  v.toLocaleString("ko-KR", {minimumFractionDigits:d, maximumFractionDigits:d});
const sf = (v, d=1) => (v==null||isNaN(v)) ? "-" : (v>0?"+":"") + nf(v, d);
const cls = v => v > 0 ? "pos" : (v < 0 ? "neg" : "");
const KIND = {NEW:"신규편입", DROP:"편출", ADD:"비중확대", TRIM:"비중축소"};
const fmtDate = d => (d && d.length === 8)
  ? `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6)}` : (d || "-");

/* ETF 인덱스 -> 소속 테마 인덱스들 */
const etfThemes = D.etfs.map(() => []);
D.themeEtfs.forEach((list, t) => list.forEach(e => etfThemes[e].push(t)));

/* ================================================================ 비교 계산
   detect.py 와 같은 정의를 브라우저에서 그대로 수행한다. 원자료가 심어져 있으므로
   어떤 두 기준일이든 즉시 계산된다. */

function indexOf(dt) {
  const m = new Map();
  for (const r of (D.holdings[dt] || [])) m.set(r[0] * 100000 + r[1], r);
  return m;
}

function makeDiff(t, e, s, c, p) {
  const w = c ? c[2] : null, pw = p ? p[2] : null;
  const q = c ? c[3] : null, pq = p ? p[3] : null;
  const v = c ? c[4] : null, pv = p ? p[4] : null;
  const ratio = (pq && q != null) ? (q / pq - 1) : null;

  let dwQty, flow;                       // 가격효과를 뺀 순수 리밸런싱 신호
  if (pw == null)      { dwQty = w || 0;     flow = v || 0; }
  else if (w == null)  { dwQty = -(pw || 0); flow = -(pv || 0); }
  else { dwQty = ratio == null ? 0 : pw * ratio;
         flow  = ratio == null ? 0 : (pv || 0) * ratio; }

  let kind = null;
  if (pw == null && (w || 0) >= TH.minWeight) kind = "NEW";
  else if (w == null && (pw || 0) >= TH.minWeight) kind = "DROP";
  else if (ratio != null && Math.abs(ratio) >= TH.qtyEps
           && Math.abs(dwQty) >= TH.weightEps) kind = ratio > 0 ? "ADD" : "TRIM";

  return {t, e, s, w, pw, q, pq, v, pv, ratio, dwQty, flow, kind};
}

function computeWindow(endDt, startDt) {
  const curr = indexOf(endDt), prev = indexOf(startDt);
  const currE = new Set(), prevE = new Set();
  for (const r of curr.values()) currE.add(r[0]);
  for (const r of prev.values()) prevE.add(r[0]);
  const both = new Set([...currE].filter(e => prevE.has(e)));
  const skipped = new Set([...currE, ...prevE].filter(e => !both.has(e)));

  const keys = new Set([...curr.keys(), ...prev.keys()]);
  const diffs = [];
  for (const k of keys) {
    const c = curr.get(k), p = prev.get(k);
    const row = c || p;
    const e = row[0], s = row[1];
    if (!both.has(e)) continue;
    for (const t of etfThemes[e]) diffs.push(makeDiff(t, e, s, c, p));
  }
  return {endDt, startDt, diffs, skipped: skipped.size};
}

function themeFlow(diffs) {
  const acc = new Map();
  for (const d of diffs) {
    let a = acc.get(d.t);
    if (!a) acc.set(d.t, a = {t:d.t, etfs:new Set(), stocks:new Set(),
                              turnover:0, net:0, gross:0});
    a.etfs.add(d.e); a.stocks.add(d.s);
    a.turnover += Math.abs(d.dwQty);
    a.net += d.flow; a.gross += Math.abs(d.flow);
  }
  return [...acc.values()].map(a => ({
    theme: D.themes[a.t], etf_cnt: a.etfs.size, stock_cnt: a.stocks.size,
    turnover: +a.turnover.toFixed(2), net: +a.net.toFixed(1), gross: +a.gross.toFixed(1),
  }));
}

/* 주도주 스코어 — detect.py LeaderRow.score 와 같은 가중합 */
function leaders(diffs, endDt) {
  const acc = new Map();
  for (const d of diffs) {
    const key = d.t * 100000 + d.s;
    let a = acc.get(key);
    if (!a) acc.set(key, a = {t:d.t, s:d.s, conviction:0, flow:0, up:0, down:0,
                              nw:0, dp:0, mngco:new Set(), weights:[], etfCnt:0,
                              rows:[]});
    a.conviction += d.dwQty; a.flow += d.flow;
    if (d.w != null) { a.weights.push(d.w); a.etfCnt++; }
    a.rows.push(d);
    if (d.kind === "NEW") a.nw++;
    if (d.kind === "DROP") a.dp++;
    if (d.kind === "NEW" || d.kind === "ADD") { a.up++; a.mngco.add(D.etfs[d.e][2]); }
    else if (d.kind === "DROP" || d.kind === "TRIM") a.down++;
  }
  const mkt = D.market[endDt] || {};
  const out = [];
  for (const a of acc.values()) {
    if (!a.up && !a.down) continue;
    const flowTerm = Math.sign(a.flow) * Math.log10(1 + Math.abs(a.flow));
    const score = 2*a.conviction + 2*a.nw - 2*a.dp
                + 0.6*(a.up - a.down) + 0.5*a.mngco.size + 1.5*flowTerm;
    const m = mkt[String(a.s)];
    out.push({
      t:a.t, s:a.s, theme:D.themes[a.t],
      code:D.stocks[a.s][0], name:D.stocks[a.s][1],
      score:+score.toFixed(3), conviction:+a.conviction.toFixed(3),
      flow:+a.flow.toFixed(2), up:a.up, down:a.down, mngco:a.mngco.size,
      new:a.nw, drop:a.dp, etfCnt:a.etfCnt,
      avgWeight: a.weights.length ? a.weights.reduce((x,y)=>x+y,0)/a.weights.length : 0,
      marketEtfCnt: m ? m[0] : null,
      rows:a.rows,
    });
  }
  out.sort((x, y) => y.score - x.score);
  return out;
}

function etfRebalances(diffs) {
  const acc = new Map();
  for (const d of diffs) {
    if (!d.kind) continue;
    const key = d.t * 1000 + d.e;
    let a = acc.get(key);
    if (!a) acc.set(key, a = {t:d.t, e:d.e, turnover:0, gross:0, nw:0, dp:0, ad:0, tr:0});
    a.turnover += Math.abs(d.dwQty); a.gross += Math.abs(d.flow);
    if (d.kind === "NEW") a.nw++; else if (d.kind === "DROP") a.dp++;
    else if (d.kind === "ADD") a.ad++; else a.tr++;
  }
  return [...acc.values()]
    .filter(a => a.turnover > 0)
    .map(a => ({
      theme:D.themes[a.t], etf_code:D.etfs[a.e][0], etf_name:D.etfs[a.e][1],
      mngco:D.etfs[a.e][2], turnover:+a.turnover.toFixed(3),
      new:a.nw, drop:a.dp, add:a.ad, trim:a.tr, gross:+a.gross.toFixed(1),
    }))
    .sort((x, y) => y.turnover - x.turnover);
}

function eventList(diffs) {
  return diffs.filter(d => d.kind).map(d => ({
    theme:D.themes[d.t], code:D.stocks[d.s][0], name:D.stocks[d.s][1],
    etf_code:D.etfs[d.e][0], etf_name:D.etfs[d.e][1], mngco:D.etfs[d.e][2],
    kind:d.kind, w:d.w, pw:d.pw,
    dqty:(d.ratio == null ? null : d.ratio * 100), flow:d.flow,
  })).sort((a, b) => Math.abs(b.flow) - Math.abs(a.flow));
}

/* "전체 테마" 합계는 (ETF, 종목) 중복을 제거한다.
   한 ETF 가 두 테마에 속하면 단순 합산은 이중계상이 되기 때문이다. */
function totals(diffs) {
  const seen = new Set();
  let net = 0, gross = 0, turnover = 0, pairs = 0;
  const ev = new Set(), nw = new Set(), dp = new Set(), rb = new Set();
  for (const d of diffs) {
    const key = d.e * 100000 + d.s;
    if (seen.has(key)) continue;
    seen.add(key); pairs++;
    net += d.flow; gross += Math.abs(d.flow); turnover += Math.abs(d.dwQty);
    if (d.kind) { ev.add(key + ":" + d.kind); rb.add(d.e); }
    if (d.kind === "NEW") nw.add(key);
    if (d.kind === "DROP") dp.add(key);
  }
  return {net:+net.toFixed(1), gross:+gross.toFixed(1),
          turnover:+turnover.toFixed(2), events:ev.size,
          new:nw.size, drop:dp.size, rebalances:rb.size, pairs};
}

/* ---------------------------------------------------------------- 툴팁 */
function showTip(ev, html) {
  tip.innerHTML = html; tip.style.opacity = 1;
  const r = tip.getBoundingClientRect();
  let x = ev.clientX + 14, y = ev.clientY + 14;
  if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - 12;
  if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - 12;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
const hideTip = () => { tip.style.opacity = 0; };

function attachTip(node, html) {
  node.addEventListener("mousemove", e => showTip(e, html));
  node.addEventListener("mouseleave", hideTip);
  node.setAttribute("tabindex", "0");
  node.addEventListener("focus", () => {
    const b = node.getBoundingClientRect();
    showTip({clientX: b.right, clientY: b.top}, html);
  });
  node.addEventListener("blur", hideTip);
}

/* ------------------------------------------ 막대 경로 (데이터끝 4px 라운드) */
function hBar(x0, y, w, h, r=4) {
  const rr = Math.min(r, Math.abs(w) / 2, h / 2);
  const x1 = x0 + w;
  if (w >= 0) {
    return `M${x0},${y} H${x1-rr} Q${x1},${y} ${x1},${y+rr} V${y+h-rr}`
         + ` Q${x1},${y+h} ${x1-rr},${y+h} H${x0} Z`;
  }
  return `M${x0},${y} H${x1+rr} Q${x1},${y} ${x1},${y+rr} V${y+h-rr}`
       + ` Q${x1},${y+h} ${x1+rr},${y+h} H${x0} Z`;
}

/* 눈금은 1 / 2 / 2.5 / 5 배수로 떨어지게 잡는다 */
function niceScale(maxAbs, targetTicks=4) {
  if (!maxAbs || !isFinite(maxAbs)) return {max:1, step:0.25};
  const raw = maxAbs / targetTicks;
  const e = Math.pow(10, Math.floor(Math.log10(raw)));
  const m = raw / e;
  const step = (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * e;
  return {max: Math.ceil(maxAbs / step) * step, step};
}
const tickDec = step => step >= 10 ? 0 : (step >= 1 ? (step % 1 ? 1 : 0) : 2);

/* ------------------------------------------------------- 발산형 가로 막대 */
function divergingBars(host, rows, opt) {
  const ROW = 30, BAR = 18, GUT = opt.gutter || 132, PAD_R = 78, AX = 24;
  const W = Math.max(560, host.clientWidth || 700);
  const H = rows.length * ROW + AX + 10;
  const plotW = W - GUT - PAD_R;
  const maxAbs = Math.max(...rows.map(r => Math.abs(opt.value(r))), 1e-4);
  const {max, step} = niceScale(maxAbs);
  const dec = tickDec(step), vdec = maxAbs < 10 ? 2 : 1;
  const zero = GUT + plotW / 2;
  const scale = v => (v / max) * (plotW / 2);
  const bottom = rows.length * ROW + 4;

  const svg = svgEl("svg", {width:W, height:H, role:"img", "aria-label":opt.aria||""});
  for (let t = step; t <= max + 1e-9; t += step) {
    [-t, t].forEach(v => {
      const x = zero + scale(v);
      svg.appendChild(svgEl("line", {class:"grid-line", x1:x, x2:x, y1:4, y2:bottom}));
      const lb = svgEl("text", {class:"tick", x, y:H-8, "text-anchor":"middle"});
      lb.textContent = sf(v, dec); svg.appendChild(lb);
    });
  }
  svg.appendChild(svgEl("line", {class:"axis-line", x1:zero, x2:zero, y1:4, y2:bottom}));
  const z = svgEl("text", {class:"tick", x:zero, y:H-8, "text-anchor":"middle"});
  z.textContent = "0"; svg.appendChild(z);

  rows.forEach((r, i) => {
    const v = opt.value(r);
    const y = i * ROW + (ROW - BAR) / 2 + 4;
    const w = scale(v);
    const g = svgEl("g");
    if (Math.abs(w) >= 0.6) {
      g.appendChild(svgEl("path", {d:hBar(zero, y, w, BAR),
        fill: v >= 0 ? "var(--pos)" : "var(--neg)"}));
    }
    const name = svgEl("text", {class:"cat", x:GUT-10, y:y+BAR/2+4, "text-anchor":"end"});
    name.textContent = opt.label(r);
    if (v === 0) name.setAttribute("fill", "var(--muted)");
    g.appendChild(name);

    const lab = svgEl("text", {class:"val", y:y+BAR/2+4});
    if (v === 0) {
      lab.setAttribute("x", zero + 7); lab.setAttribute("text-anchor", "start");
      lab.setAttribute("fill", "var(--muted)"); lab.textContent = "무변동";
    } else {
      lab.setAttribute("x", v >= 0 ? zero + w + 7 : zero + w - 7);
      lab.setAttribute("text-anchor", v >= 0 ? "start" : "end");
      lab.textContent = sf(v, vdec);
    }
    g.appendChild(lab);

    const hit = svgEl("rect", {class:"hit", x:0, y:i*ROW+4, width:W, height:ROW});
    attachTip(hit, opt.tip(r)); g.appendChild(hit);
    svg.appendChild(g);
  });
  host.appendChild(svg);
}

/* ------------------------------------------------------ 단일 색조 가로 막대 */
function magnitudeBars(host, rows, opt) {
  const ROW = 28, BAR = 16, GUT = opt.gutter || 132, PAD_R = 74, AX = 24;
  const W = Math.max(560, host.clientWidth || 700);
  const H = rows.length * ROW + AX + 10;
  const plotW = W - GUT - PAD_R;
  const {max, step} = niceScale(Math.max(...rows.map(r => opt.value(r)), 1e-4));
  const dec = tickDec(step);
  const scale = v => (v / max) * plotW;
  const bottom = rows.length * ROW + 4;

  const svg = svgEl("svg", {width:W, height:H, role:"img", "aria-label":opt.aria||""});
  for (let t = step; t <= max + 1e-9; t += step) {
    const x = GUT + scale(t);
    svg.appendChild(svgEl("line", {class:"grid-line", x1:x, x2:x, y1:4, y2:bottom}));
    const lb = svgEl("text", {class:"tick", x, y:H-8, "text-anchor":"middle"});
    lb.textContent = nf(t, dec); svg.appendChild(lb);
  }
  svg.appendChild(svgEl("line", {class:"axis-line", x1:GUT, x2:GUT, y1:4, y2:bottom}));

  rows.forEach((r, i) => {
    const v = opt.value(r);
    const y = i * ROW + (ROW - BAR) / 2 + 4;
    const g = svgEl("g");
    if (scale(v) >= 0.6) {
      g.appendChild(svgEl("path", {d:hBar(GUT, y, scale(v), BAR), fill:"var(--seq)"}));
    }
    const name = svgEl("text", {class:"cat", x:GUT-10, y:y+BAR/2+4, "text-anchor":"end"});
    name.textContent = opt.label(r);
    if (!v) name.setAttribute("fill", "var(--muted)");
    g.appendChild(name);
    const lab = svgEl("text", {class:"val", x:GUT+scale(v)+7, y:y+BAR/2+4});
    if (!v) { lab.setAttribute("fill","var(--muted)"); lab.textContent = "무변동"; }
    else lab.textContent = nf(v, 2);
    g.appendChild(lab);
    const hit = svgEl("rect", {class:"hit", x:0, y:i*ROW+4, width:W, height:ROW});
    attachTip(hit, opt.tip(r)); g.appendChild(hit);
    svg.appendChild(g);
  });
  host.appendChild(svg);
}

/* ------------------------------------------------------------- 스파크라인 */
function sparkline(sIdx, w=76, h=20) {
  const asc = [...D.dates].reverse();
  const vals = asc.map(dt => {
    const m = D.market[dt], r = m && m[String(sIdx)];
    return r ? r[1] : null;
  });
  const pts = vals.map((v, i) => [i, v]).filter(p => p[1] != null);
  const svg = svgEl("svg", {width:w, height:h});
  if (pts.length < 2) return svg;
  const ys = pts.map(p => p[1]);
  const lo = Math.min(...ys), hi = Math.max(...ys), span = (hi - lo) || 1;
  const X = i => 3 + (i / Math.max(1, vals.length - 1)) * (w - 10);
  const Y = v => h - 4 - ((v - lo) / span) * (h - 9);
  const d = pts.map((p, k) => (k ? "L" : "M") + X(p[0]) + "," + Y(p[1])).join(" ");
  svg.appendChild(svgEl("path", {d, fill:"none", stroke:"var(--seq)",
    "stroke-width":2, "stroke-linecap":"round", "stroke-linejoin":"round"}));
  const last = pts[pts.length - 1];
  svg.appendChild(svgEl("circle", {cx:X(last[0]), cy:Y(last[1]), r:4,
    fill:"var(--seq)", stroke:"var(--surface)", "stroke-width":2}));
  const body = pts.map(p => fmtDate(asc[p[0]]) + " " + nf(p[1], 0) + "억").join("<br>");
  attachTip(svg, "<b>ETF 시장 전체 보유금액</b><br><span class='r'>" + body + "</span>");
  return svg;
}

/* ------------------------------------------------------------------ 표 */
function table(headers, rows, opt={}) {
  const t = el("table"), th = el("thead"), tr = el("tr");
  headers.forEach(h => {
    const c = el("th"); c.textContent = h.label != null ? h.label : h;
    if (h.t) c.className = "t"; tr.appendChild(c);
  });
  th.appendChild(tr); t.appendChild(th);
  const tb = el("tbody");
  rows.forEach(r => {
    const row = el("tr", opt.rowClass ? opt.rowClass(r) : "");
    r.cells.forEach(c => {
      const td = el("td", c.cls || "");
      if (c.node) td.appendChild(c.node);
      else if (c.html != null) td.innerHTML = c.html;
      else td.textContent = c.text;
      if (c.t) td.classList.add("t");
      row.appendChild(td);
    });
    if (opt.onClick) row.addEventListener("click", () => opt.onClick(r.ref));
    tb.appendChild(row);
  });
  t.appendChild(tb);
  const box = el("div", "plot"); box.appendChild(t);
  return box;
}

/* --------------------------------------------------------------- 카드 셸 */
function card(title, hint, renderChart, renderTable) {
  const c = el("div", "card");
  const head = el("header");
  const h = el("h2"); h.textContent = title; head.appendChild(h);
  if (hint) { const s = el("span", "hint"); s.textContent = hint; head.appendChild(s); }
  const tg = el("span", "toggle");
  const bChart = el("button"), bTable = el("button", "ghost");
  bChart.textContent = "차트"; bTable.textContent = "표";
  tg.append(bChart, bTable); head.appendChild(tg);
  c.appendChild(head);

  const body = el("div");
  c.appendChild(body);
  let mode = renderChart ? "chart" : "table";
  const draw = () => {
    body.innerHTML = "";
    if (mode === "chart" && renderChart) {
      const host = el("div", "plot"); body.appendChild(host); renderChart(host);
    } else body.appendChild(renderTable());
    bChart.className = mode === "chart" ? "" : "ghost";
    bTable.className = mode === "table" ? "" : "ghost";
  };
  bChart.onclick = () => { mode = "chart"; draw(); };
  bTable.onclick = () => { mode = "table"; draw(); };
  if (!renderChart) tg.remove();
  draw();
  return c;
}

function legend(items) {
  const l = el("div", "legend");
  items.forEach(([color, label]) => {
    const s = el("span");
    const i = el("i"); i.style.background = color;
    s.append(i, document.createTextNode(" " + label)); l.appendChild(s);
  });
  return l;
}

function meterHTML(v, max) {
  const pct = Math.min(50, Math.abs(v) / (max || 1) * 50);
  return `<span class="meter"><i class="${v>=0?"p":"n"}" `
       + `style="width:${pct.toFixed(1)}%"></i></span>`;
}

/* ----------------------------------------------------- 데이터 신선도 배너 */
function renderFreshness() {
  const box = $("#fresh");
  const mine = D.dates[0], seibro = D.seibro_latest;
  const item = (k, v) =>
    `<div class="item"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  let html = item("내 데이터 최신 기준일", fmtDate(mine));
  if (seibro) {
    const ok = mine === seibro;
    html += item("SEIBro 최신 기준일", fmtDate(seibro));
    html += ok ? '<span class="badge ok">최신 상태</span>'
               : '<span class="badge stale">갱신 필요 — [1] 최신 데이터 받기</span>';
    if (!ok) html += '<div class="how">SEIBro 에 더 최신 기준일이 있습니다. '
      + '<b>ThemeETF.bat</b> 을 열고 <b>[1] 최신 데이터 받기</b> 를 실행하면 '
      + '빠진 영업일까지 한 번에 채워집니다.</div>';
  } else {
    html += item("SEIBro 최신 기준일", "확인 안 함");
    html += '<div class="how">이 파일은 저장된 데이터만으로 만들어졌습니다. '
      + '최신 여부까지 보려면 온라인 상태에서 '
      + '<code>python -m themeetf dashboard</code> 를 실행하세요.</div>';
  }
  box.innerHTML = html;
}

/* --------------------------------------------------------------- 렌더링 */
function tradingDaysBetween(startDt, endDt) {
  const i = D.dates.indexOf(endDt), j = D.dates.indexOf(startDt);
  return (i >= 0 && j >= 0) ? j - i : null;
}

function render() {
  const W = computeWindow(state.end, state.start);
  const all = state.theme === "__ALL__";
  const tIdx = all ? -1 : D.themes.indexOf(state.theme);
  const diffs = all ? W.diffs : W.diffs.filter(d => d.t === tIdx);

  const flows = themeFlow(diffs);
  const leadersAll = leaders(diffs, state.end);
  const rebal = etfRebalances(diffs);
  const evs = eventList(diffs);
  const T = totals(diffs);

  const net = all ? T.net : flows.reduce((a, f) => a + f.net, 0);
  const gross = all ? T.gross : flows.reduce((a, f) => a + f.gross, 0);
  const turn = all ? T.turnover : flows.reduce((a, f) => a + f.turnover, 0);
  const spanDays = tradingDaysBetween(state.start, state.end);

  $("#span-note").innerHTML =
    `비교 기간 <b>${fmtDate(state.start)}</b> &rarr; <b>${fmtDate(state.end)}</b>`
    + (spanDays != null ? ` &middot; 영업일 <b>${spanDays}</b>일 간격` : "")
    + ` &middot; 보유 기준일 ${D.dates.length}개 중 선택`
    + (W.skipped ? ` &middot; <span class="warn">한쪽 날짜에만 있는 ETF `
                   + `${W.skipped}종목 제외</span>` : "");

  $("#hero-v").textContent = sf(net, 1) + "억";
  $("#hero-v").className = "v " + cls(net);
  $("#hero-n").textContent =
    `${fmtDate(state.start)} → ${fmtDate(state.end)}`
    + ` · ${all ? "전체 테마 (ETF·종목 중복 제거)" : state.theme}`
    + ` · 1CU 수량 변화 기준 추정 순매수`;

  const tiles = [
    ["감지 이벤트", (all ? T.events : evs.length).toLocaleString(),
     `신규 ${all ? T.new : evs.filter(e=>e.kind==="NEW").length}`
     + ` · 편출 ${all ? T.drop : evs.filter(e=>e.kind==="DROP").length}`],
    ["리밸런싱 ETF", (all ? T.rebalances : rebal.length).toLocaleString(),
     `추적 ${(all ? T.pairs : diffs.length).toLocaleString()} 페어`],
    ["주도주 후보", leadersAll.length.toLocaleString(),
     `상위 ${Math.min(20, leadersAll.length)}개 표시`],
    ["총매매 규모", nf(gross, 0) + "억", `회전율 합 ${nf(turn, 1)}pp`],
    ["추적 커버리지", `${D.coverage.etfs} ETF`,
     `${D.coverage.stocks}종목 / 전체 ${D.coverage.etf_total} ETF`],
  ];
  const tw = $("#tiles"); tw.innerHTML = "";
  tiles.forEach(([k, v, n]) => {
    const t = el("div", "tile");
    const a = el("div", "k"); a.textContent = k;
    const b = el("div", "v"); b.textContent = v;
    const c = el("div", "n"); c.textContent = n;
    t.append(a, b, c); tw.appendChild(t);
  });

  const main = $("#cards"); main.innerHTML = "";

  /* 1. 테마별 추정 순매수 (발산형) */
  const fSorted = [...flows].sort((a, b) => b.net - a.net);
  main.appendChild(card("테마별 추정 순매수", "억원 · 유입(적) / 유출(청)",
    host => {
      host.appendChild(legend([["var(--pos)","순유입"], ["var(--neg)","순유출"]]));
      divergingBars(host, fSorted, {
        value: r => r.net, label: r => r.theme,
        aria: "테마별 추정 순매수 발산형 막대차트",
        tip: r => `<b>${r.theme}</b><br><span class="r">추정 순매수 ${sf(r.net,1)}억`
          + `<br>총매매 ${nf(r.gross,1)}억<br>회전율 ${nf(r.turnover,2)}pp`
          + `<br>ETF ${r.etf_cnt} · 종목 ${r.stock_cnt}</span>`,
      });
    },
    () => table([{label:"테마",t:1},"ETF","종목","회전율(pp)","순매수(억)","총매매(억)"],
      fSorted.map(r => ({cells:[
        {text:r.theme,t:1},{text:r.etf_cnt},{text:r.stock_cnt},
        {text:nf(r.turnover,2)},{text:sf(r.net,1),cls:cls(r.net)},
        {text:nf(r.gross,1)}]})))
  ));

  /* 2. 테마별 회전율 (단일 색조) */
  const tSorted = [...flows].sort((a, b) => b.turnover - a.turnover);
  main.appendChild(card("테마별 리밸런싱 회전율", "pp · 수량기여 비중변화의 절대값 합",
    host => magnitudeBars(host, tSorted, {
      value: r => r.turnover, label: r => r.theme,
      aria: "테마별 회전율 막대차트",
      tip: r => `<b>${r.theme}</b><br><span class="r">회전율 ${nf(r.turnover,2)}pp`
        + `<br>ETF ${r.etf_cnt} · 종목 ${r.stock_cnt}</span>`,
    }),
    () => table([{label:"테마",t:1},"회전율(pp)","ETF","종목"],
      tSorted.map(r => ({cells:[{text:r.theme,t:1},{text:nf(r.turnover,2)},
        {text:r.etf_cnt},{text:r.stock_cnt}]})))
  ));

  /* 3. 주도주 후보 */
  const top = leadersAll.slice(0, 20);
  const maxScore = Math.max(...top.map(r => Math.abs(r.score)), 1);
  const c3 = card("주도주 후보 — 운용사가 집중 편입한 종목",
    "행을 클릭하면 ETF별 내역", null,
    () => {
      if (!top.length) { const e = el("div","empty");
        e.textContent = "이 기간에 감지된 편입 변화가 없습니다."; return e; }
      return table(
        [{label:"#"},{label:"테마",t:1},{label:"코드",t:1},{label:"종목명",t:1},
         "스코어","수량기여(pp)","순매수(억)","증/감","운용사","신규","편출",
         "평균비중(%)","전체ETF",{label:"보유금액 추이",t:1}],
        top.map((r, i) => ({
          ref: r,
          cells: [
            {text:i+1}, {text:r.theme,t:1}, {text:r.code,t:1}, {text:r.name,t:1},
            {html: meterHTML(r.score, maxScore) + " " + nf(r.score,2), cls:cls(r.score)},
            {text:sf(r.conviction,2), cls:cls(r.conviction)},
            {text:sf(r.flow,1), cls:cls(r.flow)},
            {text:(r.up||0) + " / " + (r.down||0)}, {text:r.mngco||"·"},
            {text:r.new||"·"}, {text:r.drop||"·"},
            {text:nf(r.avgWeight,2)},
            {text:r.marketEtfCnt == null ? "-" : r.marketEtfCnt},
            {node: sparkline(r.s), t:1},
          ],
        })),
        {onClick: r => { state.sel = r.t + "|" + r.s; render(); },
         rowClass: r => "clickable" + (state.sel === r.t + "|" + r.s ? " sel" : "")}
      );
    });
  const drill = drillPanel(top);
  if (drill) c3.appendChild(drill);
  main.appendChild(c3);

  /* 4. 리밸런싱 ETF */
  main.appendChild(card("리밸런싱 발생 ETF", "회전율 상위",
    rebal.length ? host => magnitudeBars(host, rebal.slice(0, 14), {
      gutter: 210, value: r => r.turnover, label: r => r.etf_name,
      aria: "ETF별 리밸런싱 회전율 막대차트",
      tip: r => `<b>${r.etf_name}</b> <span class="r">${r.etf_code}</span><br>`
        + `<span class="r">${r.mngco} · ${r.theme}<br>회전율 ${nf(r.turnover,2)}pp`
        + `<br>신규 ${r.new} · 편출 ${r.drop} · 확대 ${r.add} · 축소 ${r.trim}`
        + `<br>총매매 ${nf(r.gross,0)}억</span>`,
    }) : null,
    () => rebal.length ? table(
      [{label:"테마",t:1},{label:"코드",t:1},{label:"ETF",t:1},{label:"운용사",t:1},
       "회전율(pp)","신규","편출","확대","축소","총매매(억)"],
      rebal.map(r => ({cells:[{text:r.theme,t:1},{text:r.etf_code,t:1},
        {text:r.etf_name,t:1},{text:r.mngco,t:1},{text:nf(r.turnover,2)},
        {text:r.new||"·"},{text:r.drop||"·"},{text:r.add||"·"},{text:r.trim||"·"},
        {text:nf(r.gross,0)}]})))
      : (() => { const e = el("div","empty");
          e.textContent = "리밸런싱이 감지된 ETF가 없습니다."; return e; })()
  ));

  /* 5. 이벤트 — 카드 안에서 종류·운용사·ETF·종목·금액으로 다시 좁힌다 */
  main.appendChild(card("구성종목 변경 이벤트", "추정금액 순", null, () => eventBody(evs)));
}

/* 이벤트 카드 본문: 필터 바 + 표. 필터를 바꾸면 표만 다시 그린다. */
const KIND_ORDER = ["NEW", "ADD", "TRIM", "DROP"];
function applyEventFilter(evs) {
  const f = state.ev, q = f.q.trim().toLowerCase();
  return evs.filter(e =>
    f.kinds.has(e.kind) &&
    (f.mngco === "__ALL__" || e.mngco === f.mngco) &&
    (f.etf === "__ALL__" || e.etf_code === f.etf) &&
    Math.abs(e.flow) >= f.minFlow &&
    (!q || e.name.toLowerCase().includes(q) || e.code.includes(q)
        || e.etf_name.toLowerCase().includes(q)));
}
function eventBody(evs) {
  const f = state.ev;
  const wrap = el("div");
  const bar = el("div", "evbar");

  // 종류 (복수 선택)
  const kinds = el("div", "field");
  const kl = el("label"); kl.textContent = "구분"; kinds.appendChild(kl);
  const kg = el("div", "presets");
  KIND_ORDER.forEach(k => {
    const b = el("button", f.kinds.has(k) ? "on" : "");
    b.innerHTML = `<span class="chip ${k}">${KIND[k]}</span>`;
    b.title = "클릭: 켜기/끄기 · Shift+클릭: 이것만";
    b.onclick = ev => {
      if (ev.shiftKey) { f.kinds = new Set([k]); }
      else if (f.kinds.has(k)) { if (f.kinds.size > 1) f.kinds.delete(k); }
      else f.kinds.add(k);
      redraw();
    };
    kg.appendChild(b);
  });
  kinds.appendChild(kg); bar.appendChild(kinds);

  // 운용사
  const mngcos = [...new Set(evs.map(e => e.mngco).filter(Boolean))].sort();
  if (!mngcos.includes(f.mngco)) f.mngco = "__ALL__";
  bar.appendChild(selectField("운용사", [["__ALL__", "전체"], ...mngcos.map(m => [m, m])],
    f.mngco, v => { f.mngco = v; f.etf = "__ALL__"; redraw(); }));

  // ETF (운용사 선택에 따라 좁혀짐)
  const etfPool = evs.filter(e => f.mngco === "__ALL__" || e.mngco === f.mngco);
  const etfs = [...new Map(etfPool.map(e => [e.etf_code, e.etf_name])).entries()]
    .sort((a, b) => a[1].localeCompare(b[1], "ko"));
  if (!etfs.some(([c]) => c === f.etf)) f.etf = "__ALL__";
  bar.appendChild(selectField("ETF", [["__ALL__", "전체"], ...etfs], f.etf,
    v => { f.etf = v; redraw(); }));

  // 최소 추정금액
  bar.appendChild(selectField("최소 금액", [[0, "전체"], [1, "1억 이상"], [5, "5억 이상"],
    [10, "10억 이상"], [50, "50억 이상"], [100, "100억 이상"]], f.minFlow,
    v => { f.minFlow = +v; redraw(); }));

  // 검색
  const qf = el("div", "field");
  const ql = el("label"); ql.textContent = "종목 · ETF 검색"; qf.appendChild(ql);
  const qi = el("input"); qi.type = "search"; qi.placeholder = "종목명 / 코드 / ETF명"; qi.value = f.q;
  let timer; qi.oninput = () => { clearTimeout(timer); timer = setTimeout(() => { f.q = qi.value; redraw(false); }, 120); };
  qf.appendChild(qi); bar.appendChild(qf);

  // 초기화 + 건수
  const rf = el("div", "field"); const rl = el("label"); rl.innerHTML = "&nbsp;"; rf.appendChild(rl);
  const rb = el("button", "ghost"); rb.textContent = "초기화";
  rb.onclick = () => { state.ev = { kinds: new Set(KIND_ORDER), q: "", mngco: "__ALL__", etf: "__ALL__", minFlow: 0 }; redraw(); };
  rf.appendChild(rb); bar.appendChild(rf);
  const note = el("div", "span-note"); bar.appendChild(note);

  const host = el("div");
  wrap.append(bar, host);

  function redraw(rebuildBar = true) {
    if (rebuildBar) { const fresh = eventBody(evs); wrap.replaceWith(fresh); return; }
    drawTable();
  }
  function drawTable() {
    const rows = applyEventFilter(evs);
    const byKind = KIND_ORDER.map(k => `${KIND[k]} <b>${rows.filter(e => e.kind === k).length}</b>`).join(" · ");
    note.innerHTML = `표시 <b>${rows.length.toLocaleString()}</b> / 전체 ${evs.length.toLocaleString()}건`
      + (rows.length ? ` &middot; ${byKind}` : "")
      + (rows.length > 300 ? ` &middot; <span class="warn">상위 300건만 표시 — 필터를 더 좁히세요</span>` : "");
    host.innerHTML = "";
    if (!rows.length) { const x = el("div", "empty");
      x.textContent = evs.length ? "필터 조건에 맞는 이벤트가 없습니다." : "이 기간에 감지된 이벤트가 없습니다.";
      host.appendChild(x); return; }
    host.appendChild(table(
      [{label:"구분",t:1},{label:"테마",t:1},{label:"코드",t:1},{label:"종목명",t:1},
       {label:"ETF",t:1},{label:"운용사",t:1},"종료일 비중(%)","시작일 비중(%)",
       "1CU수량 변화(%)","추정금액(억)"],
      rows.slice(0, 300).map(e => ({cells:[
        {html:`<span class="chip ${e.kind}">${KIND[e.kind]}</span>`, t:1},
        {text:e.theme,t:1},{text:e.code,t:1},{text:e.name,t:1},
        {text:e.etf_name,t:1},{text:e.mngco,t:1},
        {text:e.w==null?"-":nf(e.w,2)}, {text:e.pw==null?"-":nf(e.pw,2)},
        {text:e.dqty==null?"-":sf(e.dqty,2), cls:cls(e.dqty)},
        {text:sf(e.flow,1), cls:cls(e.flow)}]}))));
  }
  drawTable();
  return wrap;
}
function selectField(label, options, value, onChange) {
  const fld = el("div", "field");
  const l = el("label"); l.textContent = label; fld.appendChild(l);
  const sel = el("select");
  options.forEach(([v, txt]) => { const o = el("option"); o.value = v; o.textContent = txt; sel.appendChild(o); });
  sel.value = String(value);
  sel.onchange = () => onChange(sel.value);
  fld.appendChild(sel);
  return fld;
}

function drillPanel(rows) {
  if (!state.sel) return null;
  const [t, s] = state.sel.split("|").map(Number);
  const row = rows.find(r => r.t === t && r.s === s);
  if (!row) return null;

  const box = el("div", "drill");
  const title = el("div", "title");
  title.textContent = `${row.name} (${row.code}) — ${row.theme}`;
  const meta = el("div", "meta");
  meta.textContent = `${fmtDate(state.start)} → ${fmtDate(state.end)}`
    + ` · 평균비중 ${nf(row.avgWeight,2)}% · 수량기여 ${sf(row.conviction,2)}pp`
    + ` · 추정 순매수 ${sf(row.flow,1)}억 · 보유 ETF ${row.etfCnt}개`
    + (row.marketEtfCnt ? ` (ETF 시장 전체 ${row.marketEtfCnt}개)` : "");
  box.append(title, meta);

  const det = row.rows
    .filter(d => d.w != null || d.pw != null)
    .map(d => ({
      etf_code:D.etfs[d.e][0], etf_name:D.etfs[d.e][1], mngco:D.etfs[d.e][2],
      w:d.w, pw:d.pw, q:d.q, pq:d.pq,
      dqty:(d.ratio == null ? null : d.ratio * 100), dw:d.dwQty, flow:d.flow,
    }))
    .sort((a, b) => (b.w == null ? -1 : b.w) - (a.w == null ? -1 : a.w));

  if (!det.length) { const e = el("div","empty");
    e.textContent = "ETF별 내역이 없습니다."; box.appendChild(e); return box; }

  const host = el("div", "plot");
  magnitudeBars(host, det.slice(0, 16), {
    gutter: 210, value: r => r.w || 0, label: r => r.etf_name,
    aria: "ETF별 보유비중 막대차트",
    tip: r => `<b>${r.etf_name}</b> <span class="r">${r.etf_code}</span><br>`
      + `<span class="r">${r.mngco}<br>비중 ${r.w==null?"-":nf(r.w,2)}%`
      + ` (시작일 ${r.pw==null?"-":nf(r.pw,2)}%)`
      + `<br>1CU 수량 ${r.pq==null?"-":nf(r.pq,0)} → ${r.q==null?"-":nf(r.q,0)}`
      + ` (${r.dqty==null?"-":sf(r.dqty,2)}%)<br>수량기여 ${sf(r.dw,2)}pp`
      + ` · 추정 ${sf(r.flow,1)}억</span>`,
  });
  box.appendChild(host);
  box.appendChild(table(
    [{label:"ETF",t:1},{label:"코드",t:1},{label:"운용사",t:1},"종료일 비중(%)",
     "시작일 비중(%)","종료일 1CU수량","시작일 1CU수량","수량변화(%)",
     "수량기여(pp)","추정(억)"],
    det.map(r => ({cells:[{text:r.etf_name,t:1},{text:r.etf_code,t:1},
      {text:r.mngco,t:1},{text:r.w==null?"-":nf(r.w,2)},
      {text:r.pw==null?"-":nf(r.pw,2)},
      {text:r.q==null?"-":nf(r.q,0)},{text:r.pq==null?"-":nf(r.pq,0)},
      {text:r.dqty==null?"-":sf(r.dqty,2), cls:cls(r.dqty)},
      {text:sf(r.dw,2), cls:cls(r.dw)},
      {text:sf(r.flow,1), cls:cls(r.flow)}]}))));
  return box;
}

/* ---------------------------------------------------------------- 초기화 */
const PRESETS = [
  ["prev", "직전 영업일", 1],
  ["w1",   "1주",        5],
  ["m1",   "1개월",      20],
  ["all",  "전체 기간",  -1],
];

function applyPreset(id) {
  const p = PRESETS.find(x => x[0] === id);
  if (!p) return;
  state.preset = id;
  const endIdx = D.dates.indexOf(state.end);
  const back = p[2] < 0 ? D.dates.length - 1 : p[2];
  state.start = D.dates[Math.min(D.dates.length - 1, endIdx + back)];
  syncSelects();
}

function fillDateSelect(sel) {
  sel.innerHTML = "";
  D.dates.forEach(d => {
    const o = el("option"); o.value = d; o.textContent = fmtDate(d); sel.appendChild(o);
  });
}

function syncSelects() {
  $("#f-start").value = state.start;
  $("#f-end").value = state.end;
  document.querySelectorAll(".presets button").forEach(b => {
    b.classList.toggle("on", b.dataset.preset === state.preset);
  });
}

function onDateChange() {
  let start = $("#f-start").value, end = $("#f-end").value;
  if (D.dates.indexOf(start) < D.dates.indexOf(end)) {
    [start, end] = [end, start];       // dates 는 최신순 — 시작일이 더 과거여야 한다
  }
  if (start === end) {                 // 같은 날은 비교가 안 되므로 한 칸 벌린다
    const i = D.dates.indexOf(end);
    if (i + 1 < D.dates.length) start = D.dates[i + 1];
    else end = D.dates[Math.max(0, i - 1)];
  }
  state.start = start; state.end = end;
  state.preset = null; state.sel = null;
  syncSelects(); render();
}

function init() {
  fillDateSelect($("#f-start"));
  fillDateSelect($("#f-end"));

  const pbox = $("#f-presets");
  PRESETS.forEach(([id, label]) => {
    const b = el("button"); b.textContent = label; b.dataset.preset = id;
    b.onclick = () => { state.sel = null; applyPreset(id); render(); };
    pbox.appendChild(b);
  });

  const ts = $("#f-theme");
  const all = el("option"); all.value = "__ALL__"; all.textContent = "전체 테마";
  ts.appendChild(all);
  D.themes.forEach((t, i) => {
    const o = el("option"); o.value = t;
    o.textContent = `${t} (ETF ${D.themeEtfs[i].length})`;
    ts.appendChild(o);
  });
  ts.value = state.theme;

  $("#f-start").onchange = onDateChange;
  $("#f-end").onchange = onDateChange;
  ts.onchange = () => { state.theme = ts.value; state.sel = null; render(); };
  $("#f-theme-reset").onclick = () => {
    state.theme = "__ALL__"; state.sel = null; ts.value = "__ALL__"; render();
  };
  $("#f-mode").onclick = () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : (cur === "light" ? "dark"
      : (matchMedia("(prefers-color-scheme: dark)").matches ? "light" : "dark"));
    document.documentElement.setAttribute("data-theme", next);
    render();
  };
  addEventListener("resize", () => { clearTimeout(window.__rt);
    window.__rt = setTimeout(render, 160); });

  syncSelects();
  renderFreshness();
  render();
}
init();
"""


_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>테마 ETF 리밸런싱 대시보드</title>
<style>__CSS__</style>
<div class="wrap">
  <h1>테마별 ETF 구성종목(PDF) 리밸런싱 대시보드</h1>
  <div class="sub">데이터 출처 <b>한국예탁결제원 SEIBro</b>
    &middot; 보유 기준일 <b>__NDATES__</b>개 (__DATERANGE__)
    &middot; 이 파일을 만든 시각 __GENERATED__</div>

  <div class="filters">
    <div class="field">
      <label for="f-start">시작일 (과거 · 비교 기준)</label>
      <select id="f-start"></select>
    </div>
    <div class="arrow">&rarr;</div>
    <div class="field">
      <label for="f-end">종료일 (최근)</label>
      <select id="f-end"></select>
    </div>
    <div class="field">
      <label>빠른 선택</label>
      <div class="presets" id="f-presets"></div>
    </div>
    <div class="field">
      <label for="f-theme">테마</label>
      <select id="f-theme"></select>
    </div>
    <div class="field">
      <label>&nbsp;</label>
      <button id="f-theme-reset" class="ghost">전체 보기</button>
    </div>
    <div class="spacer"></div>
    <div class="field">
      <label>&nbsp;</label>
      <button id="f-mode" class="ghost">라이트 / 다크</button>
    </div>
    <div class="span-note" id="span-note"></div>
  </div>

  <div class="fresh" id="fresh"></div>

  <div class="hero">
    <div class="k">추정 순매수 합계</div>
    <div class="v" id="hero-v">-</div>
    <div class="n" id="hero-n"></div>
  </div>
  <div class="tiles" id="tiles"></div>
  <div id="cards"></div>

  <footer>
    추정 순매수 = 1CU 구성수량 변화율 &times; 시작일 보유금액.
    1CU 수량은 설정/환매·가격에 영향받지 않으므로 운용사의 실제 포트폴리오 변경만
    반영합니다. 시작일·종료일은 보유한 어떤 기준일 조합이든 고를 수 있고, 계산은
    브라우저에서 즉시 이뤄집니다. 색은 국내 증시 관행에 맞춰
    <b>유입 적색 / 유출 청색</b>이며, 모든 차트는 <b>표</b> 버튼으로 수치 표로
    전환할 수 있습니다. SEIBro PDF 는 T-1 기준으로 공시됩니다.
    투자 판단의 근거로 쓰기 전 원본을 확인하세요.
  </footer>
</div>
<div id="tip" role="tooltip"></div>
<script>window.__DATA__ = __PAYLOAD__;</script>
<script>__JS__</script>
"""


def render(payload: dict[str, Any]) -> str:
    dates = payload["dates"]
    date_range = "{} ~ {}".format(_fmt(dates[-1]), _fmt(dates[0])) if dates else "-"
    html = _HTML
    html = html.replace("__CSS__", _CSS)
    html = html.replace("__NDATES__", str(len(dates)))
    html = html.replace("__DATERANGE__", date_range)
    html = html.replace("__GENERATED__", payload["generated"])
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # </script> 가 JSON 안에서 스크립트를 조기 종료하는 것을 막는다.
    blob = blob.replace("</", "<\\/")
    html = html.replace("__PAYLOAD__", blob)
    html = html.replace("__JS__", _JS)
    return html


def build(
    store: Store,
    path: str | Path,
    *,
    thresholds: Thresholds | None = None,
    max_dates: int = DEFAULT_MAX_DATES,
    seibro_latest: str | None = None,
    **_ignored: Any,
) -> Path:
    payload = build_payload(store, thresholds=thresholds, max_dates=max_dates,
                            seibro_latest=seibro_latest)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(payload), encoding="utf-8")
    return out

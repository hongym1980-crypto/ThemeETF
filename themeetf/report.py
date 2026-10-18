"""콘솔·HTML 리포트."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from tabulate import tabulate

from .detect import DiffReport, EtfRebalance, LeaderRow, theme_flow, top_events

def _fmt_dt(std_dt: str) -> str:
    if len(std_dt) == 8 and std_dt.isdigit():
        return "{}-{}-{}".format(std_dt[:4], std_dt[4:6], std_dt[6:])
    return std_dt


# --------------------------------------------------------------------- 콘솔


def console_report(
    report: DiffReport,
    leader_rows: Sequence[LeaderRow],
    rebalances: Sequence[EtfRebalance],
    *,
    top: int = 15,
) -> str:
    lines: list[str] = []
    add = lines.append

    add("=" * 96)
    add("테마 ETF 구성종목(PDF) 리밸런싱 리포트   {} vs {}".format(
        _fmt_dt(report.std_dt), _fmt_dt(report.prev_dt)))
    add("=" * 96)
    add("비교 대상: {}개 (ETF, 종목) 페어 | 감지 이벤트 {}건".format(
        len(report.diffs), len(report.events)))
    if report.skipped_etfs:
        add("한쪽 날짜에만 스냅샷이 있어 제외한 ETF {}종목".format(len(report.skipped_etfs)))
    add("")

    flows = theme_flow(report)
    if flows:
        add("[테마별 자금 흐름] 추정 순매수 = 1CU 수량 변화 x 전일 보유금액")
        add(tabulate(
            [[f["theme"], f["etf_cnt"], f["stock_cnt"], f["turnover"],
              "{:+,.1f}".format(f["net_flow_eok"]), "{:,.1f}".format(f["gross_flow_eok"])]
             for f in flows],
            headers=["테마", "ETF", "종목", "회전율(pp)", "순매수(억)", "총매매(억)"],
            tablefmt="simple", floatfmt=".2f",
        ))
        add("")

    if leader_rows:
        add("[주도주 후보 TOP {}] 운용사가 집중 편입한 종목".format(top))
        add(tabulate(
            [[i + 1, r.theme, r.stock_code, r.stock_name, r.score,
              "{:+.2f}".format(r.conviction), "{:+,.0f}".format(r.est_flow / 1e8),
              "{}/{}".format(r.etfs_up, r.etfs_down), r.mngco_up,
              r.new_cnt or "", r.drop_cnt or "",
              "{:.2f}".format(r.avg_weight), r.market_etf_cnt or "-"]
             for i, r in enumerate(leader_rows[:top])],
            headers=["#", "테마", "코드", "종목명", "스코어", "수량기여(pp)",
                     "순매수(억)", "증/감ETF", "운용사", "신규", "편출",
                     "평균비중", "전체ETF"],
            tablefmt="simple",
        ))
        add("")

    newly = top_events(report, ["NEW"], limit=top)
    if newly:
        add("[신규 편입]")
        add(tabulate(
            [[e["theme"], e["stock_code"], _stock_name(report, e),
              _etf_name(report, e), _etf_mngco(report, e),
              "{:.2f}".format(e["weight"] or 0.0)] for e in newly],
            headers=["테마", "코드", "종목명", "ETF", "운용사", "비중(%)"],
            tablefmt="simple",
        ))
        add("")

    dropped = top_events(report, ["DROP"], limit=top)
    if dropped:
        add("[편출]")
        add(tabulate(
            [[e["theme"], e["stock_code"], _stock_name(report, e),
              _etf_name(report, e), _etf_mngco(report, e),
              "{:.2f}".format(e["prev_weight"] or 0.0)] for e in dropped],
            headers=["테마", "코드", "종목명", "ETF", "운용사", "전일비중(%)"],
            tablefmt="simple",
        ))
        add("")

    if rebalances:
        add("[리밸런싱 발생 ETF]")
        add(tabulate(
            [[r.theme, r.etf_code, r.etf_name, r.mngco, r.turnover,
              r.new_cnt, r.drop_cnt, r.add_cnt, r.trim_cnt,
              "{:,.0f}".format(r.est_flow_abs / 1e8)]
             for r in rebalances[: top * 2]],
            headers=["테마", "코드", "ETF", "운용사", "회전율(pp)",
                     "신규", "편출", "확대", "축소", "총매매(억)"],
            tablefmt="simple",
        ))
        add("")

    if not report.events:
        add("감지된 리밸런싱 이벤트가 없습니다. (임계값을 낮춰보세요: --qty-eps)")
    return "\n".join(lines)


def _stock_name(report: DiffReport, event: dict[str, Any]) -> str:
    for d in report.diffs:
        if d.stock_code == event["stock_code"] and d.isin == event["isin"]:
            return d.stock_name
    return event["stock_code"]


# ---------------------------------------------------------------------- HTML

_CSS = """
/* dataviz 검증 통과 팔레트 — 유입 적색 / 유출 청색 (국내 증시 관행) */
:root{color-scheme:light;
--bg:#f9f9f7;--fg:#0b0b0b;--muted:#898781;--line:#e1e0d9;--card:#fcfcfb;
--up:#e34948;--down:#2a78d6;--accent:#2a78d6;}
@media (prefers-color-scheme: dark){:root:not([data-theme=light]){color-scheme:dark;
--bg:#0d0d0d;--fg:#ffffff;--muted:#898781;--line:#2c2c2a;--card:#1a1a19;
--up:#e66767;--down:#3987e5;--accent:#3987e5;}}
:root[data-theme=dark]{color-scheme:dark;
--bg:#0d0d0d;--fg:#ffffff;--muted:#898781;--line:#2c2c2a;--card:#1a1a19;
--up:#e66767;--down:#3987e5;--accent:#3987e5;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 "Pretendard",
-apple-system,"Segoe UI",system-ui,sans-serif;padding:28px 22px 64px}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:15px;margin:34px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.sub{color:var(--muted);font-size:13px;margin-bottom:22px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0 6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .k{color:var(--muted);font-size:11px;letter-spacing:.03em;text-transform:uppercase}
.card .v{font-size:20px;font-weight:650;margin-top:3px;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 11px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
th{background:var(--card);font-weight:600;color:var(--muted);font-size:11.5px;
letter-spacing:.02em;position:sticky;top:0}
td:nth-child(-n+4),th:nth-child(-n+4){text-align:left}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:var(--card)}
.num{font-variant-numeric:tabular-nums}
.up{color:var(--up);font-weight:600}.down{color:var(--down);font-weight:600}
.tag{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11px;
border:1px solid var(--line);background:var(--card)}
.note{color:var(--muted);font-size:12px;margin-top:8px}
footer{margin-top:44px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:14px}
"""


def _signed(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "-"
    cls = "up" if value > 0 else ("down" if value < 0 else "")
    txt = "{:+,.{d}f}{s}".format(value, d=digits, s=suffix)
    return '<span class="{c}">{t}</span>'.format(c=cls, t=txt) if cls else txt


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "".join("<th>{}</th>".format(html.escape(h)) for h in headers)
    body = "".join(
        "<tr>{}</tr>".format("".join('<td class="num">{}</td>'.format(c) for c in row))
        for row in rows
    )
    return (
        '<div class="scroll"><table><thead><tr>{h}</tr></thead>'
        "<tbody>{b}</tbody></table></div>".format(h=head, b=body)
    )


def html_report(
    report: DiffReport,
    leader_rows: Sequence[LeaderRow],
    rebalances: Sequence[EtfRebalance],
    *,
    top: int = 30,
) -> str:
    esc = html.escape
    flows = theme_flow(report)
    net_total = sum(f["net_flow_eok"] for f in flows)
    parts: list[str] = []
    add = parts.append

    add("<title>테마 ETF 리밸런싱 트래커</title>")
    add("<style>{}</style>".format(_CSS))
    add('<div class="wrap">')
    add("<h1>테마별 ETF 구성종목(PDF) 리밸런싱 리포트</h1>")
    add('<div class="sub">기준일 <b>{}</b> vs 직전 <b>{}</b> &middot; 출처 한국예탁결제원 SEIBro'
        " &middot; 생성 {}</div>".format(
            _fmt_dt(report.std_dt), _fmt_dt(report.prev_dt),
            datetime.now().strftime("%Y-%m-%d %H:%M")))

    add('<div class="cards">')
    for k, v in [
        ("비교 페어", "{:,}".format(len(report.diffs))),
        ("감지 이벤트", "{:,}".format(len(report.events))),
        ("리밸런싱 ETF", "{:,}".format(len(rebalances))),
        ("주도주 후보", "{:,}".format(len(leader_rows))),
        ("테마 순매수(억)", "{:+,.0f}".format(net_total)),
    ]:
        add('<div class="card"><div class="k">{}</div><div class="v">{}</div></div>'.format(
            esc(k), v))
    add("</div>")
    add('<div class="note">추정 순매수 = 1CU 구성수량 변화율 &times; 전일 보유금액. '
        "1CU 수량은 설정/환매·가격과 무관하므로 운용사의 실제 포트폴리오 변경만 반영합니다.</div>")

    if flows:
        add("<h2>테마별 자금 흐름</h2>")
        add(_table(
            ["테마", "ETF", "종목", "회전율(pp)", "순매수(억)", "총매매(억)"],
            [[esc(f["theme"]), f["etf_cnt"], f["stock_cnt"],
              "{:.2f}".format(f["turnover"]), _signed(f["net_flow_eok"], 1),
              "{:,.1f}".format(f["gross_flow_eok"])] for f in flows],
        ))

    if leader_rows:
        add("<h2>주도주 후보 &mdash; 최근 집중 편입 종목</h2>")
        add(_table(
            ["#", "테마", "코드", "종목명", "스코어", "수량기여(pp)", "순매수(억)",
             "증가ETF", "감소ETF", "운용사", "신규", "편출", "평균비중(%)", "전체보유ETF"],
            [[i + 1, esc(r.theme), esc(r.stock_code), esc(r.stock_name),
              "{:.2f}".format(r.score), _signed(r.conviction),
              _signed(r.est_flow / 1e8, 0), r.etfs_up, r.etfs_down, r.mngco_up,
              r.new_cnt or "&middot;", r.drop_cnt or "&middot;",
              "{:.2f}".format(r.avg_weight),
              r.market_etf_cnt if r.market_etf_cnt is not None else "-"]
             for i, r in enumerate(leader_rows[:top])],
        ))
        add('<div class="note">스코어 = 2&times;수량기여비중 + 2&times;신규 &minus; 2&times;편출 '
            "+ 0.6&times;순확산 + 0.5&times;운용사수 + 1.5&times;log(순매수).</div>")

    for kind, title in (("NEW", "신규 편입"), ("DROP", "편출")):
        events = top_events(report, [kind], limit=top)
        if not events:
            continue
        add("<h2>{}</h2>".format(title))
        weight_key = "weight" if kind == "NEW" else "prev_weight"
        add(_table(
            ["테마", "코드", "종목명", "ETF", "운용사", "비중(%)", "1CU수량", "추정금액(억)"],
            [[esc(e["theme"]), esc(e["stock_code"]), esc(_stock_name(report, e)),
              esc(_etf_name(report, e)), esc(_etf_mngco(report, e)),
              "{:.2f}".format(e[weight_key] or 0.0),
              "{:,.0f}".format(e["qty_per_cu"] or e["prev_qty"] or 0),
              _signed((e["d_value"] or 0) / 1e8, 0)] for e in events],
        ))

    if rebalances:
        add("<h2>리밸런싱 발생 ETF</h2>")
        add(_table(
            ["테마", "코드", "ETF", "운용사", "회전율(pp)", "신규", "편출", "확대", "축소",
             "총매매(억)"],
            [[esc(r.theme), esc(r.etf_code), esc(r.etf_name), esc(r.mngco),
              "{:.2f}".format(r.turnover), r.new_cnt or "&middot;",
              r.drop_cnt or "&middot;", r.add_cnt or "&middot;", r.trim_cnt or "&middot;",
              "{:,.0f}".format(r.est_flow_abs / 1e8)] for r in rebalances],
        ))

    if report.skipped_etfs:
        add('<div class="note">한쪽 날짜에만 스냅샷이 있어 비교에서 제외한 ETF '
            "{}종목.</div>".format(len(report.skipped_etfs)))

    add("<footer>데이터 출처: 한국예탁결제원 증권정보포털 SEIBro "
        "(ETF 종목발행현황 · 보유자산명세 · 종목보유현황). "
        "PDF 는 T-1 기준으로 공시됩니다. 투자 판단의 근거로 사용하기 전 원본을 확인하세요."
        "</footer>")
    add("</div>")
    return "\n".join(parts)


def _etf_name(report: DiffReport, event: dict[str, Any]) -> str:
    for d in report.diffs:
        if d.isin == event["isin"]:
            return d.etf_name
    return event["isin"]


def _etf_mngco(report: DiffReport, event: dict[str, Any]) -> str:
    for d in report.diffs:
        if d.isin == event["isin"]:
            return d.mngco
    return ""


def write_html(path: str | Path, content: str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return out

"""명령줄 인터페이스.

    python -m themeetf universe                 ETF 전종목 갱신 + 테마 분류
    python -m themeetf collect                  오늘자 PDF 스냅샷 수집
    python -m themeetf detect                   최근 2개 기준일 비교 + 리포트
    python -m themeetf daily                    universe -> collect -> detect
    python -m themeetf dashboard --open         대화형 HTML 대시보드
    python -m themeetf doctor                   연결 진단 (최신화 실패 원인)
    python -m themeetf themes                   테마별 편입 ETF 목록
    python -m themeetf history --stock 000660   특정 종목의 비중 추이
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import webbrowser
from pathlib import Path

from . import __version__, dashboard, dnsfix, doctor
from .collect import Collector, business_days_back, missing_business_days
from .detect import Thresholds, compare, etf_rebalances, leaders, persist_events
from .paths import (
    DASHBOARD_PATH,
    DB_PATH,
    REPORT_DIR,
    ROOT,
    ensure_dirs,
    migrate_legacy,
)
from .report import console_report, html_report, write_html
from .seibro import HOST as SEIBRO_HOST, SeibroClient
from .store import Store
from .universe import (
    ClassificationCollapsed,
    classify,
    load_theme_config,
    refresh_universe,
)

DEFAULT_DB = DB_PATH
DEFAULT_REPORTS = REPORT_DIR


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="themeetf",
        description="테마별 ETF 구성종목(PDF) 추적 및 리밸런싱 감지기 "
                    "(출처: 한국예탁결제원 SEIBro)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 경로")
    p.add_argument("--themes-config", type=Path, default=None, help="테마 정의 JSON")
    p.add_argument("--delay", type=float, default=0.35, help="API 호출 간 대기(초)")
    p.add_argument("--workers", type=int, default=4, help="병렬 요청 수")
    p.add_argument("--no-dns-fallback", action="store_true",
                   help="DNS 해석이 실패해도 공용 DNS 대체 경로를 쓰지 않는다")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version="themeetf " + __version__)

    sub = p.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("universe", help="ETF 전종목 갱신 후 테마 분류")
    u.add_argument("--force-classify", action="store_true",
                   help="분류 결과가 급감해도 그대로 반영한다")

    c = sub.add_parser("collect", help="테마 ETF 의 PDF 스냅샷 수집")
    c.add_argument("--theme", help="특정 테마만")
    c.add_argument("--no-expand", action="store_true",
                   help="역인덱스 확장 생략 (TOP10 만 수집, 빠름)")
    c.add_argument("--max-stocks", type=int, default=None,
                   help="역인덱스 확장 종목 수 상한")

    b = sub.add_parser(
        "backfill",
        help="과거 기준일을 역인덱스로 채운다 (TOP10 은 최신일만 제공되지만 "
             "종목보유현황은 과거 일자를 지원)",
    )
    b.add_argument("--days", type=int, default=5, help="최신 기준일 이전 영업일 수")
    b.add_argument("--dates", nargs="+", help="특정 기준일들 YYYYMMDD")
    b.add_argument("--theme")
    b.add_argument("--max-stocks", type=int, default=None)

    d = sub.add_parser("detect", help="기준일 비교 후 리밸런싱/주도주 리포트")
    d.add_argument("--theme")
    d.add_argument("--date", help="비교 대상 기준일 YYYYMMDD (기본: 최신)")
    d.add_argument("--prev", help="직전 기준일 YYYYMMDD (기본: 그 다음 최신)")
    d.add_argument("--top", type=int, default=15)
    d.add_argument("--qty-eps", type=float, default=0.005,
                   help="1CU 수량 변화율 임계 (기본 0.005 = 0.5%%)")
    d.add_argument("--weight-eps", type=float, default=0.02, help="비중 변화 임계(pp)")
    d.add_argument("--html", type=Path, nargs="?", const=Path("auto"),
                   help="HTML 리포트 저장 경로 (생략 시 reports/ 아래 자동 생성)")
    d.add_argument("--no-save", action="store_true", help="event 테이블에 저장하지 않음")

    dl = sub.add_parser("daily", help="universe -> collect -> detect 일괄 실행")
    dl.add_argument("--theme")
    dl.add_argument("--top", type=int, default=15)
    dl.add_argument("--no-expand", action="store_true")
    dl.add_argument("--max-stocks", type=int, default=None)
    dl.add_argument("--skip-universe", action="store_true")
    dl.add_argument("--force-classify", action="store_true",
                    help="테마 분류 급감 보호를 끈다")
    dl.add_argument("--catch-up", type=int, default=10, metavar="N",
                    help="빠진 영업일을 최대 N일까지 소급 수집 (0 이면 생략)")

    db = sub.add_parser("dashboard", help="대화형 HTML 대시보드 생성")
    db.add_argument("-o", "--out", type=Path, default=DASHBOARD_PATH)
    db.add_argument("--max-dates", type=int, default=60, metavar="N",
                    help="대시보드에 담을 최신 기준일 수 (파일 크기 제한)")
    db.add_argument("--open", action="store_true", help="생성 후 브라우저로 열기")
    db.add_argument("--offline", action="store_true",
                    help="SEIBro 최신 기준일 확인을 생략 (네트워크 호출 없음)")

    sub.add_parser("doctor", help="연결 진단 — 최신화가 실패하는 이유를 찾는다")

    t = sub.add_parser("themes", help="테마별 편입 ETF 목록")
    t.add_argument("--theme")

    h = sub.add_parser("history", help="종목/ETF 비중 추이")
    h.add_argument("--stock", help="종목 단축코드 (예: 000660)")
    h.add_argument("--etf", help="ETF 단축코드 (예: 091160)")
    h.add_argument("--limit", type=int, default=40)

    return p


def _client(args: argparse.Namespace) -> SeibroClient:
    return SeibroClient(delay=args.delay)


# ---------------------------------------------------------------- subcommands


def cmd_universe(args: argparse.Namespace, store: Store) -> int:
    client = _client(args)
    count, market_dt = refresh_universe(client, store)
    themes = load_theme_config(args.themes_config)
    try:
        counts = classify(store, themes,
                          allow_collapse=getattr(args, "force_classify", False))
    except ClassificationCollapsed as exc:
        print("\n  ! {}".format(exc))
        print("    보통 SEIBro 가 순자산총액을 아직 채우지 않은 이른 시간에 실행하면"
              " 생깁니다.")
        print("    잠시 뒤(장 시작 이후) 다시 실행하세요. 기존 테마 매핑은 그대로"
              " 유지했으므로 수집은 정상 진행됩니다.")
        print("    분류 결과를 그래도 반영하려면 `--force-classify` 를 쓰세요.")
        return 0
    print("ETF 전종목 {}건 갱신 (SEIBro 기준일 {})".format(count, market_dt))
    print("테마 분류 결과:")
    for name in sorted(counts, key=lambda k: -counts[k]):
        print("  {:<16} {:>3}종목".format(name, counts[name]))
    missing = [t for t in themes if t not in counts]
    if missing:
        print("  (편입 ETF 없음: {})".format(", ".join(missing)))
    return 0


def _collect(args: argparse.Namespace, store: Store):
    collector = Collector(
        _client(args), store,
        workers=args.workers,
        expand=not getattr(args, "no_expand", False),
        max_expand_stocks=getattr(args, "max_stocks", None),
    )
    result = collector.run(theme=getattr(args, "theme", None))
    print(result.summary())
    for err in result.errors[:10]:
        print("  ! " + err)
    if len(result.errors) > 10:
        print("  ! ... {}건 더".format(len(result.errors) - 10))
    return result


def cmd_collect(args: argparse.Namespace, store: Store) -> int:
    _collect(args, store)
    return 0


def cmd_backfill(args: argparse.Namespace, store: Store) -> int:
    latest = store.scalar("SELECT MAX(std_dt) FROM holding")
    if not latest:
        print("최신 스냅샷이 없습니다. `collect` 를 먼저 실행하세요.", file=sys.stderr)
        return 1
    dates = args.dates or business_days_back(latest, args.days)
    dates = [d for d in dates if d != latest]

    collector = Collector(
        _client(args), store, workers=args.workers,
        max_expand_stocks=args.max_stocks,
    )
    results = collector.backfill(dates, theme=args.theme)
    for res in results:
        status = res.summary() if res.holdings else "기준일 {} 데이터 없음 (휴장일 추정)".format(
            res.std_dt)
        print(status)
    have = store.available_dates()
    print("\n보유 기준일 {}개: {}".format(len(have), ", ".join(have[:12])))
    return 0


def _resolve_dates(store: Store, args: argparse.Namespace) -> tuple[str, str] | None:
    dates = store.available_dates()
    if not dates:
        print("스냅샷이 없습니다. `collect` 를 먼저 실행하세요.", file=sys.stderr)
        return None
    std_dt = args.date or dates[0]
    if std_dt not in dates:
        print("기준일 {} 스냅샷이 없습니다. 보유: {}".format(std_dt, ", ".join(dates[:8])),
              file=sys.stderr)
        return None
    if args.prev:
        prev_dt = args.prev
    else:
        earlier = [d for d in dates if d < std_dt]
        if not earlier:
            print("비교할 직전 기준일이 없습니다. 하루 뒤 다시 `collect` 하면 "
                  "변화 감지가 시작됩니다. (보유 기준일: {})".format(", ".join(dates)),
                  file=sys.stderr)
            return None
        prev_dt = earlier[0]
    return std_dt, prev_dt


def cmd_detect(args: argparse.Namespace, store: Store) -> int:
    resolved = _resolve_dates(store, args)
    if resolved is None:
        return 1
    std_dt, prev_dt = resolved

    th = Thresholds(qty_eps=args.qty_eps, weight_eps=args.weight_eps)
    report = compare(store, std_dt, prev_dt, theme=args.theme, thresholds=th)
    leader_rows = leaders(report, thresholds=th, store=store)
    rebal = etf_rebalances(report, thresholds=th)

    print(console_report(report, leader_rows, rebal, top=args.top))

    if not args.no_save:
        saved = persist_events(store, report)
        if saved:
            print("\nevent 테이블에 {}건 저장".format(saved))

    if args.html is not None:
        path = args.html
        if str(path) == "auto":
            path = DEFAULT_REPORTS / "rebalance_{}.html".format(std_dt)
        out = write_html(path, html_report(report, leader_rows, rebal,
                                          top=max(args.top, 30)))
        print("HTML 리포트: {}".format(out))
    return 0


#: 직전 기준일 대비 이 비율 미만이면 수집이 덜 된 것으로 본다.
THIN_SNAPSHOT_RATIO = 0.5


def _warn_if_thin(store: Store, std_dt: str) -> bool:
    """수집량이 직전 기준일보다 크게 적으면 경고한다.

    부분 수집된 날짜가 그대로 '최신' 이 되면, 비교에서 양쪽에 모두 있는 ETF 만
    남아 신규편입/편출이 통째로 사라진 것처럼 보인다.
    """
    prev_dt = store.scalar(
        "SELECT MAX(std_dt) FROM holding WHERE std_dt < ?", (std_dt,))
    if not prev_dt:
        return False
    now = store.scalar(
        "SELECT COUNT(DISTINCT isin) FROM holding WHERE std_dt = ?", (std_dt,)) or 0
    before = store.scalar(
        "SELECT COUNT(DISTINCT isin) FROM holding WHERE std_dt = ?", (prev_dt,)) or 0
    if before < 10 or now >= before * THIN_SNAPSHOT_RATIO:
        return False

    print("\n  !! 수집이 덜 되었습니다: {} 는 ETF {}종목뿐입니다"
          " (직전 {} 는 {}종목).".format(std_dt, now, prev_dt, before))
    print("     이 상태로 두면 대시보드 비교에서 신규편입/편출이 거의 사라집니다.")
    print("     원인은 대개 둘 중 하나입니다:")
    print("       - 테마 분류가 무너짐  -> `python -m themeetf themes` 로 확인")
    print("       - 수집 중 네트워크 실패 -> `python -m themeetf doctor` 로 확인")
    print("     고친 뒤 `python -m themeetf collect` 를 다시 실행하면 채워집니다.")
    return True


def _rule(title: str) -> None:
    print("\n" + "─" * 74)
    print(title)
    print("─" * 74)


def cmd_daily(args: argparse.Namespace, store: Store) -> int:
    """최신화 전 과정을 한 번에: 목록 갱신 -> 오늘자 수집 -> 빠진 날 메우기
    -> 감지 -> 리포트 -> 대시보드."""
    before = set(store.available_dates())
    prev_latest = max(before) if before else None

    if not args.skip_universe:
        _rule("1/5  ETF 목록 갱신")
        cmd_universe(args, store)

    _rule("2/5  최신 기준일 구성종목 수집")
    result = _collect(args, store)
    seibro_latest = result.std_dt
    _warn_if_thin(store, seibro_latest)

    catch_up = getattr(args, "catch_up", 10)
    if catch_up:
        have = set(store.available_dates())
        gaps = missing_business_days(have, seibro_latest, cap=catch_up)
        gaps = [d for d in gaps if d not in have]
        if gaps:
            _rule("3/5  빠진 영업일 {}일 메우기 ({})".format(
                len(gaps), ", ".join(gaps)))
            collector = Collector(
                _client(args), store, workers=args.workers,
                max_expand_stocks=getattr(args, "max_stocks", None),
            )
            for res in collector.backfill(gaps, theme=args.theme):
                print(res.summary() if res.holdings
                      else "기준일 {} 데이터 없음 (휴장일 추정)".format(res.std_dt))
        else:
            _rule("3/5  빠진 영업일 없음 — 이력이 연속입니다")

    after = store.available_dates()
    added = sorted(set(after) - before, reverse=True)

    _rule("4/5  리밸런싱 감지")
    detect_args = argparse.Namespace(
        theme=args.theme, date=None, prev=None, top=args.top,
        qty_eps=0.005, weight_eps=0.02, html=Path("auto"), no_save=False,
    )
    rc = cmd_detect(detect_args, store)

    _rule("5/5  대시보드 갱신")
    if len(after) >= 2:
        out = dashboard.build(store, DASHBOARD_PATH, seibro_latest=seibro_latest)
        print("대시보드: {}".format(out))
    else:
        print("기준일이 {}개뿐이라 대시보드를 만들 수 없습니다."
              " 비교에는 2개 이상이 필요합니다.".format(len(after)))

    _rule("데이터 최신화 결과")
    now_latest = after[0] if after else "-"
    print("  SEIBro 최신 기준일 : {}".format(_pretty(seibro_latest)))
    print("  내 데이터 최신     : {}".format(_pretty(now_latest)))
    if added:
        print("  새로 추가된 기준일  : {}".format(
            ", ".join(_pretty(d) for d in added)))
    else:
        print("  새로 추가된 기준일  : 없음 (이미 최신 상태였습니다)")
    if prev_latest:
        print("  직전 실행 시 최신   : {}".format(_pretty(prev_latest)))
    print("  보유 기준일 총 {}개".format(len(after)))
    if now_latest != seibro_latest:
        print("\n  ! 최신 기준일이 SEIBro 와 다릅니다. 해당 일자에 공시가 없거나"
              "\n    수집이 일부 실패했을 수 있습니다. 위 로그를 확인하세요.")
    return rc


def _pretty(std_dt: str | None) -> str:
    if not std_dt or len(std_dt) != 8:
        return str(std_dt or "-")
    return "{}-{}-{}".format(std_dt[:4], std_dt[4:6], std_dt[6:])


def cmd_dashboard(args: argparse.Namespace, store: Store) -> int:
    # 데이터가 최신인지 대시보드 안에서 바로 보이게 SEIBro 기준일을 확인한다.
    seibro_latest = None
    if not args.offline:
        try:
            _, seibro_latest = _client(args).etf_count()
        except Exception as exc:
            print("SEIBro 기준일 확인 실패(무시하고 진행): {}".format(exc))
    out = dashboard.build(store, args.out, max_dates=args.max_dates,
                          seibro_latest=seibro_latest)
    size = out.stat().st_size / 1024
    print("대시보드 생성: {} ({:,.0f} KB)".format(out, size))
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_doctor(args: argparse.Namespace, store: Store) -> int:
    return 0 if doctor.run().ok else 1


def cmd_themes(args: argparse.Namespace, store: Store) -> int:
    rows = store.theme_etfs(args.theme)
    if not rows:
        print("테마 매핑이 없습니다. `universe` 를 먼저 실행하세요.")
        return 1
    current = None
    for r in rows:
        if r["theme"] != current:
            current = r["theme"]
            print("\n[{}]".format(current))
        print("  {code}  {name:<34} {mngco:<14} 순자산 {nav:>9,.0f}억  보수 {fee}".format(
            code=r["code"], name=(r["name"] or "")[:34], mngco=(r["mngco"] or "")[:14],
            nav=(r["nav_total"] or 0) / 1e8, fee=r["total_fee"]))
    print("\n합계 {}건".format(len(rows)))
    return 0


def cmd_history(args: argparse.Namespace, store: Store) -> int:
    if not args.stock and not args.etf:
        print("--stock 또는 --etf 중 하나는 지정해야 합니다.", file=sys.stderr)
        return 1
    where, params = [], []
    if args.stock:
        where.append("h.stock_code = ?")
        params.append(args.stock)
    if args.etf:
        where.append("m.code = ?")
        params.append(args.etf)
    sql = """
        SELECT h.std_dt, m.code AS etf_code, m.name AS etf_name, h.stock_code,
               COALESCE(h.stock_name, h.stock_code) AS stock_name,
               h.weight, h.qty_per_cu, h.hold_value, h.rank
        FROM holding h JOIN etf_master m ON m.isin = h.isin
        WHERE {w}
        ORDER BY h.std_dt DESC, h.weight DESC
        LIMIT ?
    """.format(w=" AND ".join(where))
    params.append(args.limit)
    rows = store.query(sql, params)
    if not rows:
        print("해당 조건의 스냅샷이 없습니다.")
        return 1
    print("{:<10} {:<8} {:<26} {:<10} {:>8} {:>12} {:>12}".format(
        "기준일", "ETF", "ETF명", "종목", "비중%", "1CU수량", "보유금액(억)"))
    for r in rows:
        print("{:<10} {:<8} {:<26} {:<10} {:>8} {:>12} {:>12}".format(
            r["std_dt"], r["etf_code"], (r["etf_name"] or "")[:26],
            (r["stock_name"] or "")[:10],
            "{:.2f}".format(r["weight"] or 0),
            "{:,.0f}".format(r["qty_per_cu"] or 0),
            "{:,.0f}".format((r["hold_value"] or 0) / 1e8)))
    return 0


_DISPATCH = {
    "universe": cmd_universe,
    "collect": cmd_collect,
    "backfill": cmd_backfill,
    "detect": cmd_detect,
    "daily": cmd_daily,
    "dashboard": cmd_dashboard,
    "doctor": cmd_doctor,
    "themes": cmd_themes,
    "history": cmd_history,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:
        logging.getLogger("themeetf.seibro").setLevel(logging.WARNING)

    # 윈도우 콘솔에서 한글이 깨지지 않게
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    # VPN 리졸버가 seibro.or.kr 을 못 푸는 환경을 위한 대체 경로 (필요할 때만 작동)
    if args.no_dns_fallback:
        os.environ["THEMEETF_NO_DNS_FALLBACK"] = "1"
    if args.cmd not in ("doctor",):
        dnsfix.ensure(SEIBRO_HOST)

    # 산출물은 모두 output/ 아래로 모은다. 구버전 경로가 있으면 한 번 옮긴다.
    ensure_dirs()
    for line in migrate_legacy():
        log_msg = "산출물 이동: {}".format(line)
        print(log_msg)

    store = Store(args.db)
    return _DISPATCH[args.cmd](args, store)


if __name__ == "__main__":
    raise SystemExit(main())

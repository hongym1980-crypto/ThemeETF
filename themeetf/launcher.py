"""더블클릭으로 쓰는 메뉴형 실행기.

`ThemeETF.bat` 이 이 모듈을 띄운다. 명령을 외우지 않고도 수집 -> 감지 ->
대시보드까지 순서대로 돌릴 수 있게 하는 것이 목적이다.

`python -m themeetf.launcher` 로 직접 실행해도 된다.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser

from .paths import (
    DASHBOARD_PATH as DASHBOARD,
    DB_PATH as DB,
    OUTPUT,
    ROOT,
    ensure_dirs,
    migrate_legacy,
)

BANNER = """
  ┌───────────────────────────────────────────────────────────────────┐
  │  T h e m e E T F   ·   테마 ETF 구성종목(PDF) 리밸런싱 감지기      │
  └───────────────────────────────────────────────────────────────────┘
"""

MENU = """
  ■ 최신 데이터가 필요하면 이것만 누르면 됩니다
  [1] 최신 데이터 받기    SEIBro 에서 새 기준일을 받아오고 빠진 영업일까지 채운 뒤
                        리포트와 대시보드를 다시 만들어 엽니다  (약 2~3분, 인터넷 필요)

  ■ 이미 받아둔 데이터로 보기 (인터넷 불필요)
  [2] 대시보드 다시 열기  저장된 데이터로 다시 그려서 브라우저로 열기
  [3] 리포트만 보기      최근 두 기준일 비교 결과를 콘솔에 출력
  [5] 종목 조회         특정 종목의 ETF별 비중/수량 추이
  [6] 테마 목록         테마별 편입 ETF 확인
  [8] 상태 확인         산출물 폴더 · 보유 기준일 · SEIBro 최신 기준일 비교
  [D] 연결 진단         최신화가 실패할 때 원인(VPN/DNS/방화벽)을 찾아 준다

  ■ 가끔 쓰는 것
  [4] 과거 더 채우기     원하는 영업일 수만큼 이력을 더 소급 수집
  [7] ETF 목록만 갱신    SEIBro 전종목 재수집 + 테마 재분류
  [9] 라이브러리 설치    requests / pandas / tabulate 설치

  [0] 종료
"""


def _sep(char: str = "-", n: int = 74) -> None:
    print(char * n)


def _run(args: list[str], *, check: bool = False) -> int:
    """자기 자신과 같은 인터프리터로 themeetf 서브커맨드를 실행."""
    cmd = [sys.executable, "-m", "themeetf", *args]
    print("\n$ " + " ".join(cmd[2:]))
    _sep()
    # 자식 프로세스와 출력이 섞이지 않게 (리다이렉트 시 부모는 블록 버퍼링된다)
    sys.stdout.flush()
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run(cmd, cwd=ROOT, env=env)
    _sep()
    if proc.returncode:
        print("[실패] 종료코드 {}".format(proc.returncode))
    if check and proc.returncode:
        raise SystemExit(proc.returncode)
    return proc.returncode


class _Quit(Exception):
    """stdin 이 닫혔거나 Ctrl-C — 메뉴를 종료한다."""


def _ask(prompt: str, default: str = "") -> str:
    """입력을 받는다. stdin 이 닫히면 :class:`_Quit` 를 올려 루프가 돌지 않게 한다."""
    try:
        value = input(prompt).strip()
    except EOFError:
        raise _Quit from None
    except KeyboardInterrupt:
        print()
        raise _Quit from None
    return value or default


def _status() -> None:
    if not DB.exists():
        print("\n아직 데이터가 없습니다. [1] 오늘 실행 을 먼저 돌리세요.")
        return
    from .store import Store

    store = Store(DB)
    dates = store.available_dates()
    print("\n산출물 폴더: {}".format(OUTPUT))
    print("저장소: {}  ({:,.1f} MB)".format(DB, DB.stat().st_size / 1e6))
    print("ETF 전종목       {:>7,}".format(store.scalar("SELECT COUNT(*) FROM etf_master") or 0))
    print("테마 매핑        {:>7,}".format(store.scalar("SELECT COUNT(*) FROM etf_theme") or 0))
    print("구성종목 스냅샷   {:>7,}".format(store.scalar("SELECT COUNT(*) FROM holding") or 0))
    print("감지 이벤트      {:>7,}".format(store.scalar("SELECT COUNT(*) FROM event") or 0))
    print("보유 기준일 {}개: {}".format(len(dates), ", ".join(dates[:12]) or "-"))
    if DASHBOARD.exists():
        print("대시보드: {}".format(DASHBOARD))
    _freshness(check_online=True)
    if len(dates) < 2:
        print("   비교에는 기준일이 2개 이상 필요합니다 -> [1] 최신 데이터 받기")


def _open_dashboard(rebuild: bool = True) -> None:
    if rebuild:
        if _run(["dashboard", "-o", str(DASHBOARD)]):
            return
    if not DASHBOARD.exists():
        print("대시보드 파일이 없습니다.")
        return
    print("브라우저로 엽니다: {}".format(DASHBOARD))
    webbrowser.open(DASHBOARD.resolve().as_uri())


def _fmt_date(d: str | None) -> str:
    if not d or len(d) != 8:
        return str(d or "-")
    return "{}-{}-{}".format(d[:4], d[4:6], d[6:])


def _seibro_latest() -> str | None:
    """SEIBro 가 공시한 최신 기준일. 오프라인이면 None."""
    try:
        from .seibro import SeibroClient

        _, std_dt = SeibroClient(delay=0.1, retries=1, timeout=15).etf_count()
        return std_dt or None
    except Exception:
        return None


def _freshness(check_online: bool = True) -> tuple[str | None, str | None]:
    """(내 데이터 최신, SEIBro 최신) 을 출력하고 그대로 돌려준다."""
    mine = (_dates() or [None])[0]
    latest = _seibro_latest() if check_online else None

    print("\n  ── 데이터 상태 " + "─" * 55)
    print("   내 데이터 최신 기준일 : {}".format(_fmt_date(mine)))
    if latest is None:
        print("   SEIBro 최신 기준일    : 확인 실패 (인터넷 연결을 확인하세요)")
    else:
        print("   SEIBro 최신 기준일    : {}".format(_fmt_date(latest)))
        if mine == latest:
            print("   → 최신 상태입니다. 새로 받을 데이터가 없습니다.")
        elif mine is None:
            print("   → 아직 받은 데이터가 없습니다. [1] 최신 데이터 받기 를 실행하세요.")
        else:
            print("   → 새 데이터가 있습니다. [1] 최신 데이터 받기 를 실행하세요.")
    print("  " + "─" * 69)
    return mine, latest


def _today() -> None:
    """[1] 최신 데이터 받기 — 이 메뉴 하나로 최신화가 끝나야 한다."""
    before = (_dates() or [None])[0]

    print("\n  SEIBro 에서 새 기준일을 받아옵니다.")
    print("  ETF 목록 갱신 → 최신 기준일 수집 → 빠진 영업일 메우기 → 리포트 → 대시보드")
    speed = _ask("  호출 간격(초) [Enter = 0.25, 느린 회선이면 0.5]: ", "0.25")
    try:
        float(speed)
    except ValueError:
        print("  숫자가 아니어서 기본값 0.25 를 씁니다.")
        speed = "0.25"

    rc = _run(["--delay", speed, "--workers", "5", "daily", "--top", "20"])
    if rc:
        print("\n  최신화에 실패했습니다. 원인을 자동으로 진단합니다...")
        _doctor()
        return

    after = (_dates() or [None])[0]
    if after and after != before:
        print("\n  최신 기준일이 {} -> {} 로 갱신되었습니다.".format(
            _fmt_date(before), _fmt_date(after)))
    else:
        print("\n  최신 기준일 변화 없음 ({}). SEIBro 에 새 공시가 없었습니다.".format(
            _fmt_date(after)))
    _open_dashboard()


def _dates() -> list[str]:
    if not DB.exists():
        return []
    from .store import Store

    return Store(DB).available_dates()


def _history() -> None:
    code = _ask("\n종목 단축코드 (예: 000660) 또는 ETF 코드 앞에 e (예: e091160): ")
    if not code:
        return
    if code.lower().startswith("e"):
        _run(["history", "--etf", code[1:], "--limit", "30"])
    else:
        _run(["history", "--stock", code, "--limit", "30"])


def _backfill() -> None:
    days = _ask("\n소급할 영업일 수 (기본 5, 많으면 오래 걸림): ", "5")
    _run(["--delay", "0.25", "--workers", "5", "backfill", "--days", days])


def _doctor() -> None:
    from . import doctor

    doctor.run()


def _install() -> None:
    print("\nrequests / pandas / tabulate 를 설치합니다.")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
        cwd=ROOT,
    )


ACTIONS = {
    "1": _today,
    "2": lambda: _open_dashboard(True),
    "3": lambda: _run(["detect", "--top", "20"]),
    "4": _backfill,
    "5": _history,
    "6": lambda: _run(["themes"]),
    "7": lambda: _run(["universe"]),
    "8": _status,
    "9": _install,
    "d": _doctor,
    "D": _doctor,
}


def _startup(open_dashboard: bool) -> None:
    """실행 직후: 최신 여부를 먼저 알리고, 그 다음 대시보드를 띄운다.

    최신 여부 안내는 ``--no-open`` 이어도 항상 보여준다 — 이 화면이 "왜 데이터가
    안 바뀌지?" 를 곧바로 설명해 주기 때문이다. 대시보드 그리기는 저장된
    데이터만 쓰므로 수집(네트워크)은 일어나지 않는다.
    """
    print("\n  SEIBro 최신 기준일을 확인하는 중...")
    _freshness(check_online=True)

    dates = _dates()
    if not DB.exists():
        print("\n  아직 수집된 데이터가 없습니다."
              "\n  [9] 라이브러리 설치 -> [1] 최신 데이터 받기 순서로 진행하세요.")
        return
    if len(dates) < 2:
        print("\n  기준일이 {}개뿐이라 비교할 대상이 없습니다."
              "\n  [1] 최신 데이터 받기 를 실행하면 지난 영업일까지 함께 채워집니다."
              .format(len(dates)))
        return
    if not open_dashboard:
        print("\n  (--no-open: 대시보드를 열지 않습니다. [2] 로 언제든 열 수 있습니다.)")
        return
    print("\n  저장된 데이터로 대시보드를 그려서 엽니다. (기준일 {}개)".format(len(dates)))
    print("  * 이 단계는 인터넷에서 새로 받아오지 않습니다 —"
          " 새 데이터가 필요하면 [1] 을 누르세요.")
    _open_dashboard(rebuild=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ThemeETF", add_help=True,
        description="테마 ETF 리밸런싱 감지기 메뉴 실행기",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="시작할 때 대시보드를 자동으로 열지 않는다",
    )
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    ensure_dirs()
    moved = migrate_legacy()

    print(BANNER)
    print("  데이터 출처: 한국예탁결제원 SEIBro")
    print("  산출물 폴더: {}".format(OUTPUT))
    for line in moved:
        print("  산출물 이동: {}".format(line))

    _startup(open_dashboard=not args.no_open
             and not os.environ.get("THEMEETF_NO_OPEN"))

    while True:
        print(MENU)
        try:
            choice = _ask("  선택> ")
        except _Quit:
            print("\n종료합니다.")
            return 0
        if choice.lower() in ("0", "q", "exit", "quit"):
            print("\n종료합니다.")
            return 0
        action = ACTIONS.get(choice)
        if action is None:
            print("  0-9 중에서 선택하세요.")
            continue
        try:
            action()
        except _Quit:
            print("\n종료합니다.")
            return 0
        except SystemExit as exc:
            print("[중단] {}".format(exc))
        except KeyboardInterrupt:
            print("\n[취소]")
        except Exception as exc:  # 메뉴가 죽지 않게 방어
            print("[오류] {}: {}".format(type(exc).__name__, exc))


if __name__ == "__main__":
    raise SystemExit(main())

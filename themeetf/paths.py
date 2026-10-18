"""산출물 경로 한 곳 관리.

프로그램이 만드는 모든 것은 ``output/`` 아래로 모은다.

    output/
      dashboard.html            대화형 대시보드 (항상 같은 경로 -> 북마크 가능)
      data/themeetf.sqlite      스냅샷 저장소
      reports/rebalance_*.html  일자별 정적 리포트
      logs/daily_*.log          배치 실행 로그

``THEMEETF_OUTPUT`` 환경변수로 위치를 바꿀 수 있다. 예를 들어 공유 드라이브에
쌓고 싶으면::

    set THEMEETF_OUTPUT=Z:\\ETF추적
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def output_root() -> Path:
    override = os.environ.get("THEMEETF_OUTPUT")
    return Path(override).expanduser() if override else ROOT / "output"


OUTPUT = output_root()
DATA_DIR = OUTPUT / "data"
REPORT_DIR = OUTPUT / "reports"
LOG_DIR = OUTPUT / "logs"
DB_PATH = DATA_DIR / "themeetf.sqlite"
DASHBOARD_PATH = OUTPUT / "dashboard.html"

# 이전 버전이 쓰던 위치. 있으면 한 번만 output/ 아래로 옮긴다.
_LEGACY = [
    (ROOT / "data" / "themeetf.sqlite", DB_PATH),
    (ROOT / "data" / "themeetf.sqlite-wal", DATA_DIR / "themeetf.sqlite-wal"),
    (ROOT / "data" / "themeetf.sqlite-shm", DATA_DIR / "themeetf.sqlite-shm"),
]


#: 안내 페이지를 알아보기 위한 표식 (중복 교체 방지)
_STUB_MARK = "themeetf-moved-stub"


def _redirect_stub() -> str:
    """옛 대시보드 경로에 남기는 안내 페이지.

    이 파일은 더 이상 갱신되지 않으므로, 북마크로 열었을 때 옛 데이터를 보여주는
    대신 새 위치로 보낸다.
    """
    target = DASHBOARD_PATH.resolve().as_uri()
    return (
        '<meta name="generator" content="' + _STUB_MARK + '">'
        "<title>대시보드가 이동했습니다</title>"
        '<meta http-equiv="refresh" content="3;url={u}">'
        "<style>body{{font:15px/1.6 system-ui,'Segoe UI',sans-serif;margin:0;"
        "display:grid;place-items:center;min-height:100vh;background:#f9f9f7;color:#0b0b0b}}"
        "@media(prefers-color-scheme:dark){{body{{background:#0d0d0d;color:#fff}}}}"
        ".b{{max-width:560px;padding:28px}}h1{{font-size:19px;margin:0 0 10px}}"
        "code{{font-size:13px}}a{{color:#2a78d6}}</style>"
        '<div class="b"><h1>대시보드가 이동했습니다</h1>'
        "<p>이 파일은 더 이상 갱신되지 않는 <b>옛 대시보드</b>입니다. "
        "모든 산출물은 이제 <code>output/</code> 폴더에 모입니다.</p>"
        '<p>3초 뒤 새 대시보드로 이동합니다. 바로 열려면 '
        '<a href="{u}">{p}</a></p>'
        "<p>북마크를 새 주소로 바꿔 주세요.</p></div>"
    ).format(u=target, p=DASHBOARD_PATH)


def ensure_dirs() -> None:
    for d in (OUTPUT, DATA_DIR, REPORT_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def migrate_legacy() -> list[str]:
    """구버전 경로(data/, reports/)의 산출물을 output/ 아래로 이동.

    대상 파일이 이미 있으면 손대지 않는다. 이동한 항목 설명을 반환한다.
    """
    ensure_dirs()
    moved: list[str] = []

    for src, dst in _LEGACY:
        if src.exists() and not dst.exists():
            shutil.move(str(src), str(dst))
            moved.append("{} -> {}".format(src.name, dst))

    legacy_reports = ROOT / "reports"
    if legacy_reports.is_dir() and legacy_reports != REPORT_DIR:
        for src in sorted(legacy_reports.glob("*.html")):
            dst = DASHBOARD_PATH if src.name == "dashboard.html" else REPORT_DIR / src.name
            if not dst.exists():
                shutil.move(str(src), str(dst))
                moved.append("{} -> {}".format(src.name, dst))
            elif src.name == "dashboard.html":
                # 새 대시보드가 이미 있으면 옛 파일은 갱신되지 않는 껍데기다.
                # 북마크를 눌렀을 때 옛 데이터를 최신인 줄 알고 보는 사고를 막기 위해
                # 안내 + 자동 이동 페이지로 덮어쓴다. 이미 교체돼 있으면 그냥 둔다.
                try:
                    already = _STUB_MARK in src.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    already = False
                if not already:
                    src.write_text(_redirect_stub(), encoding="utf-8")
                    moved.append("(구) reports/dashboard.html -> 안내 페이지로 교체")
            else:
                src.unlink()
                moved.append("(중복) reports/{} 삭제".format(src.name))
        if not any(legacy_reports.iterdir()):
            legacy_reports.rmdir()

    legacy_data = ROOT / "data"
    if legacy_data.is_dir() and not any(legacy_data.iterdir()):
        legacy_data.rmdir()

    legacy_logs = ROOT / "logs"
    if legacy_logs.is_dir() and legacy_logs != LOG_DIR:
        for src in sorted(legacy_logs.glob("*.log")):
            dst = LOG_DIR / src.name
            if not dst.exists():
                shutil.move(str(src), str(dst))
                moved.append("{} -> {}".format(src.name, dst))
        if not any(legacy_logs.iterdir()):
            legacy_logs.rmdir()

    return moved

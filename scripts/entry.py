"""PyInstaller 진입점. 번들 실행 시에도 메뉴 런처를 띄운다."""

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # exe 와 같은 폴더를 작업 폴더로 삼아 output/ 이 exe 옆에 생기게 한다.
    os.chdir(Path(sys.executable).parent)
    os.environ.setdefault("THEMEETF_OUTPUT", str(Path(sys.executable).parent / "output"))

from themeetf.launcher import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

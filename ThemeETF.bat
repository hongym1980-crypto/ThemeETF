@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
title ThemeETF - 테마 ETF 리밸런싱 감지기

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

rem 산출물 위치를 바꾸려면 아래 줄의 주석을 풀고 경로를 지정하세요.
rem set "THEMEETF_OUTPUT=Z:\ETF추적"

rem Python 런처(py)를 먼저 찾고, 없으면 python 을 쓴다.
set "PY="
where py >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 set "PY=python"
)

if not defined PY (
  echo.
  echo   [!] Python 을 찾을 수 없습니다.
  echo       https://www.python.org/downloads/ 에서 Python 3.10 이상을 설치한 뒤
  echo       설치 화면의 "Add python.exe to PATH" 를 체크하세요.
  echo.
  pause
  exit /b 1
)

rem 실행하면 저장된 데이터로 대시보드를 다시 그려 브라우저로 띄운 뒤 메뉴를 보여준다.
rem 자동으로 열지 않으려면:  ThemeETF.bat --no-open
%PY% -m themeetf.launcher %*
if errorlevel 1 (
  echo.
  echo   [!] 실행 중 오류가 발생했습니다. 위 메시지를 확인하세요.
  pause
)
endlocal

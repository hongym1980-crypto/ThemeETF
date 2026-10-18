# PyInstaller 로 배포용 단일 실행 파일(.exe)을 만든다.
# 파이썬이 없는 PC 에 나눠줄 때만 필요하다. 본인 PC 에서는 ThemeETF.bat 로 충분하다.
#
#   powershell -ExecutionPolicy Bypass -File scriptsuild_exe.ps1
#
# 결과: dist\ThemeETF.exe  (config/ 와 requirements.txt 는 exe 안에 포함)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pyinstaller 설치 실패" }

python -m PyInstaller `
    --onefile `
    --name ThemeETF `
    --console `
    --add-data "config;config" `
    --hidden-import themeetf.launcher `
    --collect-submodules themeetf `
    scripts/entry.py

if ($LASTEXITCODE -ne 0) { throw "빌드 실패" }
Write-Host ""
Write-Host "완료: $root\dist\ThemeETF.exe"
Write-Host "exe 와 같은 폴더에 data/ reports/ 가 생성됩니다."

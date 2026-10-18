# 테마 ETF PDF 일일 추적 배치.
# Windows 작업 스케줄러에 등록해서 매 영업일 아침에 돌린다.
#
#   schtasks /Create /TN "ThemeETF Daily" /SC DAILY /ST 08:30 `
#     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File <프로젝트 경로>\scripts\daily.ps1"
#
# SEIBro PDF 는 T-1 기준으로 공시되므로, 장 시작 전에 돌리면 전일자 스냅샷을 받는다.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

# 로그도 산출물 폴더 아래로 (THEMEETF_OUTPUT 으로 위치 변경 가능)
$outRoot = if ($env:THEMEETF_OUTPUT) { $env:THEMEETF_OUTPUT } else { Join-Path $root 'output' }
$logDir = Join-Path $outRoot 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory $logDir -Force | Out-Null }
$log = Join-Path $logDir ("daily_{0}.log" -f (Get-Date -Format 'yyyyMMdd'))

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 시작" | Tee-Object -FilePath $log -Append

# 목록 갱신 -> 최신 기준일 수집 -> 빠진 영업일 메우기 -> 감지 -> 리포트 -> 대시보드
python -m themeetf --delay 0.25 --workers 5 daily --top 20 2>&1 |
    Tee-Object -FilePath $log -Append

if ($LASTEXITCODE -ne 0) {
    "!! 종료코드 $LASTEXITCODE" | Tee-Object -FilePath $log -Append
    exit $LASTEXITCODE
}


"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 완료" | Tee-Object -FilePath $log -Append

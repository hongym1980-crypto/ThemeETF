# 테마별 ETF 구성종목(PDF) 추적 및 리밸런싱 감지기

한국예탁결제원(KSD) **증권정보포털 SEIBro** 의 ETF 정보를 매일 수집해,
AI/반도체·2차전지·바이오 등 테마형 ETF의 **구성종목 비중 변화**를 추적하고
운용사가 최근 집중 편입한 **주도주**를 자동으로 포착한다.

---

## 1. 핵심 아이디어 — 가격효과를 걷어낸 리밸런싱 신호

비중(%)만 보면 **가격이 오른 종목이 저절로 "편입 확대"처럼 보인다.** SEIBro PDF 는
비중과 함께 **1CU 구성수량**(`VCTF_QTY`)을 주는데, 이 값은 가격이나 설정/환매와
무관하게 **운용사가 포트폴리오를 바꿀 때만** 움직인다.

실제 관측값 (SK하이닉스 / KODEX 반도체):

| 기준일 | 비중 | 1CU 수량 |
|---|---|---|
| 2026-08-14 | 35.75% | 1,384 |
| 2026-08-19 | 36.76% | 1,384 |
| 2026-08-20 | 35.38% | 1,384 |

비중은 ±1.4pp 흔들렸지만 수량은 그대로다 — 전부 가격효과다. 그래서 이 프로젝트는
다음 두 지표를 쓴다.

```
수량기여 비중변화(pp) = 전일비중 × (당일수량 / 전일수량 − 1)
추정 순매수(원)       = 전일 보유금액 × (당일수량 / 전일수량 − 1)
보유금액              = 보유비중 × ETF 순자산총액
```

실증: 하루치 비교에서 리밸런싱이 감지된 ETF는 **전부 액티브 ETF**(TIME·TIGER
액티브·KODEX 액티브)였고 패시브 지수 ETF는 0.00pp 였다. 지표가 의도대로 작동한다는
뜻이다.

## 2. 데이터 소스

`themeetf/seibro.py` 가 SEIBro 의 ProWorks 트랜잭션 엔드포인트를 직접 호출한다.

```
POST https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp
Content-Type: application/xml; charset=UTF-8

<reqParam action="compstInfoHoldAssetDetailList"
          task="ksd.safe.bip.cnts.etf.process.EtfCompstInfoPTask">
  <ISIN value="KR7091160003"/><STD_DT value="20260820"/>
</reqParam>
```

| 용도 | action | task | 비고 |
|---|---|---|---|
| ETF 전종목 기본정보 | `secnIssuStatPList` | `EtfSetredInfoPTask` | 1,160종목. `select_sorting` 을 비우면 서버 오류 |
| ETF 종목상세 | `compstInfoSecnDetailList` | `EtfCompstInfoPTask` | 운용사/벤치마크/보수 |
| 섹터(FGSC) 비중 | `compstInfoHoldAssetList` | `EtfCompstInfoPTask` | 전체 섹터 + 기준일 |
| **보유종목 TOP10** | `compstInfoHoldAssetDetailList` | `EtfCompstInfoPTask` | 비중 + 1CU수량. **최신 기준일만** |
| **종목→보유 ETF (역인덱스)** | `secnHoldStatStkPList` | `EtfMartInfoPTask` | 비중·1CU수량·순자산. **과거 일자 지원**, 페이징 |

인증키·회원가입이 필요 없다. 초당 호출은 `--delay` 로 제한된다(기본 0.35초).

### 왜 역인덱스를 함께 쓰는가

SEIBro 가 ETF 단위로 공개하는 구성종목은 **TOP10 까지**다(페이징 파라미터를 넣어도
10행 고정). 그래서 수집을 2단계로 돌린다.

1. 테마 ETF 각각의 섹터 비중 + TOP10.
2. 1단계에서 모인 종목들을 `secnHoldStatStkPList` 로 되짚어, 그 종목이 *모든* ETF
   에서 갖는 비중/수량을 받는다 → 테마 ETF 의 **11위 이하 비중까지 채워지고**,
   덤으로 그 종목의 ETF 시장 전체 보유금액(운용사 수 포함)도 얻는다.

또한 TOP10 은 최신일만 응답하지만 역인덱스는 과거 일자를 그대로 주므로
`backfill` 명령으로 **과거 이력을 소급 구축**할 수 있다.

### 검토했지만 쓰지 않은 경로

- **공공데이터포털(data.go.kr)**: ETF 오픈API는 「금융위원회_증권상품시세정보」
  하나뿐이고 시세만 준다. KSD 의 ETF 구성종목 API는 등록돼 있지 않다.
- **KSD 오픈플랫폼(openplatform.seibro.or.kr)**: 기업·주식·채권·파생결합·외화증권
  5개 부문뿐이고 ETF 부문이 없다. 법인 신청/심사도 필요하다.
- **KRX 정보데이터시스템(data.krx.co.kr)**: 로그인 벽이 생겨 익명 JSON 호출이
  `LOGOUT` 을 반환한다. KRX Open API 에도 ETF는 「일별매매정보」만 있다.

## 3. 설치

```bash
pip install -r requirements.txt
```

Python 3.10+ / `requests`, `pandas`, `tabulate` 만 쓴다.

## 4. 실행 파일 — `ThemeETF.bat`

**`ThemeETF.bat` 를 더블클릭**하면 ① SEIBro 최신 기준일과 내 데이터를 비교해 알려주고,
② 저장된 데이터로 대시보드를 그려 브라우저로 열고, ③ 메뉴를 띄운다.

```
  ┌───────────────────────────────────────────────────────────────────┐
  │  T h e m e E T F   ·   테마 ETF 구성종목(PDF) 리밸런싱 감지기      │
  └───────────────────────────────────────────────────────────────────┘

  데이터 출처: 한국예탁결제원 SEIBro
  산출물 폴더: ...\ThemeETF\output

  ── 데이터 상태 ──────────────────────────────
   내 데이터 최신 기준일 : 2026-09-02
   SEIBro 최신 기준일    : 2026-09-02
   → 최신 상태입니다. 새로 받을 데이터가 없습니다.
  ────────────────────────────────────────────

  브라우저로 엽니다: ...\ThemeETF\output\dashboard.html

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
```

### 시작 화면이 먼저 알려주는 것

실행하면 SEIBro 최신 기준일을 확인해서 **내 데이터가 최신인지 먼저 보여준다.**

```
  ── 데이터 상태 ──────────────────────────────
   내 데이터 최신 기준일 : 2026-08-24
   SEIBro 최신 기준일    : 2026-09-02
   → 새 데이터가 있습니다. [1] 최신 데이터 받기 를 실행하세요.
  ────────────────────────────────────────────
```

> **중요**: 시작할 때 자동으로 열리는 대시보드는 **이미 받아둔 데이터로 다시 그린
> 것**이다. 새 데이터를 받아오지 않는다 — 그래서 파일의 "만든 시각"은 바뀌어도
> 데이터 기준일은 그대로다. **최신 데이터를 받으려면 반드시 `[1]` 을 눌러야 한다.**
> 대시보드 상단에도 `최신 상태` / `갱신 필요` 배지가 표시된다.

`[1] 최신 데이터 받기` 한 번이면 최신화가 끝난다.

```
1/5  ETF 목록 갱신
2/5  최신 기준일 구성종목 수집          ← SEIBro 최신일(T-1) 수집
3/5  빠진 영업일 6일 메우기              ← 며칠 쉬었어도 그 사이가 자동으로 채워짐
4/5  리밸런싱 감지                      ← 콘솔 리포트 + HTML
5/5  대시보드 갱신                      ← output/dashboard.html

데이터 최신화 결과
  SEIBro 최신 기준일 : 2026-09-02
  내 데이터 최신     : 2026-09-02
  새로 추가된 기준일  : 2026-09-02, 2026-09-01, ...
  직전 실행 시 최신   : 2026-08-24
```

**처음 쓸 때**: `[9]` → `[1]`.

자동 실행을 끄려면:

```bat
ThemeETF.bat --no-open
```

또는 환경변수 `THEMEETF_NO_OPEN=1`.

- 배치는 `py -3` 을 먼저 찾고 없으면 `python` 을 쓴다. 둘 다 없으면 설치 안내를 띄운다.
- UTF-8 코드페이지를 직접 설정하므로 한글이 깨지지 않는다.
- 메뉴 없이 쓰려면 `python -m themeetf.launcher`, 또는 아래 §5 의 서브커맨드.

### 파이썬이 없는 PC 에 배포할 때

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

PyInstaller 로 `dist\ThemeETF.exe` (단일 파일)를 만든다. exe 옆에 `output/` 이
생성된다. 본인 PC 에서는 `ThemeETF.bat` 로 충분하고, 이 빌드는 배포용이다.

## 4-1. 산출물 폴더 — `output/`

프로그램이 만드는 모든 것은 `output/` 아래 한곳에 모인다.

```
output/
  dashboard.html            대화형 대시보드 (경로가 고정되므로 브라우저 북마크 가능)
  data/themeetf.sqlite      스냅샷 저장소 (모든 기준일의 구성종목/섹터/이벤트)
  reports/rebalance_*.html  일자별 정적 리포트
  logs/daily_*.log          배치 실행 로그
```

위치를 바꾸려면 환경변수 `THEMEETF_OUTPUT` 을 지정한다. 공유 드라이브에 쌓아
팀에서 같이 보는 용도로 쓸 수 있다.

```bat
set THEMEETF_OUTPUT=Z:\ETF추적
```

`ThemeETF.bat` 안에도 같은 줄이 주석으로 들어 있어 풀어 쓰면 된다.
CLI 로는 `--db` 로 저장소만 따로 지정할 수도 있다.

구버전 경로(`data/`, `reports/`, `logs/`)에 파일이 남아 있으면 **처음 실행할 때
`output/` 아래로 자동 이동**하고 무엇을 옮겼는지 출력한다. 대상 파일이 이미 있으면
건드리지 않는다.

## 5. 사용법 (CLI)

```bash
# 1) ETF 전종목 갱신 + 테마 분류
python -m themeetf universe

# 2) 오늘자 PDF 스냅샷 수집 (테마 ETF 전체, 약 1~2분)
python -m themeetf --delay 0.25 --workers 5 collect

# 3) 과거 영업일 소급 구축 (첫 실행 직후 바로 비교하고 싶을 때)
python -m themeetf --delay 0.25 --workers 5 backfill --days 5

# 4) 리밸런싱 감지 + 주도주 리포트 (+ HTML)
python -m themeetf detect --top 20 --html

# 5) 대화형 대시보드
python -m themeetf dashboard --open

# 매일 한 방에 (목록 갱신 -> 수집 -> 빠진 영업일 메우기 -> 감지 -> 리포트 -> 대시보드)
python -m themeetf daily --top 20
python -m themeetf daily --catch-up 0     # 빠진 날 메우기 없이 최신일만
```

보조 명령:

```bash
python -m themeetf themes --theme "AI/반도체"     # 테마별 편입 ETF
python -m themeetf history --stock 000660         # 종목 비중/수량 추이
python -m themeetf history --etf 091160
python -m themeetf detect --prev 20260814         # 비교 구간 직접 지정
python -m themeetf detect --qty-eps 0.001         # 민감도 조정
```

### 자동화 (Windows)

```powershell
schtasks /Create /TN "ThemeETF Daily" /SC DAILY /ST 08:30 `
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File $PWD\scripts\daily.ps1"
```

SEIBro PDF 는 **T-1 기준**으로 공시되므로 장 시작 전에 돌리면 전일자 스냅샷을 받는다.

## 6. 출력 예시

```
테마 ETF 구성종목(PDF) 리밸런싱 리포트   2026-08-20 vs 2026-08-14
비교 대상: 1704개 (ETF, 종목) 페어 | 감지 이벤트 105건

[테마별 자금 흐름] 추정 순매수 = 1CU 수량 변화 x 전일 보유금액
테마          ETF    종목    회전율(pp)    순매수(억)    총매매(억)
조선/기계         7    29      39.28     50.80   1209.60
AI/반도체       21    72       5.94     34.80     86.30
금융/지주        22    97      33.94    -33.50    873.00

[주도주 후보]
  #  테마        코드      종목명           스코어  수량기여(pp)  순매수(억)  증/감ETF  운용사  신규
  1  금융/지주     0126Z0  삼성에피스홀딩스   13.24      +3.57       +98   1/0      1     1
  2  조선/기계     439260  대한조선        11.36      +3.71       +78   2/1      1
  3  바이오/헬스케어  009420  한올바이오파마     9.59      +2.40       +52   2/0      2

[리밸런싱 발생 ETF]
테마      코드      ETF                운용사         회전율(pp)  신규  편출  확대  축소
금융/지주   307520  TIGER 지주회사        미래에셋자산운용     18.02    1    1    7    4
금융/지주   495060  TIME 코리아밸류업액티브   타임폴리오자산운용    13.34    2    5    3    0
```

`--html` 을 주면 `output/reports/rebalance_YYYYMMDD.html` 에 다크모드 대응 리포트가
생성된다.

### 대시보드 — `output/dashboard.html`

```bash
python -m themeetf dashboard --open
```

파일 하나로 끝나는 자체완결형 HTML. 외부 CDN·서버·인터넷 연결이 필요 없으므로
사내망에서도 그대로 열리고, 메일에 붙여 그대로 공유할 수 있다.

### 시작일 · 종료일을 직접 고른다

**일자별 원자료를 파일에 그대로 심고 비교 계산은 브라우저에서 한다.** 미리 계산해 둔
몇 개 구간만 보는 게 아니라, 보유한 기준일 중 **어떤 두 날짜든** 골라 그 사이를 즉시
비교할 수 있다. 계산식(수량기여 비중변화·추정 순매수·NEW/DROP/ADD/TRIM 판정·주도주
스코어)은 `detect.py` 와 동일하며, 같은 구간에 대해 Python 결과와 수치가 일치하는 것을
확인했다.

- 시작일 > 종료일로 고르면 자동으로 뒤집고, 같은 날을 고르면 한 칸 벌린다.
- 빠른 선택 버튼: 직전 영업일 / 1주 / 1개월 / 전체 기간.
- 기준일이 많아지면 파일이 커지므로 최신 **60일**까지만 담는다 (`--max-dates`).

기간이 길어질수록 신호가 또렷해진다. 예: 17영업일(08-10 → 09-02) 구간에서는
감지 이벤트 294건, 추정 순매수 +1,532억, 회전율 281pp — 하루치(16건 / -28.7억 /
8.1pp)와 비교하면 축적된 리밸런싱이 드러난다.

구성:

| 영역 | 내용 |
|---|---|
| 필터 (상단 1줄) | **시작일 → 종료일** (보유한 어떤 조합이든) · 빠른 선택(직전 영업일 / 1주 / 1개월 / 전체) · **테마** · 라이트/다크 |
| 신선도 배너 | 내 데이터 최신 기준일 vs SEIBro 최신 기준일 + `최신 상태` / `갱신 필요` 배지 |
| 히어로 | 추정 순매수 합계 (테마 전체 선택 시 ETF·종목 중복 제거) |
| 스탯 타일 | 감지 이벤트 / 리밸런싱 ETF / 주도주 후보 / 총매매 규모 / 추적 커버리지 |
| 테마별 추정 순매수 | 발산형 가로 막대 — 유입 적색, 유출 청색, 무변동은 명시 |
| 테마별 회전율 | 단일 색조 가로 막대 |
| 주도주 후보 TOP 20 | 양방향 스코어 미터 + 보유금액 4일 스파크라인, **행 클릭 → 드릴다운** |
| 드릴다운 | 그 종목을 담은 ETF별 비중 막대 + 1CU수량 전·당일 대조표 |
| 리밸런싱 발생 ETF | 회전율 막대 + 신규/편출/확대/축소 |
| 구성종목 변경 이벤트 | 추정금액 순 전체 목록 |

시각화 규칙(dataviz 기준 준수):

- **발산형은 적/청 2색**(`#e34948` / `#2a78d6`, 다크 `#e66767` / `#3987e5`) — 색맹
  시뮬레이션·명도대역·채도·대비 6개 검사 전부 통과. 국내 증시 관행대로 유입이 적색.
- 크기만 나타내는 값(회전율·비중)은 **단일 색조 한 가지**. 값에 따라 색을 바꾸지
  않는다(막대 길이가 이미 값을 말하므로).
- 테마는 색이 아니라 **행 레이블**로 구분한다. 테마가 9개라 색 슬롯 8개 한도를
  넘기 때문.
- **모든 차트에 `표` 버튼**이 있어 수치 표로 전환된다. 색으로만 전달되는 정보는 없다.
- 이중 축 없음. 순매수와 회전율은 단위가 달라 **차트를 나눴다**.
- 축 눈금은 1/2/2.5/5 배수로 떨어지고, 그리드는 실선 헤어라인.
- 신선도 배지는 시리즈 색이 아니라 **상태 팔레트**(good/warning)를 점으로만 쓰고
  글자는 잉크 색 — 색 단독으로 의미를 전달하지 않는다.

### 주도주 스코어

```
score = 2.0 × 수량기여비중합(pp)
      + 2.0 × 신규편입 ETF수  −  2.0 × 편출 ETF수
      + 0.6 × (비중확대 ETF수 − 비중축소 ETF수)
      + 0.5 × 비중확대 운용사수
      + 1.5 × sign(순매수) · log₁₀(1 + |순매수(억)|)
```

여러 운용사가 동시에 편입을 늘린 종목이 위로 올라오게 설계했다. 가중치는
`themeetf/detect.py` 의 `LeaderRow.score` 에서 바로 수정할 수 있다.

## 7. 테마 정의

`config/themes.json` 에서 ETF 종목명 정규식으로 정의한다. JSON 이므로 정규식의
역슬래시는 두 번 쓴다(`\\bAI\\b`).

```json
"AI/반도체": {
  "include": ["반도체", "\\bAI\\b", "인공지능", "HBM", "온디바이스", "팹리스"],
  "codes": ["091160", "091230", "395160", "469150"]
}
```

- `codes` 는 순자산/패턴 조건을 무시하고 무조건 편입한다.
- `defaults.exclude` 로 인버스·레버리지·채권형을 전역 배제한다.
- `defaults.min_nav_eok` 는 순자산 하한(억원). 기본 300.
- `scope`: `domestic`(기본) / `global` / `all`. 국내 종목만 역인덱스가 채워지므로
  기본은 `domestic`.

현재 기본 설정으로 9개 테마 · 107종목이 잡힌다.

## 8. 구조

```
ThemeETF.bat            ← 더블클릭 실행기
themeetf/
  launcher.py  메뉴형 실행기 (ThemeETF.bat 이 띄운다)
  cli.py       argparse 진입점 (universe/collect/backfill/detect/daily/dashboard/…)
  seibro.py    SEIBro 트랜잭션 클라이언트 (스레드별 세션 + 전역 레이트리밋 + 재시도)
  store.py     SQLite 스키마/업서트 (holding, sector, stock_market, event, snapshot)
  universe.py  ETF 전종목 갱신 + 테마 분류
  collect.py   1단계 TOP10 수집 / 2단계 역인덱스 확장 / 과거 backfill
  detect.py    일자 간 비교, NEW·DROP·ADD·TRIM 판정, 주도주 스코어링
  report.py    콘솔(tabulate) + 자체완결형 HTML 리포트
  dashboard.py 자체완결형 대화형 HTML 대시보드 (SVG 차트 직접 생성)
  paths.py     산출물 경로 한 곳 관리 + 구버전 경로 자동 이동
config/themes.json      테마 정의
scripts/daily.ps1       작업 스케줄러용 일일 배치
scripts/build_exe.ps1   PyInstaller 배포 빌드
output/                 모든 산출물 (§4-1)
  dashboard.html
  data/themeetf.sqlite
  reports/rebalance_*.html
  logs/daily_*.log
```

이벤트 4종:

| 종류 | 판정 |
|---|---|
| `NEW` | 전일 부재 → 당일 비중 ≥ `min_weight`(0.05pp) |
| `DROP` | 전일 비중 ≥ `min_weight` → 당일 부재 |
| `ADD` | 1CU 수량 증가율 ≥ `qty_eps`(0.5%) 이고 수량기여 비중변화 ≥ `weight_eps`(0.02pp) |
| `TRIM` | 같은 조건의 감소 방향 |

## 8-1. 최신화가 실패할 때 — `[D] 연결 진단`

```bash
python -m themeetf doctor
```

`[1] 최신 데이터 받기` 가 실패하면 **자동으로 진단이 돌아간다.** 수집 코드나 SEIBro
문제보다 **VPN/DNS** 가 원인인 경우가 훨씬 많기 때문이다.

진단 순서: SEIBro 도메인 해석 → (실패 시) 일반 도메인도 해석되는지 → 공용 DNS 로는
해석되는지 → VPN/프록시 흔적 → TCP 443 → 실제 API 호출 1회.

실제로 잡힌 사례:

```
  [실패] DNS: seibro.or.kr 이름 해석 실패 ([Errno 11002] getaddrinfo failed)
  [정보] 일반 인터넷은 정상입니다 (google.com -> 172.217.209.113)
  [정보] 공용 DNS 8.8.8.8 로 조회: 124.137.74.135
  [정보] 공용 DNS 1.1.1.1 로 조회: 실패
  [정보] DNS 가 Cloudflare WARP 의 내장 리졸버(127.0.2.x)로 잡혀 있습니다.
  [정상] TCP 443 연결: 124.137.74.135 연결됨

결론: 도메인은 정상인데 이 PC 의 DNS 가 seibro.or.kr 를 해석하지 못합니다.
조치: VPN(특히 Cloudflare WARP)을 잠시 끄고 다시 실행하세요.
```

즉 **도메인·서버·프로그램 모두 정상**이고, VPN 의 DNS 리졸버만 이 도메인을 풀지
못하는 상태였다. TCP 443 은 VPN 을 통해서도 붙으므로, 이름만 해석되면 바로 동작한다.

VPN 을 잠시 끄려면:

```bat
"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe" disconnect
```

다시 켤 때는 `connect`. WARP 앱 스위치를 Off 로 내려도 같다. 회사 정책상 VPN 을
유지해야 한다면 IT 담당자에게 *"`seibro.or.kr` 이 VPN DNS 에서 해석되지 않는다.
공용 DNS 로는 `124.137.74.135` 로 정상 해석된다"* 고 전달하면 된다.

에러 메시지도 원인을 바로 말해 준다 — DNS 실패 / 타임아웃 / 연결 거부 / 인증서 /
프록시를 구분해 조치까지 함께 출력한다.

### WARP 를 꺼도 다시 붙는다면

`Always On` 이 켜져 있어서다. WARP 앱 > **Settings > Advanced > Connection options**
에서 `Always On` 을 먼저 해제한 뒤 연결을 끊어야 유지된다. 진단이 이 상태를 감지하면
안내에 함께 띄운다.

### 이름 해석 대체 경로 (`themeetf/dnsfix.py`)

VPN 을 끌 수 없는 환경을 위해, **평소 DNS 로 해석이 실패할 때만** 공용 DNS(Google
DoH)로 주소를 받아 그 호스트 하나에만 적용한다. `curl --resolve` 와 같은 동작이다.

- **터널을 우회하지 않는다.** 실제 트래픽은 그대로 VPN 을 통해 나간다
  (진단에서 확인했듯 TCP 443 은 VPN 을 통해 이미 정상이다).
- TLS SNI·Host 헤더·인증서 검증은 **원래 도메인 이름 그대로** 유지된다
  (`socket.getaddrinfo` 만 해당 호스트에 한해 치환).
- 적용되면 매 실행마다 콘솔에 사실을 알린다.
- 끄려면 `--no-dns-fallback` 또는 `THEMEETF_NO_DNS_FALLBACK=1`.

근본 해결은 어디까지나 VPN 쪽 설정이다. 이 경로는 그때까지 도구가 멈추지 않게 하는
임시 수단으로 본다.

## 8-2. 수집이 덜 된 날이 생겼을 때

증상은 **"어제까지 보이던 신규편입/비중확대가 갑자기 사라짐"** 으로 나타난다.
비교는 양쪽 날짜에 **모두 있는 ETF** 만 대상으로 하므로, 최신 날짜가 부실하면
비교 대상이 통째로 줄어 이벤트가 거의 안 잡힌다.

확인:

```bash
python -m themeetf themes          # 테마별 ETF 수가 평소(약 107종목)와 비슷한가
python -m themeetf doctor          # 네트워크 문제인가
```

```sql
-- 기준일별 수집량. ETF 수가 갑자기 줄었으면 그날이 부실한 것이다.
SELECT std_dt, COUNT(DISTINCT isin) etfs, COUNT(DISTINCT stock_code) stocks, COUNT(*) n
FROM holding GROUP BY std_dt ORDER BY std_dt DESC LIMIT 10;
```

복구는 그날을 다시 수집하면 된다 (업서트라 재실행이 안전하다).

```bash
python -m themeetf universe                        # 테마 매핑부터 정상화
python -m themeetf collect                         # 최신 기준일 다시 수집
python -m themeetf backfill --dates 20260915       # 과거 특정일 다시 채우기
python -m themeetf detect --top 20 --html
python -m themeetf dashboard
```

### 실제로 있었던 원인 — 이른 아침 실행

SEIBro 는 새 기준일의 종목 목록을 먼저 내보내고 **순자산총액(`NETASST_TOTAMT`)은
조금 뒤에 채운다.** 08:01 에 실행했더니 1,170종목 전부 순자산이 빈 값으로 왔고,
그 결과:

1. `etf_master.nav_total` 이 전부 NULL 이 됨
2. 테마 분류의 순자산 하한(`min_nav_eok`) 이 NULL 을 0 으로 취급해 **전부 탈락**
3. 살아남은 건 `themes.json` 의 `codes` 에 직접 적은 6종목뿐
4. 그 6종목만 수집 → 최신 기준일이 ETF 6종목짜리 껍데기가 됨
5. 대시보드 비교에서 신규편입이 사라짐

**지금은 3중으로 막혀 있다.**

- `etf_master` 업서트가 **NULL 로 기존 값을 덮지 않는다** (`keep_on_null`).
  순자산·종가·거래량·보수가 비어 오면 직전 값을 지킨다.
- 테마 분류에서 **순자산이 NULL 이면 걸러내지 않는다.** 모르는 값을 0 으로
  취급하지 않는다.
- 분류 결과가 직전 대비 **절반 미만으로 급감하면 덮어쓰지 않고 중단**한다
  (`ClassificationCollapsed`). 기존 매핑을 유지하므로 수집은 계속 정상 동작한다.
  의도한 변경이라면 `--force-classify`.
- 수집 직후 ETF 수가 직전 기준일의 절반 미만이면 **경고를 띄운다.**

그래도 가장 확실한 예방은 **장 시작 이후(09:00 이후)에 실행**하는 것이다.

## 9. 알아둘 한계

- **PDF 는 T-1 기준.** 당일 리밸런싱은 다음 영업일에 보인다. "실시간"의 하한이 여기다.
  오늘 실행해도 최신 기준일은 어제(직전 영업일)다.
- **대시보드는 자동으로 갱신되지 않는다.** 파일을 열어두기만 해서는 새 데이터가
  들어오지 않는다. `[1] 최신 데이터 받기` 또는 `python -m themeetf daily` 를 돌려야
  한다. 대시보드 상단 배지가 `갱신 필요` 인지 확인하면 된다.
- **커버리지는 TOP10 + 역인덱스 확장분.** 테마 ETF 가 담은 소형 비중 종목 중, 어떤
  테마 ETF 의 TOP10 에도 없고 직전 스냅샷에도 없던 종목은 유니버스에 들어오지 않는다.
  일단 어느 ETF 의 TOP10 에 한 번 걸리면 그 뒤로는 계속 추적된다.
- **`backfill` 의 유니버스는 현재 종목 기준.** 과거에만 존재했던 종목은 소급되지
  않는다. 정확한 이력은 매일 `collect` 를 돌려 쌓아야 한다.
- 한쪽 날짜에만 스냅샷이 있는 ETF는 **비교에서 제외**하고 건수만 보고한다
  (전 종목이 NEW/DROP 으로 잡히는 오탐을 막기 위해).
- **추정 순매수는 추정이다.** 1CU 수량 변화율 × 전일 보유금액이므로, 같은 날 CU 수가
  크게 변한 경우 실제 체결금액과 차이가 난다. 방향과 상대적 크기를 보는 지표다.
- 공휴일 달력을 갖고 있지 않다. `backfill` 은 주말만 건너뛰고, 휴장일은 응답이 비면
  "데이터 없음"으로 넘어간다.
- SEIBro 는 공개 화면의 백엔드다. `--delay` 를 과도하게 낮추지 말 것.

## 10. 데이터 직접 질의

```bash
sqlite3 output/data/themeetf.sqlite
```

```sql
-- 특정 종목의 ETF 시장 전체 보유금액 추이
SELECT std_dt, etf_cnt, mngco_cnt, ROUND(hold_value/1e8) AS 보유금액_억
FROM stock_market WHERE stock_code = '000660' ORDER BY std_dt DESC;

-- 최근 감지된 신규 편입
SELECT std_dt, theme, stock_code, isin, ROUND(weight,2) AS 비중
FROM event WHERE kind = 'NEW' ORDER BY std_dt DESC LIMIT 20;

-- ETF 하나의 PDF 스냅샷
SELECT h.std_dt, h.stock_name, ROUND(h.weight,2) w, h.qty_per_cu, h.source
FROM holding h JOIN etf_master m ON m.isin = h.isin
WHERE m.code = '091160' AND h.std_dt = (SELECT MAX(std_dt) FROM holding)
ORDER BY h.weight DESC;
```

---

데이터 출처: 한국예탁결제원 증권정보포털 SEIBro (ETF 종목발행현황 · 보유자산명세 ·
종목보유현황). 투자 판단의 근거로 쓰기 전 원본을 확인할 것.

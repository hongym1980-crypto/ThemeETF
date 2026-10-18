"""ETF 매수 신호(NEW 신규편입 / ADD 비중확대) 이후 주가 흐름 분석.

DB(holding)에 쌓인 연속 기준일 쌍을 모두 비교해 NEW/ADD 이벤트를 뽑고,
해당 종목의 일별 종가를 네이버 금융에서 받아 이벤트 이후 수익률을 계산한다.

    python price_data/analyze_post_event.py --start 20260810 --end 20260907

산출물 (모두 이 폴더 아래):
    prices/<종목코드>.csv     종목별 일별 시세 (재실행 시 캐시 재사용, --refresh 로 갱신)
    prices/KOSPI.csv          벤치마크
    events.csv                ETF 단위 원천 이벤트 (NEW/ADD)
    signals.csv               (기준일, 종목) 단위로 묶은 신호 + 이후 수익률
    summary.md                집계 결과
"""

from __future__ import annotations

import argparse
import ast
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from themeetf.detect import KIND_ADD, KIND_NEW, Thresholds, compare  # noqa: E402
from themeetf.paths import DB_PATH  # noqa: E402
from themeetf.store import Store  # noqa: E402

HERE = Path(__file__).resolve().parent
PRICES = HERE / "prices"
HORIZONS = (1, 3, 5, 10)          # 이벤트 이후 영업일
NAVER = ("https://api.finance.naver.com/siseJson.naver"
         "?symbol={sym}&requestType=1&startTime={s}&endTime={e}&timeframe=day")
HEADERS = {"User-Agent": "Mozilla/5.0"}


# ------------------------------------------------------------------ 시세

def fetch_daily(symbol: str, start: str, end: str, *, refresh: bool = False,
                delay: float = 0.2) -> pd.DataFrame:
    """네이버 일별 시세. 컬럼: date, open, high, low, close, volume."""
    path = PRICES / "{}.csv".format(symbol)
    if path.exists() and not refresh:
        df = pd.read_csv(path, dtype={"date": str})
        if not df.empty and df["date"].max() >= end:
            return df
    url = NAVER.format(sym=symbol, s=start, e=end)
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    rows = ast.literal_eval(resp.text.strip())
    time.sleep(delay)
    df = pd.DataFrame(rows[1:], columns=rows[0])
    df = df.rename(columns={"날짜": "date", "시가": "open", "고가": "high",
                            "저가": "low", "종가": "close", "거래량": "volume"})
    df = df[["date", "open", "high", "low", "close", "volume"]]
    df["date"] = df["date"].astype(str)
    df = df[df["close"] > 0].sort_values("date").reset_index(drop=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return df


# ------------------------------------------------------------------ 이벤트

def collect_events(store: Store, start: str, end: str
                   ) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """연속 기준일 쌍의 NEW/ADD 이벤트.

    역인덕스 수집은 '조회한 종목'만 holding 에 들어가므로, 직전 기준일에 아예
    조회되지 않은 종목은 실제 신규편입인지 수집 범위 확대인지 구분할 수 없다.
    stock_market(종목, 기준일) 에 행이 있으면 그날 조회된 것이므로, NEW 는
    직전일에 조회 기록이 있는 경우만 인정하고 나머지는 uncertain 으로 분리한다.
    """
    dates = sorted(d for d in store.available_dates() if start <= d <= end)
    covered = {(r["stock_code"], r["std_dt"]) for r in store.query(
        "SELECT stock_code, std_dt FROM stock_market")}
    th = Thresholds()
    rows, uncertain = [], []
    for prev, curr in zip(dates, dates[1:]):
        rep = compare(store, curr, prev, thresholds=th)
        diffs = {(d.isin, d.stock_code): d for d in rep.diffs}
        for ev in rep.events:
            if ev["kind"] not in (KIND_NEW, KIND_ADD):
                continue
            d = diffs[(ev["isin"], ev["stock_code"])]
            target = rows
            if ev["kind"] == KIND_NEW and (d.stock_code, prev) not in covered:
                target = uncertain
            target.append({
                "std_dt": curr, "prev_dt": prev, "theme": d.theme,
                "etf_code": d.etf_code, "etf_name": d.etf_name,
                "stock_code": d.stock_code, "stock_name": d.stock_name,
                "kind": ev["kind"], "weight": d.weight, "prev_weight": d.prev_weight,
                "d_weight": ev["d_weight"], "d_qty_pct": ev["d_qty_pct"],
                "d_value_bil": round(ev["d_value"] / 1e8, 2),   # 억원
            })
    return pd.DataFrame(rows), pd.DataFrame(uncertain), dates


def to_signals(events: pd.DataFrame) -> pd.DataFrame:
    """(기준일, 종목) 단위로 묶는다. 여러 ETF 가 같은 날 샀으면 n_etfs 가 커진다."""
    g = events.groupby(["std_dt", "stock_code"])
    sig = g.agg(
        stock_name=("stock_name", "first"),
        n_etfs=("etf_code", "nunique"),
        n_new=("kind", lambda s: int((s == KIND_NEW).sum())),
        n_add=("kind", lambda s: int((s == KIND_ADD).sum())),
        themes=("theme", lambda s: " | ".join(sorted(set(s)))),   # 테마명에 '/' 포함
        etfs=("etf_name", lambda s: "; ".join(sorted(set(s)))),
        flow_bil=("d_value_bil", "sum"),
        max_d_weight=("d_weight", "max"),
    ).reset_index()
    sig["kind"] = sig["n_new"].map(lambda n: KIND_NEW if n > 0 else KIND_ADD)
    sig["flow_bil"] = sig["flow_bil"].round(1)
    return sig


# ------------------------------------------------------------------ 수익률

def attach_returns(sig: pd.DataFrame, prices: dict[str, pd.DataFrame],
                   kospi: pd.DataFrame) -> pd.DataFrame:
    cal = kospi["date"].tolist()                       # 거래일 캘린더
    kclose = kospi.set_index("date")["close"]
    out = []
    for _, r in sig.iterrows():
        px = prices.get(r["stock_code"])
        rec = dict(r)
        if px is None or px.empty:
            rec["note"] = "시세없음"
            out.append(rec)
            continue
        close = px.set_index("date")["close"]
        d0 = r["std_dt"]
        if d0 not in close.index or d0 not in cal:
            rec["note"] = "기준일 시세없음"
            out.append(rec)
            continue
        i0 = cal.index(d0)
        rec["close_t0"] = close[d0]
        # 이벤트 전 5영업일 수익률 (추격 매수였는지)
        if i0 >= 5 and cal[i0 - 5] in close.index:
            rec["pre5"] = close[d0] / close[cal[i0 - 5]] - 1
        for k in HORIZONS:
            if i0 + k < len(cal) and cal[i0 + k] in close.index:
                dk = cal[i0 + k]
                rec["r{}".format(k)] = close[dk] / close[d0] - 1
                rec["x{}".format(k)] = rec["r{}".format(k)] - (kclose[dk] / kclose[d0] - 1)
        # 공개 후 실제 따라 살 수 있는 시점(T+1 종가) 진입 기준
        if i0 + 1 < len(cal) and cal[i0 + 1] in close.index:
            d1 = cal[i0 + 1]
            for k in (5, 10):
                if i0 + k < len(cal) and cal[i0 + k] in close.index:
                    rec["r1to{}".format(k)] = close[cal[i0 + k]] / close[d1] - 1
        out.append(rec)
    df = pd.DataFrame(out)
    pct_cols = [c for c in df.columns
                if c == "pre5" or (c[0] in "rx" and c[1:].replace("to", "").isdigit())]
    df[pct_cols] = (df[pct_cols] * 100).round(2)
    return df


# ------------------------------------------------------------------ 요약

def _stats(df: pd.DataFrame, label: str) -> dict:
    row = {"구분": label, "신호수": len(df)}
    for k in HORIZONS:
        col = "r{}".format(k)
        s = df[col].dropna() if col in df else pd.Series(dtype=float)
        if s.empty:
            continue
        row["T+{} 평균".format(k)] = round(s.mean(), 2)
        row["T+{} 중앙".format(k)] = round(s.median(), 2)
        row["T+{} 승률".format(k)] = round((s > 0).mean() * 100, 0)
        x = df["x{}".format(k)].dropna()
        row["T+{} 초과".format(k)] = round(x.mean(), 2)
        row["T+{} n".format(k)] = int(len(s))
    return row


def summarize(sig: pd.DataFrame, dates: list[str], last_px: str) -> str:
    ok = sig[sig["r1"].notna()] if "r1" in sig else sig.iloc[0:0]
    lines = ["# ETF 매수 신호 이후 주가 흐름", ""]
    lines.append("- 분석 구간: {} ~ {} (기준일 {}개, 비교쌍 {}개)".format(
        dates[0], dates[-1], len(dates), len(dates) - 1))
    lines.append("- 시세 마지막 일자: {}  (뒤쪽 이벤트는 T+5/T+10 이 아직 없음 → n 확인)".format(last_px))
    lines.append("- 신호 {}건 (종목 {}개) 중 수익률 계산 가능 {}건".format(
        len(sig), sig["stock_code"].nunique(), len(ok)))
    lines.append("- 수익률 %: 기준일(T0) 종가 → T+k 종가. 초과 = KOSPI 대비. "
                 "승률 = 양(+)인 비율")
    lines.append("")

    def table(rows: list[dict], title: str) -> None:
        rows = [r for r in rows if r["신호수"] > 0]
        if not rows:
            return
        df = pd.DataFrame(rows)
        lines.append("## " + title)
        lines.append("")
        lines.append(df.to_markdown(index=False))
        lines.append("")

    table([_stats(ok, "전체"),
           _stats(ok[ok["kind"] == KIND_NEW], "NEW 신규편입"),
           _stats(ok[ok["kind"] == KIND_ADD], "ADD 비중확대")], "1. 신호 종류별")
    table([_stats(ok[ok["n_etfs"] == 1], "ETF 1개"),
           _stats(ok[ok["n_etfs"] >= 2], "ETF 2개 이상 동시매수"),
           _stats(ok[ok["n_etfs"] >= 3], "ETF 3개 이상")], "2. 동시 매수 ETF 수")
    table([_stats(ok[ok["flow_bil"] >= 50], "추정 순매수 50억+"),
           _stats(ok[(ok["flow_bil"] >= 10) & (ok["flow_bil"] < 50)], "10~50억"),
           _stats(ok[ok["flow_bil"] < 10], "10억 미만")], "3. 추정 순매수 규모")
    if "pre5" in ok:
        table([_stats(ok[ok["pre5"] > 5], "직전 5일 +5% 이상 (추격)"),
               _stats(ok[(ok["pre5"] <= 5) & (ok["pre5"] >= -5)], "직전 5일 ±5% 이내"),
               _stats(ok[ok["pre5"] < -5], "직전 5일 -5% 이하 (저가매수)")],
              "4. 매수 전 5일 흐름별")
    theme_rows = []
    exploded = ok.assign(theme=ok["themes"].str.split(" | ", regex=False)).explode("theme")
    for th, g in exploded.groupby("theme"):
        theme_rows.append(_stats(g, th))
    table(sorted(theme_rows, key=lambda r: -r["신호수"]), "5. 테마별")
    if "r1to5" in ok:
        s5, s10 = ok["r1to5"].dropna(), ok["r1to10"].dropna()
        lines.append("## 6. 공개 후 따라 살 경우 (T+1 종가 진입)")
        lines.append("")
        lines.append("| 보유 | n | 평균 % | 중앙 % | 승률 % |")
        lines.append("|---|---|---|---|---|")
        for lab, s in (("T+1→T+5", s5), ("T+1→T+10", s10)):
            if not s.empty:
                lines.append("| {} | {} | {:.2f} | {:.2f} | {:.0f} |".format(
                    lab, len(s), s.mean(), s.median(), (s > 0).mean() * 100))
        lines.append("")

    cols = ["std_dt", "stock_code", "stock_name", "kind", "n_etfs", "flow_bil",
            "themes", "pre5", "r1", "r5", "r10", "x5"]
    cols = [c for c in cols if c in ok.columns]
    have5 = ok[ok["r5"].notna()] if "r5" in ok else ok.iloc[0:0]
    if not have5.empty:
        lines.append("## 7. T+5 상위 10")
        lines.append("")
        lines.append(have5.nlargest(10, "r5")[cols].to_markdown(index=False))
        lines.append("")
        lines.append("## 8. T+5 하위 10")
        lines.append("")
        lines.append(have5.nsmallest(10, "r5")[cols].to_markdown(index=False))
        lines.append("")
    multi = ok[ok["n_etfs"] >= 2].sort_values(["std_dt", "n_etfs"], ascending=[False, False])
    if not multi.empty:
        lines.append("## 9. 2개 이상 ETF 동시 매수 신호 전체")
        lines.append("")
        lines.append(multi[cols].to_markdown(index=False))
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default="20260810")
    ap.add_argument("--end", default="20260907")
    ap.add_argument("--price-end", default=None, help="시세 조회 종료일 (기본: 오늘)")
    ap.add_argument("--refresh", action="store_true", help="시세 캐시 무시하고 다시 받기")
    ap.add_argument("--delay", type=float, default=0.2)
    args = ap.parse_args()

    PRICES.mkdir(parents=True, exist_ok=True)
    price_end = args.price_end or pd.Timestamp.today().strftime("%Y%m%d")
    price_start = (pd.Timestamp(args.start) - pd.Timedelta(30, "D")).strftime("%Y%m%d")

    store = Store(DB_PATH)
    events, uncertain, dates = collect_events(store, args.start, args.end)
    if events.empty:
        print("구간 내 NEW/ADD 이벤트가 없습니다.")
        return 1
    events.to_csv(HERE / "events.csv", index=False, encoding="utf-8-sig")
    uncertain.to_csv(HERE / "events_uncertain_new.csv", index=False, encoding="utf-8-sig")
    sig = to_signals(events)
    print("기준일 {}개, ETF단위 이벤트 {}건 → 신호 {}건 / 종목 {}개".format(
        len(dates), len(events), len(sig), sig["stock_code"].nunique()))
    print("  제외: 직전일 미조회 종목의 NEW {}건 (events_uncertain_new.csv)".format(len(uncertain)))

    kospi = fetch_daily("KOSPI", price_start, price_end, refresh=args.refresh, delay=args.delay)
    prices: dict[str, pd.DataFrame] = {}
    codes = sorted(sig["stock_code"].unique())
    fail = []
    for i, code in enumerate(codes, 1):
        try:
            prices[code] = fetch_daily(code, price_start, price_end,
                                       refresh=args.refresh, delay=args.delay)
        except Exception as exc:  # 한 종목 실패로 전체가 멈추지 않게
            fail.append((code, str(exc)[:60]))
        if i % 25 == 0 or i == len(codes):
            print("  시세 {}/{}".format(i, len(codes)))
    if fail:
        print("시세 실패 {}건: {}".format(len(fail), fail[:5]))

    sig = attach_returns(sig, prices, kospi)
    sig = sig.sort_values(["std_dt", "flow_bil"], ascending=[False, False])
    sig.to_csv(HERE / "signals.csv", index=False, encoding="utf-8-sig")

    md = summarize(sig, dates, kospi["date"].max())
    (HERE / "summary.md").write_text(md, encoding="utf-8")
    print(md)

    # 대시보드도 함께 갱신 (실패해도 분석 산출물은 이미 저장됨)
    try:
        from build_dashboard import build as build_dashboard
        print("\n대시보드: {}".format(build_dashboard()))
    except Exception as exc:  # noqa: BLE001
        print("\n대시보드 생성 실패: {}".format(exc))
    print("산출물: {}".format(HERE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

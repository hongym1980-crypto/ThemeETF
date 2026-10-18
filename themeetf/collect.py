"""일일 PDF(구성종목) 스냅샷 수집.

SEIBro 가 ETF 단위로 공개하는 구성종목은 보유종목 TOP10 이다. 그래서 2단계로
커버리지를 넓힌다.

1. **직접 수집** — 테마 ETF 각각에 대해 섹터 비중 + 보유종목 TOP10.
2. **역인덱스 확장** — 1단계에서 나온 종목들을 ``secnHoldStatStkPList`` 로
   되짚어, 해당 종목이 *모든* ETF 에서 갖는 비중/1CU수량을 받는다. 덕분에
   테마 ETF 의 TOP10 밖(11위 이하) 비중까지 채워지고, 동시에 그 종목의 ETF
   시장 전체 보유금액도 얻는다.

수집 단위 필드
--------------
- ``weight``      보유비중 %
- ``qty_per_cu``  1CU 구성수량. 설정/환매와 무관하므로 순수 리밸런싱 신호다.
- ``hold_value``  비중 x ETF 순자산총액 = 실제 보유금액(원)
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .seibro import SeibroClient, SeibroError, short_code, to_float
from .store import Store

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class CollectResult:
    std_dt: str = ""
    etfs: int = 0
    holdings: int = 0
    sectors: int = 0
    stocks_expanded: int = 0
    api_calls: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        base = (
            "기준일 {dt} | ETF {e}종목 | 구성종목 {h}행 | 섹터 {s}행 | "
            "역인덱스 확장 {x}종목 | API {c}회"
        ).format(dt=self.std_dt, e=self.etfs, h=self.holdings,
                 s=self.sectors, x=self.stocks_expanded, c=self.api_calls)
        if self.errors:
            base += " | 실패 {n}건".format(n=len(self.errors))
        return base


class Collector:
    """테마 ETF 의 PDF 스냅샷을 수집해 저장소에 적재."""

    def __init__(
        self,
        client: SeibroClient,
        store: Store,
        *,
        workers: int = 4,
        expand: bool = True,
        max_expand_stocks: int | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.workers = max(1, workers)
        self.expand = expand
        self.max_expand_stocks = max_expand_stocks

    # ------------------------------------------------------------------ public

    def run(self, theme: str | None = None) -> CollectResult:
        targets = self.store.theme_etfs(theme)
        by_isin: dict[str, dict[str, Any]] = {}
        for row in targets:
            by_isin.setdefault(row["isin"], row)
        if not by_isin:
            raise SystemExit(
                "대상 ETF가 없습니다. 먼저 `python -m themeetf universe` 를 실행하세요."
            )

        result = CollectResult()
        _, market_dt = self.client.etf_count()
        result.std_dt = market_dt

        log.info("1단계: ETF %d종목의 섹터/TOP10 수집 (기준일 %s)", len(by_isin), market_dt)
        direct = self._collect_direct(list(by_isin.values()), market_dt, result)

        stock_codes: set[str] = {h["stock_code"] for h in direct["holdings"]}
        # 직전 스냅샷에 있던 종목도 확장 대상에 포함시킨다. 그러지 않으면 TOP10
        # 밖으로 밀려난 종목이 "편출(DROP)"로 오탐된다.
        stock_codes |= self._previous_stocks(set(by_isin), result.std_dt)
        if self.expand and stock_codes:
            codes = sorted(stock_codes)
            if self.max_expand_stocks:
                codes = codes[: self.max_expand_stocks]
            log.info("2단계: 역인덱스로 %d종목 x 전체 ETF 확장", len(codes))
            self._collect_xref(codes, market_dt, set(by_isin), result)
            result.stocks_expanded = len(codes)

        result.api_calls = self.client.calls
        return result

    def _previous_stocks(self, target_isins: set[str], std_dt: str) -> set[str]:
        """대상 ETF 의 가장 최근 과거 기준일에 담겨 있던 종목 코드."""
        prev = self.store.scalar(
            "SELECT MAX(std_dt) FROM holding WHERE std_dt < ?", (std_dt,)
        )
        if not prev:
            return set()
        rows = self.store.query(
            "SELECT DISTINCT stock_code, isin FROM holding WHERE std_dt = ?", (prev,)
        )
        return {r["stock_code"] for r in rows if r["isin"] in target_isins}

    # ----------------------------------------------------------------- 1단계

    def _collect_direct(
        self, etfs: list[dict[str, Any]], market_dt: str, result: CollectResult
    ) -> dict[str, list[dict[str, Any]]]:
        holdings: list[dict[str, Any]] = []
        sectors: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []

        def work(etf: dict[str, Any]) -> tuple[list, list, dict] | None:
            isin = etf["isin"]
            try:
                std_dt, sec_rows = self.client.resolve_std_dt(isin, market_dt)
                if not std_dt:
                    return None
                top = self.client.top_holdings(isin, std_dt)
            except SeibroError as exc:
                result.errors.append("{} {}: {}".format(etf["code"], etf["name"], exc))
                return None

            nav = etf.get("nav_total") or 0.0
            h_rows = []
            for r in top:
                code = short_code(r.get("G_VCTF_ISIN", ""))
                if not code:
                    continue
                weight = to_float(r.get("G_HOLD_IMPO"))
                h_rows.append(
                    {
                        "isin": isin,
                        "std_dt": std_dt,
                        "stock_code": code,
                        "stock_name": r.get("G_KOR_SECN_NM", ""),
                        "weight": weight,
                        "qty_per_cu": to_float(r.get("G_VCTF_QTY")),
                        "close": to_float(r.get("G_LDAY_CPRI")),
                        "chg_rate": to_float(r.get("G_RATE")),
                        "rank": int(to_float(r.get("G_RNUM"), 0) or 0) or None,
                        "hold_value": (weight / 100.0 * nav) if weight and nav else None,
                        "source": "top10",
                    }
                )

            s_rows = []
            for r in sec_rows:
                w = to_float(r.get("HOLD_IMPO"))
                if w is None:
                    continue
                s_rows.append(
                    {
                        "isin": isin,
                        "std_dt": std_dt,
                        "sector_cd": r.get("INDTP_INDEX_CD", ""),
                        "sector_nm": r.get("INDTP_INDEX_NM", ""),
                        "etf_weight": w,
                        "market_weight": to_float(r.get("MART_IMPO")),
                        "diff": to_float(r.get("DEFRATE")),
                    }
                )

            snap = {
                "isin": isin,
                "std_dt": std_dt,
                "holding_cnt": len(h_rows),
                "sector_cnt": len(s_rows),
                "collected_at": _now(),
            }
            return h_rows, s_rows, snap

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for out in pool.map(work, etfs):
                if out is None:
                    continue
                h_rows, s_rows, snap = out
                holdings.extend(h_rows)
                sectors.extend(s_rows)
                snapshots.append(snap)

        self.store.upsert_holdings(holdings)
        self.store.upsert_sectors(sectors)
        self.store.upsert_snapshots(snapshots)

        result.etfs = len(snapshots)
        result.holdings += len(holdings)
        result.sectors += len(sectors)
        if snapshots:
            result.std_dt = max(s["std_dt"] for s in snapshots)
        return {"holdings": holdings, "sectors": sectors}

    # ----------------------------------------------------------------- 2단계

    def _collect_xref(
        self,
        stock_codes: list[str],
        market_dt: str,
        target_isins: set[str],
        result: CollectResult,
    ) -> int:
        holdings: list[dict[str, Any]] = []
        market: list[dict[str, Any]] = []

        def work(code: str) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
            try:
                rows = self.client.holders_of_stock(code, market_dt, page_size=400)
            except SeibroError as exc:
                result.errors.append("xref {}: {}".format(code, exc))
                return None
            if not rows:
                return None

            std_dt = next((r["STD_DT"] for r in rows if r.get("STD_DT")), market_dt)
            name = next((r["KOR_SECN_NM"] for r in rows if r.get("VCTF_ISIN")), "")
            mngcos: set[str] = set()
            total_value = 0.0
            out: list[dict[str, Any]] = []

            for r in rows:
                etf_isin = r.get("ISIN", "")
                weight = to_float(r.get("HOLD_IMPO"))
                nav = to_float(r.get("NETASST_TOTAMT")) or 0.0
                value = (weight / 100.0 * nav) if weight else None
                if value:
                    total_value += value
                if r.get("MNGCO_NM"):
                    mngcos.add(r["MNGCO_NM"])
                if etf_isin not in target_isins:
                    continue
                out.append(
                    {
                        "isin": etf_isin,
                        "std_dt": std_dt,
                        "stock_code": code,
                        "stock_name": None,  # 역인덱스는 ETF명을 주므로 종목명은 보류
                        "weight": weight,
                        "qty_per_cu": to_float(r.get("VCTF_QTY")),
                        "close": None,
                        "chg_rate": None,
                        "rank": None,
                        "hold_value": value,
                        "source": "xref",
                    }
                )

            agg = {
                "stock_code": code,
                "std_dt": std_dt,
                "stock_name": None,
                "etf_cnt": len(rows),
                "mngco_cnt": len(mngcos),
                "hold_value": total_value or None,
            }
            return out, agg

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for out in pool.map(work, stock_codes):
                if out is None:
                    continue
                rows, agg = out
                holdings.extend(rows)
                market.append(agg)

        # TOP10 으로 들어온 행은 종목명/종가/순위까지 있어 더 풍부하므로 덮어쓰지 않는다.
        protected = {
            (r["isin"], r["std_dt"], r["stock_code"])
            for r in self.store.query(
                "SELECT isin, std_dt, stock_code FROM holding "
                "WHERE source = 'top10' AND std_dt = ?",
                (market_dt,),
            )
        }
        fresh = [
            h for h in holdings
            if (h["isin"], h["std_dt"], h["stock_code"]) not in protected
        ]
        self.store.upsert_holdings(fresh)
        self._fill_stock_names(market)
        self.store.upsert_stock_market(market)
        result.holdings += len(fresh)
        return len(fresh)

    # -------------------------------------------------------------- 과거 백필

    def backfill(
        self, dates: list[str], theme: str | None = None, stocks: list[str] | None = None
    ) -> list[CollectResult]:
        """역인덱스로 과거 기준일을 채운다.

        SEIBro 의 보유종목 TOP10(``compstInfoHoldAssetDetailList``)은 최신
        기준일만 응답하지만, 종목보유현황(``secnHoldStatStkPList``)은 과거 일자를
        그대로 돌려준다. 종목 유니버스는 이미 수집된 최신 스냅샷에서 가져온다.
        """
        targets = self.store.theme_etfs(theme)
        target_isins = {r["isin"] for r in targets}
        if not target_isins:
            raise SystemExit("대상 ETF가 없습니다. `universe` 를 먼저 실행하세요.")

        codes = stocks or sorted(
            {
                r["stock_code"]
                for r in self.store.query(
                    "SELECT DISTINCT stock_code FROM holding WHERE isin IN ({})".format(
                        ",".join("?" * len(target_isins))
                    ),
                    sorted(target_isins),
                )
            }
        )
        if not codes:
            raise SystemExit("종목 유니버스가 비어 있습니다. `collect` 를 먼저 실행하세요.")
        if self.max_expand_stocks:
            codes = codes[: self.max_expand_stocks]

        results: list[CollectResult] = []
        for dt in dates:
            res = CollectResult(std_dt=dt)
            log.info("백필 %s: %d종목 역인덱스 조회", dt, len(codes))
            written = self._collect_xref(codes, dt, target_isins, res)
            res.stocks_expanded = len(codes)
            res.api_calls = self.client.calls
            if written:
                etf_rows = self.store.query(
                    "SELECT isin, COUNT(*) AS n FROM holding WHERE std_dt = ? "
                    "GROUP BY isin", (dt,)
                )
                self.store.upsert_snapshots([
                    {
                        "isin": r["isin"], "std_dt": dt, "holding_cnt": r["n"],
                        "sector_cnt": 0, "collected_at": _now(),
                    }
                    for r in etf_rows
                ])
                res.etfs = len(etf_rows)
            results.append(res)
        return results

    def _fill_stock_names(self, market: list[dict[str, Any]]) -> None:
        """TOP10 수집분에 있는 종목명을 역인덱스 집계에 채워 넣는다."""
        names = {
            r["stock_code"]: r["stock_name"]
            for r in self.store.query(
                "SELECT stock_code, MAX(stock_name) AS stock_name FROM holding "
                "WHERE stock_name IS NOT NULL GROUP BY stock_code"
            )
        }
        for row in market:
            row["stock_name"] = names.get(row["stock_code"])


def missing_business_days(have: set[str], latest: str, cap: int = 10) -> list[str]:
    """``latest`` 바로 아래부터 과거로 훑으며 빠진 영업일을 최신순으로 모은다.

    ``latest`` 자신은 이미 수집된 날이므로 하루 전부터 시작한다. 이미 가진
    날짜를 만나면 멈춘다 — 그 아래는 이어져 있다고 보기 때문이다. 며칠 쉬었다가
    실행해도 그 사이 영업일이 자동으로 메워진다.
    """
    out: list[str] = []
    day = datetime.strptime(latest, "%Y%m%d").date() - timedelta(days=1)
    while len(out) < cap:
        stamp = day.strftime("%Y%m%d")
        if stamp in have:
            break
        if day.weekday() < 5:
            out.append(stamp)
        day -= timedelta(days=1)
    return out


def business_days_back(anchor: str, count: int) -> list[str]:
    """``anchor`` (YYYYMMDD) 직전 영업일(주말 제외)을 최신순으로 ``count`` 개 반환.

    공휴일은 달력만으로 알 수 없으므로, 데이터가 없는 날짜는 백필 단계에서
    응답이 비면 자연히 건너뛰어진다.
    """
    day = datetime.strptime(anchor, "%Y%m%d").date()
    out: list[str] = []
    while len(out) < count:
        day -= timedelta(days=1)
        if day.weekday() >= 5:
            continue
        out.append(day.strftime("%Y%m%d"))
    return out

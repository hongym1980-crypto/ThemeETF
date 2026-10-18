"""리밸런싱 감지와 주도주 스코어링.

핵심 아이디어
-------------
비중(%) 변화만 보면 가격이 오른 종목이 저절로 "편입 확대"로 보인다. SEIBro PDF 는
**1CU 구성수량**(``VCTF_QTY``)을 함께 주는데, 이 값은 설정/환매나 가격과 무관하게
운용사가 포트폴리오를 바꿀 때만 움직인다. 따라서

    수량기여 비중변화(pp) = 전일비중 x (당일수량 / 전일수량 - 1)

이 순수 리밸런싱 신호이고, 여기에 전일 보유금액을 곱하면 금액 기준 추정 순매수가
나온다. 주도주 스코어는 이 값을 테마 내 여러 ETF·운용사에 걸쳐 합산한 것이다.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .store import Store

log = logging.getLogger(__name__)

KIND_NEW = "NEW"
KIND_DROP = "DROP"
KIND_ADD = "ADD"
KIND_TRIM = "TRIM"


@dataclass
class Thresholds:
    """감지 민감도."""

    qty_eps: float = 0.005      # 1CU 수량 변화율 |Δ| 하한 (0.5%)
    weight_eps: float = 0.02    # 비중 변화 |Δ| 하한 (pp)
    etf_turnover: float = 0.50  # ETF 리밸런싱 판정 회전율 (pp)
    min_weight: float = 0.05    # NEW/DROP 로 볼 최소 비중 (pp)


@dataclass
class Diff:
    """(테마, ETF, 종목) 1건의 일자 간 변화."""

    theme: str
    isin: str
    etf_code: str
    etf_name: str
    mngco: str
    nav_total: float
    stock_code: str
    stock_name: str
    prev_weight: float | None
    weight: float | None
    prev_qty: float | None
    qty: float | None
    prev_value: float | None
    value: float | None

    @property
    def d_weight(self) -> float:
        return (self.weight or 0.0) - (self.prev_weight or 0.0)

    @property
    def qty_ratio(self) -> float | None:
        """당일수량/전일수량 - 1. 한쪽이 없으면 None."""
        if not self.prev_qty or self.qty is None:
            return None
        return self.qty / self.prev_qty - 1.0

    @property
    def d_weight_qty(self) -> float:
        """수량 변화만으로 설명되는 비중 변화(pp). 가격효과 제거."""
        if self.prev_weight is None:
            return self.weight or 0.0            # 신규 편입: 비중 전체가 신규
        if self.weight is None:
            return -(self.prev_weight or 0.0)    # 편출: 비중 전체가 이탈
        ratio = self.qty_ratio
        if ratio is None:
            return 0.0
        return self.prev_weight * ratio

    @property
    def est_flow(self) -> float:
        """금액 기준 추정 순매수(원). 수량 변화 x 전일 보유금액."""
        if self.prev_value is None:
            return self.value or 0.0
        if self.value is None:
            return -(self.prev_value or 0.0)
        ratio = self.qty_ratio
        if ratio is None:
            return 0.0
        return self.prev_value * ratio

    def kind(self, th: Thresholds) -> str | None:
        if self.prev_weight is None and (self.weight or 0.0) >= th.min_weight:
            return KIND_NEW
        if self.weight is None and (self.prev_weight or 0.0) >= th.min_weight:
            return KIND_DROP
        ratio = self.qty_ratio
        if ratio is None:
            return None
        if abs(ratio) < th.qty_eps or abs(self.d_weight_qty) < th.weight_eps:
            return None
        return KIND_ADD if ratio > 0 else KIND_TRIM


@dataclass
class DiffReport:
    std_dt: str
    prev_dt: str
    diffs: list[Diff] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    skipped_etfs: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _load(store: Store, std_dt: str, theme: str | None) -> dict[tuple[str, str, str], dict[str, Any]]:
    sql = """
        SELECT t.theme, h.isin, m.code AS etf_code, m.name AS etf_name,
               m.mngco, m.nav_total, h.stock_code,
               COALESCE(h.stock_name, sm.stock_name, h.stock_code) AS stock_name,
               h.weight, h.qty_per_cu, h.hold_value
        FROM holding h
        JOIN etf_theme t USING (isin)
        JOIN etf_master m ON m.isin = h.isin
        LEFT JOIN stock_market sm
               ON sm.stock_code = h.stock_code AND sm.std_dt = h.std_dt
        WHERE h.std_dt = ?
    """
    params: list[Any] = [std_dt]
    if theme:
        sql += " AND t.theme = ?"
        params.append(theme)
    rows = store.query(sql, params)
    return {(r["theme"], r["isin"], r["stock_code"]): r for r in rows}


def compare(
    store: Store,
    std_dt: str,
    prev_dt: str,
    *,
    theme: str | None = None,
    thresholds: Thresholds | None = None,
) -> DiffReport:
    """두 기준일 사이의 구성종목 변화를 계산한다."""
    th = thresholds or Thresholds()
    curr = _load(store, std_dt, theme)
    prev = _load(store, prev_dt, theme)

    # 양쪽 날짜에 모두 스냅샷이 있는 ETF만 비교한다. 한쪽만 있으면 전 종목이
    # NEW 또는 DROP 으로 잡히므로 제외하고 따로 보고한다.
    curr_etfs = {k[1] for k in curr}
    prev_etfs = {k[1] for k in prev}
    both = curr_etfs & prev_etfs
    skipped = sorted(curr_etfs ^ prev_etfs)

    report = DiffReport(std_dt=std_dt, prev_dt=prev_dt, skipped_etfs=skipped)
    keys = {k for k in set(curr) | set(prev) if k[1] in both}

    for key in sorted(keys):
        c = curr.get(key)
        p = prev.get(key)
        base = c or p
        assert base is not None
        d = Diff(
            theme=key[0],
            isin=key[1],
            etf_code=base["etf_code"],
            etf_name=base["etf_name"],
            mngco=base["mngco"] or "",
            nav_total=base["nav_total"] or 0.0,
            stock_code=key[2],
            stock_name=(c or p or {}).get("stock_name") or key[2],
            prev_weight=(p or {}).get("weight"),
            weight=(c or {}).get("weight"),
            prev_qty=(p or {}).get("qty_per_cu"),
            qty=(c or {}).get("qty_per_cu"),
            prev_value=(p or {}).get("hold_value"),
            value=(c or {}).get("hold_value"),
        )
        report.diffs.append(d)
        kind = d.kind(th)
        if kind:
            report.events.append(
                {
                    "std_dt": std_dt,
                    "prev_dt": prev_dt,
                    "theme": d.theme,
                    "isin": d.isin,
                    "stock_code": d.stock_code,
                    "kind": kind,
                    "weight": d.weight,
                    "prev_weight": d.prev_weight,
                    "d_weight": round(d.d_weight, 4),
                    "qty_per_cu": d.qty,
                    "prev_qty": d.prev_qty,
                    "d_qty_pct": round((d.qty_ratio or 0.0) * 100, 4),
                    "d_value": round(d.est_flow, 0),
                    "detected_at": _now(),
                }
            )
    return report


# --------------------------------------------------------------------- 집계


@dataclass
class LeaderRow:
    """주도주 후보 1건."""

    theme: str
    stock_code: str
    stock_name: str
    conviction: float = 0.0     # 수량기여 비중변화 합(pp)
    est_flow: float = 0.0       # 추정 순매수(원)
    etfs_up: int = 0
    etfs_down: int = 0
    mngco_up: int = 0
    new_cnt: int = 0
    drop_cnt: int = 0
    avg_weight: float = 0.0
    etf_cnt: int = 0
    market_etf_cnt: int | None = None
    contributors: list[str] = field(default_factory=list)

    @property
    def net_breadth(self) -> int:
        return self.etfs_up - self.etfs_down

    @property
    def score(self) -> float:
        """해석 가능한 가중합. 항목별 기여를 그대로 보여줄 수 있게 단순화."""
        flow_eok = self.est_flow / 1e8
        flow_term = math.copysign(math.log10(1 + abs(flow_eok)), flow_eok)
        return round(
            2.0 * self.conviction
            + 2.0 * self.new_cnt
            - 2.0 * self.drop_cnt
            + 0.6 * self.net_breadth
            + 0.5 * self.mngco_up
            + 1.5 * flow_term,
            3,
        )


def leaders(
    report: DiffReport,
    *,
    thresholds: Thresholds | None = None,
    store: Store | None = None,
) -> list[LeaderRow]:
    """테마별 종목 단위로 리밸런싱 신호를 합산해 주도주 후보를 만든다."""
    th = thresholds or Thresholds()
    acc: dict[tuple[str, str], LeaderRow] = {}
    weights: dict[tuple[str, str], list[float]] = {}
    mngcos: dict[tuple[str, str], set[str]] = {}

    for d in report.diffs:
        key = (d.theme, d.stock_code)
        row = acc.get(key)
        if row is None:
            row = LeaderRow(theme=d.theme, stock_code=d.stock_code, stock_name=d.stock_name)
            acc[key] = row
            weights[key] = []
            mngcos[key] = set()
        if d.stock_name and d.stock_name != d.stock_code:
            row.stock_name = d.stock_name

        row.conviction += d.d_weight_qty
        row.est_flow += d.est_flow
        row.etf_cnt += 1 if d.weight is not None else 0
        if d.weight is not None:
            weights[key].append(d.weight)

        kind = d.kind(th)
        if kind == KIND_NEW:
            row.new_cnt += 1
        elif kind == KIND_DROP:
            row.drop_cnt += 1
        if kind in (KIND_NEW, KIND_ADD):
            row.etfs_up += 1
            mngcos[key].add(d.mngco)
            row.contributors.append("{}{:+.2f}pp".format(d.etf_name, d.d_weight_qty))
        elif kind in (KIND_DROP, KIND_TRIM):
            row.etfs_down += 1
            row.contributors.append("{}{:+.2f}pp".format(d.etf_name, d.d_weight_qty))

    for key, row in acc.items():
        ws = weights[key]
        row.avg_weight = sum(ws) / len(ws) if ws else 0.0
        row.mngco_up = len({m for m in mngcos[key] if m})
        row.conviction = round(row.conviction, 4)

    if store is not None:
        market = {
            r["stock_code"]: r["etf_cnt"]
            for r in store.query(
                "SELECT stock_code, etf_cnt FROM stock_market WHERE std_dt = ?",
                (report.std_dt,),
            )
        }
        for row in acc.values():
            row.market_etf_cnt = market.get(row.stock_code)

    out = [r for r in acc.values() if r.etfs_up or r.etfs_down]
    out.sort(key=lambda r: r.score, reverse=True)
    return out


@dataclass
class EtfRebalance:
    """ETF 단위 리밸런싱 요약."""

    theme: str
    isin: str
    etf_code: str
    etf_name: str
    mngco: str
    turnover: float = 0.0      # Σ|수량기여 비중변화| (pp)
    new_cnt: int = 0
    drop_cnt: int = 0
    add_cnt: int = 0
    trim_cnt: int = 0
    est_flow_abs: float = 0.0


def etf_rebalances(
    report: DiffReport, *, thresholds: Thresholds | None = None
) -> list[EtfRebalance]:
    th = thresholds or Thresholds()
    acc: dict[tuple[str, str], EtfRebalance] = {}
    for d in report.diffs:
        key = (d.theme, d.isin)
        row = acc.get(key)
        if row is None:
            row = EtfRebalance(
                theme=d.theme, isin=d.isin, etf_code=d.etf_code,
                etf_name=d.etf_name, mngco=d.mngco or "",
            )
            acc[key] = row
        kind = d.kind(th)
        if not kind:
            continue
        row.turnover += abs(d.d_weight_qty)
        row.est_flow_abs += abs(d.est_flow)
        if kind == KIND_NEW:
            row.new_cnt += 1
        elif kind == KIND_DROP:
            row.drop_cnt += 1
        elif kind == KIND_ADD:
            row.add_cnt += 1
        else:
            row.trim_cnt += 1

    rows = [r for r in acc.values() if r.turnover > 0]
    for r in rows:
        r.turnover = round(r.turnover, 3)
    rows.sort(key=lambda r: (r.new_cnt + r.drop_cnt, r.turnover), reverse=True)
    return [r for r in rows if r.turnover >= min(th.etf_turnover, 1e-9) or r.new_cnt or r.drop_cnt]


def persist_events(store: Store, report: DiffReport) -> int:
    if not report.events:
        return 0
    return store.upsert_events(report.events)


def theme_flow(report: DiffReport) -> list[dict[str, Any]]:
    """테마 단위 요약 (회전율·추정 순매수·이벤트 수)."""
    acc: dict[str, dict[str, Any]] = {}
    for d in report.diffs:
        row = acc.setdefault(
            d.theme,
            {"theme": d.theme, "etfs": set(), "stocks": set(),
             "turnover": 0.0, "net_flow": 0.0, "gross_flow": 0.0},
        )
        row["etfs"].add(d.isin)
        row["stocks"].add(d.stock_code)
        row["turnover"] += abs(d.d_weight_qty)
        row["net_flow"] += d.est_flow
        row["gross_flow"] += abs(d.est_flow)
    out = []
    for row in acc.values():
        out.append(
            {
                "theme": row["theme"],
                "etf_cnt": len(row["etfs"]),
                "stock_cnt": len(row["stocks"]),
                "turnover": round(row["turnover"], 2),
                "net_flow_eok": round(row["net_flow"] / 1e8, 1),
                "gross_flow_eok": round(row["gross_flow"] / 1e8, 1),
            }
        )
    out.sort(key=lambda r: abs(r["net_flow_eok"]), reverse=True)
    return out


def top_events(report: DiffReport, kinds: Iterable[str], limit: int = 20) -> list[dict[str, Any]]:
    wanted = set(kinds)
    rows = [e for e in report.events if e["kind"] in wanted]
    rows.sort(key=lambda e: abs(e["d_weight"] or 0.0), reverse=True)
    return rows[:limit]

"""ETF 유니버스 갱신과 테마 분류."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .seibro import SeibroClient, to_float
from .store import Store

log = logging.getLogger(__name__)

DEFAULT_THEME_CONFIG = Path(__file__).resolve().parent.parent / "config" / "themes.json"

#: 이 수 미만이면 "원래 작은 매핑" 으로 보고 급감 판정을 하지 않는다.
MIN_SANE_MAPPING = 20
#: 직전 매핑 대비 이 비율 미만으로 줄면 이상으로 본다.
COLLAPSE_RATIO = 0.5


class ClassificationCollapsed(RuntimeError):
    """테마 분류 결과가 비정상적으로 급감한 경우.

    대개 SEIBro 응답에서 순자산총액 같은 필드가 비어 와서 필터가 전부를 걸러낸
    상황이다. 이때 매핑을 덮어쓰면 그날 수집이 통째로 비어 버리므로 중단한다.
    """

    def __init__(self, previous: int, current: int) -> None:
        self.previous = previous
        self.current = current
        super().__init__(
            "테마 분류가 {}건 -> {}건으로 급감했습니다. 기존 매핑을 유지했습니다."
            .format(previous, current)
        )


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_theme_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_THEME_CONFIG
    with cfg_path.open(encoding="utf-8") as fh:
        cfg = json.load(fh)
    defaults = cfg.get("defaults", {})
    themes: dict[str, dict[str, Any]] = {}
    for name, spec in cfg.get("themes", {}).items():
        merged = {
            "scope": spec.get("scope", defaults.get("scope", "domestic")),
            "min_nav_eok": spec.get("min_nav_eok", defaults.get("min_nav_eok", 0)),
            "include": list(spec.get("include", [])),
            "exclude": list(defaults.get("exclude", [])) + list(spec.get("exclude", [])),
            "codes": [str(c).zfill(6) for c in spec.get("codes", [])],
        }
        themes[name] = merged
    return themes


def refresh_universe(client: SeibroClient, store: Store) -> tuple[int, str]:
    """SEIBro 종목발행현황 전체를 받아 ``etf_master`` 를 갱신한다."""
    total, market_dt = client.etf_count()
    log.info("SEIBro ETF 전종목 %d개 (기준일 %s) 수집 중...", total, market_dt)
    raw = client.etf_list(page_size=400)

    now = _now()
    rows: list[dict[str, Any]] = []
    for r in raw:
        isin = r.get("ISIN", "")
        code = r.get("SHOTN_ISIN", "")
        if not isin or not code:
            continue
        rows.append(
            {
                "isin": isin,
                "code": code,
                "name": r.get("KOR_SECN_NM", ""),
                "mngco": r.get("REP_SECN_NM", ""),
                "big_sort": r.get("ETF_BIG_SORT_NM", ""),
                "sort": r.get("ETF_SORT_NM", ""),
                "nav_total": to_float(r.get("NETASST_TOTAMT")),
                "close": to_float(r.get("CPRI")),
                "volume": to_float(r.get("TR_QTY")),
                "total_fee": to_float(r.get("TOT_RECM_RATE")),
                "std_dt": r.get("STD_DT", market_dt),
                "updated_at": now,
            }
        )
    store.upsert_etf_master(rows)
    log.info("etf_master %d건 갱신 (전체 %d건 보고)", len(rows), total)
    return len(rows), market_dt


def _compiled(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def classify(store: Store, themes: dict[str, dict[str, Any]], *,
             allow_collapse: bool = False) -> dict[str, int]:
    """ETF 종목명/분류를 규칙에 태워 ``etf_theme`` 을 다시 만든다.

    결과가 직전 매핑 대비 급감하면 :class:`ClassificationCollapsed` 를 올리고
    기존 매핑을 그대로 둔다 (``allow_collapse=True`` 면 무시하고 덮어쓴다).
    """
    etfs = store.query(
        "SELECT isin, code, name, big_sort, sort, nav_total FROM etf_master"
    )
    mapping: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    for theme, spec in themes.items():
        inc = _compiled(spec["include"])
        exc = _compiled(spec["exclude"])
        codes = set(spec["codes"])
        min_nav = float(spec["min_nav_eok"]) * 1e8
        scope = spec["scope"]

        for etf in etfs:
            name = etf["name"] or ""
            is_global = (etf["big_sort"] or "").startswith("해외")
            if scope == "domestic" and is_global:
                continue
            if scope == "global" and not is_global:
                continue

            if etf["code"] in codes:
                mapping.append({"theme": theme, "isin": etf["isin"], "matched": "codes"})
                counts[theme] = counts.get(theme, 0) + 1
                continue

            # 순자산이 '모르는 값'(NULL)이면 걸러내지 않는다. SEIBro 가 이른 아침에
            # 순자산총액을 아직 채우지 않은 채 응답하는 일이 있는데, 이를 0 으로
            # 취급하면 테마 전체가 통째로 비어 버린다.
            nav = etf["nav_total"]
            if nav is not None and nav < min_nav:
                continue
            if any(p.search(name) for p in exc):
                continue
            hit = next((p.pattern for p in inc if p.search(name)), None)
            if hit is None:
                continue
            mapping.append({"theme": theme, "isin": etf["isin"], "matched": hit})
            counts[theme] = counts.get(theme, 0) + 1

    # 같은 (theme, isin) 이 codes 와 규칙 양쪽에 걸릴 수 있으므로 정리
    seen: set[tuple[str, str]] = set()
    deduped = []
    for row in mapping:
        key = (row["theme"], row["isin"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    counts = {}
    for row in deduped:
        counts[row["theme"]] = counts.get(row["theme"], 0) + 1

    # 분류 결과가 갑자기 무너지면 덮어쓰지 않는다. 한 번 무너진 매핑으로 수집하면
    # 그날 스냅샷이 통째로 비고, 그 뒤 비교에서 신규편입/편출이 사라져 버린다.
    previous = store.scalar("SELECT COUNT(*) FROM etf_theme") or 0
    if (not allow_collapse and previous >= MIN_SANE_MAPPING
            and len(deduped) < previous * COLLAPSE_RATIO):
        log.error(
            "테마 분류 결과가 %d건 -> %d건으로 급감했습니다. 기존 매핑을 유지합니다.",
            previous, len(deduped),
        )
        raise ClassificationCollapsed(previous, len(deduped))

    store.replace_themes(deduped)
    return counts

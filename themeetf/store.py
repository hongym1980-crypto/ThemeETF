"""SQLite 스냅샷 저장소.

매일의 PDF(구성종목) 스냅샷을 누적해서 일자 간 비교가 가능하도록 한다.
핵심 테이블은 ``holding`` 으로, (ETF, 기준일, 종목) 단위의 비중/1CU수량/보유금액을
담는다. 1CU 기준 수량은 설정·환매의 영향을 받지 않으므로 순수한 리밸런싱 신호다.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

SCHEMA = """
PRAGMA journal_mode = WAL;

-- ETF 기본정보 (종목발행현황 스냅샷의 최신값)
CREATE TABLE IF NOT EXISTS etf_master (
    isin        TEXT PRIMARY KEY,
    code        TEXT NOT NULL,
    name        TEXT NOT NULL,
    mngco       TEXT,
    big_sort    TEXT,
    sort        TEXT,
    nav_total   REAL,        -- 순자산총액(원)
    close       REAL,
    volume      REAL,
    total_fee   REAL,        -- 총보수(%)
    std_dt      TEXT,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_etf_master_code ON etf_master(code);

-- 테마 <-> ETF 매핑 (분류 규칙 실행 결과)
CREATE TABLE IF NOT EXISTS etf_theme (
    theme   TEXT NOT NULL,
    isin    TEXT NOT NULL,
    matched TEXT,            -- 어떤 규칙에 걸렸는지
    PRIMARY KEY (theme, isin)
);

-- 수집 이력
CREATE TABLE IF NOT EXISTS snapshot (
    isin         TEXT NOT NULL,
    std_dt       TEXT NOT NULL,
    holding_cnt  INTEGER NOT NULL DEFAULT 0,
    sector_cnt   INTEGER NOT NULL DEFAULT 0,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (isin, std_dt)
);

-- PDF 구성종목: (ETF, 기준일, 종목)
CREATE TABLE IF NOT EXISTS holding (
    isin        TEXT NOT NULL,
    std_dt      TEXT NOT NULL,
    stock_code  TEXT NOT NULL,
    stock_name  TEXT,
    weight      REAL,      -- 보유비중 %
    qty_per_cu  REAL,      -- 1CU 구성수량 (설정/환매 무관)
    close       REAL,      -- 종목 전일 종가
    chg_rate    REAL,      -- 종목 등락률 %
    rank        INTEGER,   -- TOP10 순위 (역인덱스 유래는 NULL)
    hold_value  REAL,      -- 비중 x ETF 순자산 = 실보유금액(원)
    source      TEXT NOT NULL,  -- top10 | xref
    PRIMARY KEY (isin, std_dt, stock_code)
);
CREATE INDEX IF NOT EXISTS ix_holding_stock ON holding(stock_code, std_dt);
CREATE INDEX IF NOT EXISTS ix_holding_dt ON holding(std_dt);

-- 섹터(FGSC) 비중
CREATE TABLE IF NOT EXISTS sector (
    isin          TEXT NOT NULL,
    std_dt        TEXT NOT NULL,
    sector_cd     TEXT NOT NULL,
    sector_nm     TEXT,
    etf_weight    REAL,
    market_weight REAL,
    diff          REAL,
    PRIMARY KEY (isin, std_dt, sector_cd)
);

-- 종목별 ETF 시장 전체 집계 (역인덱스 전체 페이지 기준)
CREATE TABLE IF NOT EXISTS stock_market (
    stock_code   TEXT NOT NULL,
    std_dt       TEXT NOT NULL,
    stock_name   TEXT,
    etf_cnt      INTEGER,   -- 보유 ETF 수
    mngco_cnt    INTEGER,   -- 보유 운용사 수
    hold_value   REAL,      -- 전체 ETF 보유금액 합(원)
    PRIMARY KEY (stock_code, std_dt)
);

-- 감지된 리밸런싱 이벤트
CREATE TABLE IF NOT EXISTS event (
    std_dt      TEXT NOT NULL,
    prev_dt     TEXT NOT NULL,
    theme       TEXT NOT NULL,
    isin        TEXT NOT NULL,
    stock_code  TEXT NOT NULL,
    kind        TEXT NOT NULL,   -- NEW | DROP | ADD | TRIM
    weight      REAL,
    prev_weight REAL,
    d_weight    REAL,            -- pp
    qty_per_cu  REAL,
    prev_qty    REAL,
    d_qty_pct   REAL,            -- %
    d_value     REAL,            -- 보유금액 변화(원)
    detected_at TEXT NOT NULL,
    PRIMARY KEY (std_dt, theme, isin, stock_code, kind)
);
CREATE INDEX IF NOT EXISTS ix_event_dt ON event(std_dt);
CREATE INDEX IF NOT EXISTS ix_event_stock ON event(stock_code, std_dt);
"""


@dataclass
class Store:
    """SQLite 래퍼. 모든 쓰기는 upsert 라 재실행이 안전하다."""

    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ 쓰기

    @staticmethod
    def _upsert(conn: sqlite3.Connection, table: str, keys: Sequence[str],
                rows: Iterable[dict[str, Any]],
                keep_on_null: Sequence[str] = ()) -> int:
        """업서트. ``keep_on_null`` 컬럼은 새 값이 NULL 이면 기존 값을 지킨다.

        SEIBro 가 이른 아침에 일부 필드를 아직 채우지 않은 채로 응답하는 경우가
        있어, 그 NULL 로 멀쩡한 값을 덮어쓰지 않도록 한다.
        """
        rows = list(rows)
        if not rows:
            return 0
        cols = list(rows[0].keys())
        updates = [c for c in cols if c not in keys]
        sql = "INSERT INTO {t} ({c}) VALUES ({p})".format(
            t=table,
            c=", ".join(cols),
            p=", ".join(":" + c for c in cols),
        )
        if updates:
            def assign(col: str) -> str:
                if col in keep_on_null:
                    return "{c}=COALESCE(excluded.{c}, {t}.{c})".format(c=col, t=table)
                return "{0}=excluded.{0}".format(col)

            sql += " ON CONFLICT({k}) DO UPDATE SET {u}".format(
                k=", ".join(keys),
                u=", ".join(assign(c) for c in updates),
            )
        else:
            sql += " ON CONFLICT({k}) DO NOTHING".format(k=", ".join(keys))
        conn.executemany(sql, rows)
        return len(rows)

    #: SEIBro 응답에서 비어 올 수 있는 값들. NULL 로 기존 값을 덮지 않는다.
    _ETF_KEEP_ON_NULL = ("nav_total", "close", "volume", "total_fee")

    def upsert_etf_master(self, rows: Iterable[dict[str, Any]]) -> int:
        with self.connect() as conn:
            return self._upsert(conn, "etf_master", ["isin"], rows,
                                keep_on_null=self._ETF_KEEP_ON_NULL)

    def replace_themes(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        with self.connect() as conn:
            conn.execute("DELETE FROM etf_theme")
            return self._upsert(conn, "etf_theme", ["theme", "isin"], rows)

    def upsert_holdings(self, rows: Iterable[dict[str, Any]]) -> int:
        with self.connect() as conn:
            return self._upsert(conn, "holding", ["isin", "std_dt", "stock_code"], rows)

    def upsert_sectors(self, rows: Iterable[dict[str, Any]]) -> int:
        with self.connect() as conn:
            return self._upsert(conn, "sector", ["isin", "std_dt", "sector_cd"], rows)

    def upsert_stock_market(self, rows: Iterable[dict[str, Any]]) -> int:
        with self.connect() as conn:
            return self._upsert(conn, "stock_market", ["stock_code", "std_dt"], rows)

    def upsert_snapshots(self, rows: Iterable[dict[str, Any]]) -> int:
        with self.connect() as conn:
            return self._upsert(conn, "snapshot", ["isin", "std_dt"], rows)

    def upsert_events(self, rows: Iterable[dict[str, Any]]) -> int:
        with self.connect() as conn:
            return self._upsert(
                conn, "event", ["std_dt", "theme", "isin", "stock_code", "kind"], rows
            )

    # ------------------------------------------------------------------ 읽기

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        rows = self.query(sql, params)
        if not rows:
            return None
        return next(iter(rows[0].values()))

    def theme_names(self) -> list[str]:
        return [r["theme"] for r in self.query(
            "SELECT DISTINCT theme FROM etf_theme ORDER BY theme")]

    def theme_etfs(self, theme: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT t.theme, m.isin, m.code, m.name, m.mngco, m.big_sort, m.sort,
                   m.nav_total, m.total_fee
            FROM etf_theme t JOIN etf_master m USING (isin)
            {where}
            ORDER BY t.theme, m.nav_total DESC
        """.format(where="WHERE t.theme = ?" if theme else "")
        return self.query(sql, (theme,) if theme else ())

    def available_dates(self, isin: str | None = None) -> list[str]:
        sql = "SELECT DISTINCT std_dt FROM holding {w} ORDER BY std_dt DESC".format(
            w="WHERE isin = ?" if isin else ""
        )
        return [r["std_dt"] for r in self.query(sql, (isin,) if isin else ())]

    def latest_two_dates(self) -> tuple[str | None, str | None]:
        dates = self.available_dates()
        return (dates[0] if dates else None, dates[1] if len(dates) > 1 else None)

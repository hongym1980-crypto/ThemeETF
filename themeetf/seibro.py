"""한국예탁결제원(KSD) SEIBro ETF 정보 클라이언트.

SEIBro 의 ProWorks 트랜잭션 엔드포인트(callServletService.jsp)를 직접 호출한다.
요청 본문은 ``<reqParam action=".." task="..">`` XML, 응답은
``<vector><data><result>`` XML 이다.

검증된 action/task 조합 (2026-08 기준)
-------------------------------------
EtfSetredInfoPTask
  secnIssuStatPList / ...Cnt      ETF 전종목 기본정보 (종목발행현황)
EtfCompstInfoPTask
  compstInfoSecnDetailList        ETF 종목상세 (운용사/벤치마크/보수)
  compstInfoHoldAssetList         보유자산 섹터별 비중 (전체)
  compstInfoHoldAssetDetailList   보유종목 TOP10 (비중 + 1CU 구성수량)
EtfMartInfoPTask
  secnHoldStatStkPList / ...Cnt   종목 -> 그 종목을 보유한 전체 ETF (역인덱스)
"""

from __future__ import annotations

import logging
import random
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator
from xml.sax.saxutils import quoteattr

import requests

log = logging.getLogger(__name__)

HOST = "seibro.or.kr"
ENDPOINT = "https://{}/websquare/engine/proworks/callServletService.jsp".format(HOST)
_CTRL = "https://seibro.or.kr/websquare/control.jsp?w2xPath=/IPORTAL/user/etf/{page}.xml&menuNo={menu}"

TASK_SETRED = "ksd.safe.bip.cnts.etf.process.EtfSetredInfoPTask"
TASK_COMPST = "ksd.safe.bip.cnts.etf.process.EtfCompstInfoPTask"
TASK_MART = "ksd.safe.bip.cnts.etf.process.EtfMartInfoPTask"

# action -> (WebSquare 화면 ID, menuNo). Referer 를 실제 화면과 맞춰 보낸다.
_REFERER = {
    "secnIssuStatPList": ("BIP_CNTS06025V", 174),
    "secnIssuStatPListCnt": ("BIP_CNTS06025V", 174),
    "compstInfoSecnDetailList": ("BIP_CNTS906032V", 514),
    "compstInfoHoldAssetList": ("BIP_CNTS906034V", 516),
    "compstInfoHoldAssetDetailList": ("BIP_CNTS906034V", 516),
    "secnHoldStatStkPList": ("BIP_CNTS06036V", 186),
    "secnHoldStatStkPListCnt": ("BIP_CNTS06036V", 186),
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class SeibroError(RuntimeError):
    """SEIBro 가 WARNING 응답을 돌려주거나 재시도가 모두 실패한 경우."""


class SeibroNetworkError(SeibroError):
    """SEIBro 에 아예 닿지 못한 경우.

    원인이 코드가 아니라 VPN/DNS/방화벽인 경우가 대부분이라, 원문 예외 대신
    사람이 읽을 수 있는 설명을 담는다.
    """


def _explain_network_error(exc: BaseException) -> str:
    """requests 예외를 사람이 읽을 수 있는 원인 설명으로 바꾼다."""
    text = str(exc)
    if "getaddrinfo failed" in text or "Name or service not known" in text \
            or "NameResolutionError" in text or "Failed to resolve" in text:
        return ("도메인 이름을 찾지 못했습니다 (DNS 해석 실패). "
                "VPN(예: Cloudflare WARP)이 켜져 있으면 흔히 생깁니다. "
                "`연결 진단` 을 실행하면 원인과 조치를 알려 줍니다.")
    if "timed out" in text or isinstance(exc, requests.Timeout):
        return ("응답이 제한 시간 안에 오지 않았습니다. 회선이 느리거나 SEIBro 가 "
                "혼잡한 상태입니다. --delay 를 올려 다시 시도해 보세요.")
    if "Connection refused" in text or "ConnectionResetError" in text \
            or "Remote end closed" in text:
        return ("연결이 거부되거나 끊겼습니다. 연속 호출로 일시 차단되었을 수 있으니 "
                "--delay 를 0.8 이상으로 올려 보세요.")
    if "CERTIFICATE_VERIFY_FAILED" in text or "SSLError" in text:
        return ("TLS 인증서 검증에 실패했습니다. 보안 프로그램이나 회사 프록시가 "
                "통신을 가로채고 있을 수 있습니다.")
    if "ProxyError" in text:
        return "프록시를 통과하지 못했습니다. 프록시 설정을 확인하세요."
    return "네트워크 오류: {}".format(text)


@dataclass
class SeibroClient:
    """SEIBro ETF 트랜잭션 클라이언트.

    Parameters
    ----------
    delay:
        연속 호출 사이 최소 대기 시간(초). 공개 화면의 백엔드를 직접 호출하므로
        기본 0.35초로 예의 있는 속도를 유지한다.
    retries:
        네트워크/파싱 오류 재시도 횟수.
    """

    delay: float = 0.35
    retries: int = 3
    timeout: float = 45.0
    _last_call: float = field(default=0.0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _local: threading.local = field(default_factory=threading.local, init=False, repr=False)
    calls: int = field(default=0, init=False)

    _HEADERS = {
        "User-Agent": _UA,
        "Content-Type": "application/xml; charset=UTF-8",
        "Accept": "application/xml, text/xml, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Origin": "https://seibro.or.kr",
    }

    # ------------------------------------------------------------------ 저수준

    @property
    def session(self) -> requests.Session:
        """스레드별 세션. ``requests.Session`` 은 스레드 간 공유가 안전하지 않다."""
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = requests.Session()
            sess.headers.update(self._HEADERS)
            self._local.session = sess
        return sess

    def _throttle(self) -> None:
        """전체 호출을 초당 ``1/delay`` 건으로 제한한다.

        락은 슬롯 예약까지만 잡으므로 HTTP 요청 자체는 스레드 간에 겹칠 수 있다.
        """
        with self._lock:
            now = time.monotonic()
            wait = self.delay - (now - self._last_call)
            self._last_call = now + max(0.0, wait)
        if wait > 0:
            time.sleep(wait)

    @staticmethod
    def _build_body(action: str, task: str, params: dict[str, Any]) -> bytes:
        parts = [
            "<{k} value={v}/>".format(
                k=k, v=quoteattr("" if v is None else str(v))
            )
            for k, v in params.items()
        ]
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<reqParam action={a} task={t}>".format(
                a=quoteattr(action), t=quoteattr(task)
            )
            + "".join(parts)
            + "</reqParam>"
        )
        return xml.encode("utf-8")

    def call(self, action: str, task: str, **params: Any) -> list[dict[str, str]]:
        """트랜잭션 1건을 호출하고 ``<result>`` 노드를 dict 리스트로 반환."""
        page, menu = _REFERER.get(action, ("BIP_CNTS06025V", 174))
        headers = {"Referer": _CTRL.format(page=page, menu=menu)}
        body = self._build_body(action, task, params)

        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                resp = self.session.post(
                    ENDPOINT, data=body, headers=headers, timeout=self.timeout
                )
                resp.raise_for_status()
                with self._lock:
                    self.calls += 1
                return self._parse(resp.content, action)
            except (requests.RequestException, ET.ParseError) as exc:
                last_exc = exc
                backoff = min(8.0, 0.8 * 2 ** (attempt - 1)) + random.random() * 0.3
                log.warning(
                    "%s 실패 (%d/%d): %s -- %.1fs 후 재시도",
                    action, attempt, self.retries, exc, backoff,
                )
                if attempt < self.retries:
                    time.sleep(backoff)

        # 재시도를 모두 소진했다. 원문 예외 대신 원인 설명을 전면에 내보낸다.
        raise SeibroNetworkError(
            "SEIBro 에 연결하지 못했습니다 ({} 호출, {}회 시도).\n  → {}".format(
                action, self.retries, _explain_network_error(last_exc)
                if last_exc else "원인 불명")
        ) from last_exc

    @staticmethod
    def _parse(payload: bytes, action: str) -> list[dict[str, str]]:
        root = ET.fromstring(payload)
        if root.tag == "WARNING":
            msg = ""
            for child in root:
                if child.tag == "msg":
                    msg = child.get("value") or ""
            raise SeibroError("{}: SEIBro 오류 -- {!r}".format(action, msg))

        rows: list[dict[str, str]] = []
        for res in root.iter("result"):
            row = {
                c.tag: (c.get("value") or "")
                for c in res
                if c.get("value") is not None
            }
            if row:
                rows.append(row)
        return rows

    def call_one(self, action: str, task: str, **params: Any) -> dict[str, str]:
        rows = self.call(action, task, **params)
        return rows[0] if rows else {}

    def paged(
        self,
        action: str,
        task: str,
        *,
        page_size: int = 300,
        max_rows: int | None = None,
        **params: Any,
    ) -> Iterator[dict[str, str]]:
        """START_PAGE/END_PAGE 기반 페이징을 자동으로 돌린다."""
        start = 1
        seen = 0
        while True:
            end = start + page_size - 1
            if max_rows is not None:
                end = min(end, max_rows)
            if end < start:
                return
            requested = end - start + 1
            rows = self.call(action, task, START_PAGE=start, END_PAGE=end, **params)
            if not rows:
                return
            for row in rows:
                yield row
            seen += len(rows)
            if len(rows) < requested:
                return
            if max_rows is not None and seen >= max_rows:
                return
            start = end + 1

    # --------------------------------------------------------------- 고수준 API

    # 종목발행현황 화면의 검색 조건 기본값. select_sorting 을 비우면 서버가
    # "서버오류2" 를 반환하므로 반드시 값이 있어야 한다.
    _LIST_FILTERS = {
        "etf_sort_level_cd": "",
        "etf_big_sort_cd": "",
        "mngco": "",
        "SETUP_DT": "",
        "from_TOT_RECM_RATE": "",
        "to_TOT_RECM_RATE": "",
        "from_NETASST_TOTAMT": "",
        "to_NETASST_TOTAMT": "",
        "from_tr_qty": "",
        "to_tr_qty": "",
        "kor_SECN_NM": "",
        "ic4_select": "1",
        "select_sorting": "1",
    }

    def etf_count(self, **overrides: Any) -> tuple[int, str]:
        """전체 ETF 종목 수와 데이터 기준일(YYYYMMDD)을 반환."""
        params = {**self._LIST_FILTERS, **overrides}
        row = self.call_one("secnIssuStatPListCnt", TASK_SETRED, **params)
        cnt = to_int(row.get("LIST_CNT"), 0) or 0
        return cnt, (row.get("STD_DT", "") or "").replace("/", "")

    def etf_list(self, *, page_size: int = 300, **overrides: Any) -> list[dict[str, str]]:
        """ETF 전종목 기본정보 (종목발행현황)."""
        params = {**self._LIST_FILTERS, **overrides}
        return list(
            self.paged("secnIssuStatPList", TASK_SETRED, page_size=page_size, **params)
        )

    def etf_detail(self, isin: str) -> dict[str, str]:
        """ETF 종목상세 (운용사/벤치마크/보수/설정단위 등)."""
        return self.call_one("compstInfoSecnDetailList", TASK_COMPST, isin=isin)

    def sector_weights(self, isin: str, std_dt: str = "") -> list[dict[str, str]]:
        """보유자산 섹터별 비중.

        SEIBro 는 FGSC 10개 섹터 전체를 항상 내려주고, 노출이 없는 섹터는 값을
        빈 문자열로 남긴다. 데이터 기준일(STD_DT)도 값이 있는 행에만 담기므로
        :meth:`resolve_std_dt` 로 뽑아 쓴다.
        """
        return self.call("compstInfoHoldAssetList", TASK_COMPST, ISIN=isin, STD_DT=std_dt)

    def resolve_std_dt(self, isin: str, fallback: str = "") -> tuple[str, list[dict[str, str]]]:
        """ETF 의 실제 PDF 기준일과 섹터 비중을 함께 반환."""
        rows = self.sector_weights(isin)
        for row in rows:
            if row.get("STD_DT"):
                return row["STD_DT"], rows
        return fallback, rows

    def top_holdings(self, isin: str, std_dt: str) -> list[dict[str, str]]:
        """보유종목 TOP10 -- 비중(HOLD_IMPO)과 1CU 구성수량(VCTF_QTY)."""
        return self.call(
            "compstInfoHoldAssetDetailList", TASK_COMPST, ISIN=isin, STD_DT=std_dt
        )

    def holders_of_stock(
        self,
        stock_code: str,
        std_dt: str,
        *,
        page_size: int = 300,
        max_rows: int | None = None,
    ) -> list[dict[str, str]]:
        """역인덱스: 해당 종목을 보유한 모든 ETF와 비중/1CU수량/순자산총액."""
        return list(
            self.paged(
                "secnHoldStatStkPList",
                TASK_MART,
                page_size=page_size,
                max_rows=max_rows,
                VCTF_ISIN=stock_code,
                STD_DT=std_dt,
                radio2="1",
            )
        )

    def holders_count(self, stock_code: str, std_dt: str) -> int:
        row = self.call_one(
            "secnHoldStatStkPListCnt", TASK_MART,
            VCTF_ISIN=stock_code, STD_DT=std_dt, radio2="1",
        )
        return to_int(row.get("LIST_CNT"), 0) or 0


# --------------------------------------------------------------------- 유틸


def to_float(value: Any, default: float | None = None) -> float | None:
    """SEIBro 는 ``-.92``, ``.05``, ``` `` 같은 값을 그대로 내려준다."""
    if value is None:
        return default
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "N/A"}:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def to_int(value: Any, default: int | None = None) -> int | None:
    f = to_float(value)
    return default if f is None else int(f)


def short_code(isin_or_code: str) -> str:
    """``KR7005930003`` -> ``005930``. 이미 단축코드면 그대로 반환."""
    code = (isin_or_code or "").strip()
    if len(code) == 12 and code[:2].isalpha():
        return code[3:9]
    return code


def iter_chunks(items: Iterable[Any], size: int) -> Iterator[list[Any]]:
    chunk: list[Any] = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk

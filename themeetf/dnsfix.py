"""이름 해석 대체 경로.

일부 VPN(특히 Cloudflare WARP)은 DNS 를 자기 리졸버로 가로채는데, 그 리졸버가
``seibro.or.kr`` 을 해석하지 못하는 경우가 있다. 이때 증상은 ``getaddrinfo failed``
한 줄이고, TCP 443 은 VPN 을 통해 멀쩡히 붙는다 — **이름만 못 푸는 상태**다.

여기서 하는 일은 딱 하나, **이름 해석을 공용 DNS 로 대신하는 것**이다.

- 터널을 우회하지 않는다. 실제 트래픽은 그대로 VPN 을 통해 나간다.
- TLS SNI·Host 헤더·인증서 검증은 원래 도메인 이름 그대로 유지된다
  (``socket.getaddrinfo`` 만 그 호스트에 한해 치환하므로).
- ``curl --resolve`` 와 같은 동작이며, 대상 호스트 하나에만 적용된다.

기본값은 "평소 DNS 를 쓰고, 실패할 때만 이 경로를 쓴다" 이고, 적용되면 매번
콘솔에 알린다. ``--no-dns-fallback`` 또는 ``THEMEETF_NO_DNS_FALLBACK=1`` 로 끌 수 있다.
"""

from __future__ import annotations

import logging
import os
import socket

log = logging.getLogger(__name__)

#: 공용 DoH(JSON) 엔드포인트. HTTPS 라 VPN 을 그대로 통과한다.
DOH_URLS = [
    "https://dns.google/resolve",
    "https://8.8.8.8/resolve",
]

_overrides: dict[str, str] = {}
_orig_getaddrinfo = None


def disabled() -> bool:
    return os.environ.get("THEMEETF_NO_DNS_FALLBACK", "").strip() not in ("", "0")


def resolves_normally(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
        return True
    except socket.gaierror:
        return False


def resolve_public(host: str, timeout: float = 12.0) -> str | None:
    """공용 DoH 로 A 레코드를 조회한다. 실패하면 None."""
    import requests

    for url in DOH_URLS:
        try:
            resp = requests.get(
                url, params={"name": host, "type": "A"},
                headers={"Accept": "application/dns-json"}, timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:                      # noqa: BLE001 — 다음 후보로
            log.debug("DoH %s 실패: %s", url, exc)
            continue
        if data.get("Status") != 0:
            continue
        for answer in data.get("Answer", []):
            if answer.get("type") == 1 and answer.get("data"):
                return answer["data"]
    return None


def _install_patch() -> None:
    """등록된 호스트에 한해 ``socket.getaddrinfo`` 를 치환한다 (1회)."""
    global _orig_getaddrinfo
    if _orig_getaddrinfo is not None:
        return
    _orig_getaddrinfo = socket.getaddrinfo

    def patched(host, port, *args, **kwargs):
        target = _overrides.get(host, host)
        return _orig_getaddrinfo(target, port, *args, **kwargs)

    socket.getaddrinfo = patched


def ensure(host: str, *, announce=print) -> str | None:
    """``host`` 가 해석되도록 보장한다.

    평소 DNS 로 해석되면 아무것도 하지 않고 None 을 반환한다. 실패하면 공용 DNS 로
    주소를 받아 그 호스트에만 적용하고, 적용한 주소를 반환한다.
    """
    if host in _overrides:
        return _overrides[host]
    if resolves_normally(host):
        return None
    if disabled():
        announce(
            "  ! {} 이름 해석에 실패했고, 대체 경로가 꺼져 있습니다"
            " (THEMEETF_NO_DNS_FALLBACK).".format(host)
        )
        return None

    ip = resolve_public(host)
    if not ip:
        announce("  ! {} 를 공용 DNS 로도 해석하지 못했습니다.".format(host))
        return None

    _overrides[host] = ip
    _install_patch()
    announce(
        "  * {host} 이름 해석을 공용 DNS 로 대체했습니다 (-> {ip}).\n"
        "    이 PC 의 DNS(VPN 리졸버)가 이 도메인을 풀지 못해서입니다. 통신 자체는\n"
        "    평소대로 VPN 을 통해 나가며, 인증서 검증도 원래 도메인으로 그대로 합니다.\n"
        "    근본 해결은 `연결 진단`([D]) 의 안내를 참고하세요."
        .format(host=host, ip=ip)
    )
    return ip

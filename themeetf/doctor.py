"""연결 진단.

수집이 실패했을 때 "왜" 를 사람이 읽을 수 있는 말로 알려준다. 실제로 가장 흔한
원인은 코드나 SEIBro 가 아니라 **VPN/DNS** 다. 예를 들어 Cloudflare WARP 가 켜져
있으면 DNS 를 가로채는데, 그 리졸버가 ``seibro.or.kr`` 을 해석하지 못하는 경우가
있다. 이때 증상은 ``getaddrinfo failed`` 한 줄뿐이라 원인을 짐작하기 어렵다.

진단 순서
---------
1. SEIBro 도메인 이름 해석 (DNS)
2. 실패 시 일반 도메인(google.com)도 해석되는지 → 인터넷 자체 문제인지 구분
3. 공용 DNS(8.8.8.8)로는 해석되는지 → 도메인 문제인지 내 DNS 문제인지 구분
4. VPN/프록시 흔적 탐지 (Cloudflare WARP, 프록시 환경변수)
5. TCP 443 연결
6. 실제 API 1회 호출
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HOST = "seibro.or.kr"
CONTROL_HOST = "google.com"
PUBLIC_RESOLVERS = ["8.8.8.8", "1.1.1.1"]

_WARP_PATHS = [
    Path(r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe"),
    Path(r"C:\Program Files (x86)\Cloudflare\Cloudflare WARP\warp-cli.exe"),
]

OK = "  [정상]"
NG = "  [실패]"
INFO = "  [정보]"


@dataclass
class Result:
    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    remedies: list[str] = field(default_factory=list)
    ok: bool = True

    def say(self, text: str) -> None:
        self.lines.append(text)
        print(text)

    def fail(self, problem: str, *remedies: str) -> None:
        self.ok = False
        self.problems.append(problem)
        self.remedies.extend(remedies)


def _resolve(host: str) -> tuple[bool, str]:
    try:
        infos = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
        return True, infos[0][4][0]
    except socket.gaierror as exc:
        return False, str(exc)


def _resolve_with(server: str, host: str) -> str | None:
    """공용 DNS 서버로 직접 조회. Windows 의 nslookup 을 쓴다."""
    try:
        out = subprocess.run(
            ["nslookup", "-type=A", host, server],
            capture_output=True, text=True, timeout=12,
            encoding="utf-8", errors="replace",
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    # "Address:  124.137.74.135" 중 서버 자신의 주소는 건너뛴다.
    found = []
    for line in out.splitlines():
        line = line.strip()
        if line.lower().startswith(("address:", "addresses:")):
            value = line.split(":", 1)[1].strip()
            if value and value != server:
                found.append(value)
    return found[-1] if found else None


def _dns_servers() -> list[str]:
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-DnsClientServerAddress -AddressFamily IPv4 | "
             "Where-Object {$_.ServerAddresses} | "
             "ForEach-Object {$_.ServerAddresses}) -join ','"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return []
    return [s for s in (x.strip() for x in out.split(",")) if s]


def _warp_cli() -> Path | None:
    return next((p for p in _WARP_PATHS if p.exists()), None)


def _warp_always_on() -> bool:
    """WARP 의 'Always On' 여부. 켜져 있으면 꺼도 곧 다시 연결된다."""
    cli = _warp_cli()
    if cli is None:
        return False
    try:
        out = subprocess.run(
            [str(cli), "--accept-tos", "settings"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    for line in out.splitlines():
        if "always on" in line.lower():
            return "true" in line.lower()
    return False


def _vpn_hints() -> list[str]:
    hints = []
    servers = _dns_servers()
    if servers:
        hints.append("현재 DNS 서버: " + ", ".join(servers))
    # WARP 의 내장 리졸버는 127.0.2.2 / 127.0.2.3 을 쓴다.
    if any(s.startswith("127.0.2.") for s in servers):
        hints.append("DNS 가 Cloudflare WARP 의 내장 리졸버(127.0.2.x)로 잡혀 있습니다.")
    if _warp_cli():
        note = "Cloudflare WARP 가 설치되어 있습니다."
        if _warp_always_on():
            note += " (Always On 켜짐 — 꺼도 곧 다시 연결됩니다)"
        hints.append(note)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if os.environ.get(var):
            hints.append("프록시 환경변수 {}={}".format(var, os.environ[var]))
    return hints


def _tcp(ip: str, port: int = 443, timeout: float = 8.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True, "연결됨"
    except OSError as exc:
        return False, str(exc)


def run() -> Result:
    """연결 진단을 수행하고 결과를 출력한다."""
    r = Result()
    r.say("\n" + "=" * 74)
    r.say("SEIBro 연결 진단")
    r.say("=" * 74)

    # 1) 이름 해석 -----------------------------------------------------------
    ok, value = _resolve(HOST)
    if ok:
        r.say("{} DNS: {} -> {}".format(OK, HOST, value))
        ip = value
    else:
        r.say("{} DNS: {} 이름 해석 실패 ({})".format(NG, HOST, value))
        ip = None

        ctrl_ok, ctrl = _resolve(CONTROL_HOST)
        if ctrl_ok:
            r.say("{} 일반 인터넷은 정상입니다 ({} -> {})".format(INFO, CONTROL_HOST, ctrl))
        else:
            r.say("{} {} 도 해석되지 않습니다 — 인터넷 연결 자체를 확인하세요."
                  .format(NG, CONTROL_HOST))
            r.fail("인터넷에 연결되어 있지 않습니다.",
                   "Wi-Fi / 유선 연결 상태를 확인하세요.")
            _summary(r)
            return r

        # 3) 공용 DNS 로는 되는지 -------------------------------------------
        public = {}
        for server in PUBLIC_RESOLVERS:
            got = _resolve_with(server, HOST)
            public[server] = got
            r.say("{} 공용 DNS {} 로 조회: {}".format(
                INFO, server, got or "실패"))
        if any(public.values()):
            good = next(s for s, v in public.items() if v)
            ip = public[good]
            remedies = [
                "VPN(특히 Cloudflare WARP)을 끄고 다시 실행하세요. "
                "WARP 앱 스위치를 Off 로 내리면 됩니다.",
            ]
            if _warp_always_on():
                remedies.append(
                    "WARP 의 'Always On' 이 켜져 있어 꺼도 곧 다시 연결됩니다. "
                    "WARP 앱 > Settings > Advanced > Connection options 에서 "
                    "'Always On' 을 먼저 해제한 뒤 연결을 끊으세요."
                )
            remedies += [
                "이 프로그램은 이름 해석만 공용 DNS 로 대신하는 대체 경로를 갖고 "
                "있습니다 (터널은 그대로 사용). 보통은 자동으로 적용됩니다.",
                "회사 정책상 VPN 을 유지해야 한다면 IT 담당자에게 "
                "'{} 이 VPN DNS 에서 해석되지 않는다. 공용 DNS({}) 로는 {} 로 "
                "정상 해석된다' 고 전달하세요.".format(HOST, good, ip),
            ]
            r.fail(
                "도메인은 정상인데 **이 PC 의 DNS 가** {} 를 해석하지 못합니다.".format(HOST),
                *remedies,
            )
        else:
            r.fail(
                "{} 도메인이 어떤 DNS 로도 해석되지 않습니다.".format(HOST),
                "잠시 뒤 다시 시도하세요. SEIBro 측 일시적 장애일 수 있습니다.",
                "사내망이라면 방화벽이 이 도메인을 차단하는지 확인하세요.",
            )

    # 4) VPN/프록시 흔적 -----------------------------------------------------
    hints = _vpn_hints()
    if hints:
        r.say("")
        for h in hints:
            r.say("{} {}".format(INFO, h))

    # 5) TCP 연결 ------------------------------------------------------------
    if ip:
        r.say("")
        tcp_ok, msg = _tcp(ip)
        if tcp_ok:
            r.say("{} TCP 443 연결: {} {}".format(OK, ip, msg))
        else:
            r.say("{} TCP 443 연결 실패: {} ({})".format(NG, ip, msg))
            r.fail("주소는 찾았지만 접속이 막혀 있습니다.",
                   "방화벽 또는 보안 프로그램이 443 포트를 막고 있는지 확인하세요.")

    # 6) 실제 API 호출 -------------------------------------------------------
    if r.ok:
        r.say("")
        try:
            from .seibro import SeibroClient

            cnt, std_dt = SeibroClient(delay=0.1, retries=1, timeout=20).etf_count()
            r.say("{} SEIBro API 호출 성공 — ETF {}종목, 최신 기준일 {}".format(
                OK, cnt, std_dt))
        except Exception as exc:
            r.say("{} SEIBro API 호출 실패: {}".format(NG, exc))
            r.fail("네트워크는 되는데 API 응답이 오지 않습니다.",
                   "잠시 뒤 다시 시도하세요. 연속 호출로 일시 차단되었을 수 있습니다.",
                   "계속 실패하면 --delay 를 0.8 이상으로 올려 보세요.")

    _summary(r)
    return r


def _summary(r: Result) -> None:
    r.say("")
    r.say("-" * 74)
    if r.ok:
        r.say("결론: 연결에 문제가 없습니다. [1] 최신 데이터 받기 를 실행하세요.")
    else:
        r.say("결론: 아래 문제로 최신화가 실패합니다.")
        for i, p in enumerate(r.problems, 1):
            r.say("  {}. {}".format(i, p))
        r.say("")
        r.say("조치:")
        for i, m in enumerate(r.remedies, 1):
            r.say("  {}) {}".format(i, m))
    r.say("-" * 74)

"""Server-side fetch of provider result URLs with SSRF protection.

https only; every resolved address must be public; the connection is pinned to the checked IP
(SNI and certificate checks still use the real host name); redirects are refused; size and time are capped.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Optional
from urllib.parse import urlsplit

import httpx

MAX_FETCH_BYTES = 40 * 1024 * 1024
TIMEOUT_S = 30.0


class FetchRefused(ValueError):
    pass


def _public(ip: ipaddress._BaseAddress) -> bool:
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified)


def resolve_public(host: str, resolver: Callable = socket.getaddrinfo) -> str:
    try:
        infos = resolver(host, 443, type=socket.SOCK_STREAM)
    except OSError as e:
        raise FetchRefused("host did not resolve") from e
    addrs = []
    for fam, _t, _p, _c, sa in infos:
        ip = ipaddress.ip_address(sa[0])
        if not _public(ip):
            raise FetchRefused("host resolves to a non-public address")
        addrs.append(sa[0])
    if not addrs:
        raise FetchRefused("host did not resolve")
    return addrs[0]


def fetch_bytes(url: str, *, max_bytes: int = MAX_FETCH_BYTES, resolver: Callable = socket.getaddrinfo,
                transport: Optional[httpx.BaseTransport] = None, allowed_types: tuple[str, ...] = ("image/",)) -> tuple[bytes, str]:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise FetchRefused("only plain https URLs are allowed")
    if parts.port not in (None, 443):
        raise FetchRefused("only port 443 is allowed")
    host = parts.hostname
    try:
        ipaddress.ip_address(host)
        raise FetchRefused("IP-literal URLs are not allowed")
    except ValueError as e:
        if isinstance(e, FetchRefused):
            raise
    ip = resolve_public(host, resolver)
    target = f"https://{ip if ':' not in ip else '[' + ip + ']'}{parts.path or '/'}"
    if parts.query:
        target += "?" + parts.query
    with httpx.Client(transport=transport, timeout=TIMEOUT_S, follow_redirects=False) as client:
        with client.stream("GET", target, headers={"Host": host}, extensions={"sni_hostname": host}) as r:
            if 300 <= r.status_code < 400:
                raise FetchRefused("redirects are not followed")
            if r.status_code != 200:
                raise FetchRefused(f"download failed with HTTP {r.status_code}")
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            if allowed_types and not any(ctype.startswith(t) for t in allowed_types):
                raise FetchRefused("unexpected content type")
            clen = r.headers.get("content-length")
            if clen and clen.isdigit() and int(clen) > max_bytes:
                raise FetchRefused("download too large")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > max_bytes:
                    raise FetchRefused("download too large")
    return bytes(buf), ctype

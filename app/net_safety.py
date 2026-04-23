from __future__ import annotations

import ipaddress
import socket


class BodyTooLarge(ValueError):
    pass


def host_matches(host: str, allowed: str) -> bool:
    lowered = host.lower()
    allowed = allowed.lower()
    if allowed.startswith("."):
        return lowered == allowed[1:] or lowered.endswith(allowed)
    return lowered == allowed


def host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    return any(host_matches(host, allowed) for allowed in allowed_hosts)


def public_ip(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def public_host(host: str) -> bool:
    try:
        resolved = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False

    return bool(resolved) and all(public_ip(entry[4][0]) for entry in resolved)


def response_peer_public(response) -> bool:
    peer_ip = _response_peer_ip(response)
    return True if peer_ip is None else public_ip(peer_ip)


def _response_peer_ip(response) -> str | None:
    raw = getattr(response, "raw", None)
    candidates = (
        getattr(getattr(raw, "_connection", None), "sock", None),
        getattr(getattr(getattr(raw, "_fp", None), "fp", None), "raw", None),
    )
    for candidate in candidates:
        sock = getattr(candidate, "_sock", candidate)
        getpeername = getattr(sock, "getpeername", None)
        if getpeername is None:
            continue
        try:
            peer = getpeername()
        except OSError:
            continue
        if isinstance(peer, tuple) and peer:
            return str(peer[0])
    return None


def read_response_bytes(response, max_bytes: int) -> bytes:
    content_length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > max_bytes:
            raise BodyTooLarge("response body too large")

    chunks: list[bytes] = []
    total = 0
    iter_content = getattr(response, "iter_content", None)
    if iter_content is not None:
        for chunk in iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            total += len(chunk)
            if total > max_bytes:
                raise BodyTooLarge("response body too large")
            chunks.append(chunk)
        if chunks:
            return b"".join(chunks)

    body = getattr(response, "content", None)
    if body is None:
        text = getattr(response, "text", "")
        encoding = getattr(response, "encoding", None) or "utf-8"
        body = text.encode(encoding, errors="replace") if isinstance(text, str) else bytes(text)
    if len(body) > max_bytes:
        raise BodyTooLarge("response body too large")
    return body


def read_response_text(response, max_bytes: int) -> str:
    body = read_response_bytes(response, max_bytes)
    encoding = getattr(response, "encoding", None) or "utf-8"
    return body.decode(encoding, errors="replace")

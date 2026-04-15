from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


def _host_matches(host: str, allowed: str) -> bool:
    lowered = host.lower()
    if allowed.startswith("."):
        return lowered == allowed[1:] or lowered.endswith(allowed)
    return lowered == allowed


@dataclass(frozen=True)
class BootstrapProvider:
    name: str
    host_patterns: tuple[str, ...]
    path_prefixes: tuple[str, ...] = ()

    def matches_url(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return False
        if not any(_host_matches(host, pattern) for pattern in self.host_patterns):
            return False
        if not self.path_prefixes:
            return True
        return any(parsed.path.startswith(prefix) for prefix in self.path_prefixes)


PLAYER_PAGE_HOSTS: tuple[str, ...] = (
    "dlstreams.top",
    ".dlstreams.top",
)

BOOTSTRAP_PROVIDERS: tuple[BootstrapProvider, ...] = (
    BootstrapProvider(
        name="premiumtv",
        host_patterns=(
            "embedkclx.sbs",
            ".embedkclx.sbs",
            "enviromentalspa2.sbs",
            ".enviromentalspa2.sbs",
        ),
        path_prefixes=("/premiumtv/",),
    ),
    BootstrapProvider(
        name="topembed-channel",
        host_patterns=(
            "viewembed.ru",
            ".viewembed.ru",
        ),
        path_prefixes=("/channel/",),
    ),
)

BOOTSTRAP_MANIFEST_HOSTS: tuple[str, ...] = (
    "soyspace.cyou",
    ".soyspace.cyou",
    "vid.aivideox.site",
    ".aivideox.site",
)


def default_proxy_allowed_hosts() -> tuple[str, ...]:
    hosts: list[str] = []
    for group in (PLAYER_PAGE_HOSTS, *(provider.host_patterns for provider in BOOTSTRAP_PROVIDERS), BOOTSTRAP_MANIFEST_HOSTS):
        for host in group:
            if host not in hosts:
                hosts.append(host)
    return tuple(hosts)


def should_attempt_bootstrap_fetch(url: str) -> bool:
    return any(provider.matches_url(url) for provider in BOOTSTRAP_PROVIDERS)

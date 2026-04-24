from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse


def _host_matches(host: str, allowed: str) -> bool:
    lowered = host.lower()
    if allowed.startswith("."):
        return lowered == allowed[1:] or lowered.endswith(allowed)
    return lowered == allowed


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


EMBED_CHANNEL_KEY_RE = re.compile(r"const\s+CHANNEL_KEY\s*=\s*['\"]([^'\"]+)['\"]")
EMBED_SERVERS_RE = re.compile(r"let\s+M3U8_SERVERS\s*=\s*\[(.*?)\]", re.S)
BOOTSTRAP_MARKERS = ("CHANNEL_KEY", "M3U8_SERVERS", "server_lookup")


@dataclass(frozen=True)
class BootstrapInputs:
    strategy_name: str
    channel_key: str
    servers: tuple[str, ...]


@dataclass(frozen=True)
class BootstrapParseResult:
    strategy_name: str
    inputs: BootstrapInputs | None
    reason: str
    priority: int


@dataclass(frozen=True)
class BootstrapStrategy:
    name: str
    host_patterns: tuple[str, ...]
    path_prefixes: tuple[str, ...] = ()
    player_path_hints: tuple[str, ...] = ()
    player_label_hints: tuple[str, ...] = ()
    required_markers: tuple[str, ...] = BOOTSTRAP_MARKERS
    fallback: bool = False
    match_any_host: bool = False

    def matches_host(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return False
        if self.match_any_host:
            return True
        return any(_host_matches(host, pattern) for pattern in self.host_patterns)

    def matches_url(self, url: str) -> bool:
        if not self.matches_host(url):
            return False
        if not self.path_prefixes:
            return True
        return any(urlparse(url).path.startswith(prefix) for prefix in self.path_prefixes)

    def priority(self, *, source_url: str | None, player_url: str | None, player_label: str | None) -> int:
        score = 0 if self.fallback else -10

        if source_url and not self.fallback and self.matches_url(source_url):
            score += 100

        if player_url:
            player_path = urlparse(player_url).path
            if any(player_path.startswith(prefix) for prefix in self.player_path_hints):
                score += 20

        if player_label:
            lowered_label = player_label.lower()
            if any(hint in lowered_label for hint in self.player_label_hints):
                score += 5

        return score

    def parse(self, html: str, *, priority: int) -> BootstrapParseResult:
        if any(marker not in html for marker in self.required_markers):
            return BootstrapParseResult(self.name, None, "missing_markers", priority)

        channel_match = EMBED_CHANNEL_KEY_RE.search(html)
        servers_match = EMBED_SERVERS_RE.search(html)
        if not channel_match or not servers_match:
            return BootstrapParseResult(self.name, None, "missing_bootstrap_fields", priority)

        channel_key = _clean(channel_match.group(1))
        servers = tuple(
            server.strip()
            for server in re.findall(r"['\"]([^'\"]+)['\"]", servers_match.group(1))
            if server.strip()
        )
        if not channel_key or not servers:
            return BootstrapParseResult(self.name, None, "invalid_bootstrap_fields", priority)

        return BootstrapParseResult(
            self.name,
            BootstrapInputs(strategy_name=self.name, channel_key=channel_key, servers=servers),
            "parsed",
            priority,
        )


PLAYER_PAGE_HOSTS: tuple[str, ...] = (
    "dlstreams.com",
    ".dlstreams.com",
    "dlstreams.top",
    ".dlstreams.top",
    "ddyplayer.cfd",
    ".ddyplayer.cfd",
)

SPECIFIC_BOOTSTRAP_STRATEGIES: tuple[BootstrapStrategy, ...] = (
    BootstrapStrategy(
        name="premiumtv",
        host_patterns=(
            "embedkclx.sbs",
            ".embedkclx.sbs",
            "enviromentalspa2.sbs",
            ".enviromentalspa2.sbs",
        ),
        path_prefixes=("/premiumtv/",),
        player_path_hints=("/stream/", "/cast/"),
    ),
    BootstrapStrategy(
        name="premiumtv-any-host",
        host_patterns=(),
        path_prefixes=("/premiumtv/",),
        player_path_hints=("/stream/", "/cast/"),
        match_any_host=True,
    ),
    BootstrapStrategy(
        name="topembed-channel",
        host_patterns=(
            "viewembed.ru",
            ".viewembed.ru",
        ),
        path_prefixes=("/channel/",),
        player_path_hints=("/watch/",),
    ),
)

KNOWN_BOOTSTRAP_HOST_PATTERNS = tuple(
    dict.fromkeys(host for strategy in SPECIFIC_BOOTSTRAP_STRATEGIES for host in strategy.host_patterns)
)

BOOTSTRAP_STRATEGIES: tuple[BootstrapStrategy, ...] = (
    *SPECIFIC_BOOTSTRAP_STRATEGIES,
    BootstrapStrategy(name="known-host-fallback", host_patterns=KNOWN_BOOTSTRAP_HOST_PATTERNS, fallback=True),
)

BOOTSTRAP_MANIFEST_HOSTS: tuple[str, ...] = (
    "vovlacosa.sbs",
    ".vovlacosa.sbs",
    "soyspace.cyou",
    ".soyspace.cyou",
    "vid.aivideox.site",
    ".aivideox.site",
    "aiphotofree.site",
    ".aiphotofree.site",
    "edge.cdnlivetv.ru",
    ".cdnlivetv.ru",
    "edge.cdn-aws.ru",
    ".cdn-aws.ru",
)


def matching_bootstrap_strategies(
    source_url: str | None,
    *,
    player_url: str | None = None,
    player_label: str | None = None,
) -> tuple[BootstrapStrategy, ...]:
    if not source_url:
        return BOOTSTRAP_STRATEGIES

    candidates = [
        strategy
        for strategy in BOOTSTRAP_STRATEGIES
        if strategy.matches_url(source_url) or (not strategy.match_any_host and strategy.matches_host(source_url))
    ]
    if not candidates:
        return ()

    return tuple(
        sorted(
            candidates,
            key=lambda strategy: (
                -strategy.priority(source_url=source_url, player_url=player_url, player_label=player_label),
                BOOTSTRAP_STRATEGIES.index(strategy),
            ),
        )
    )


def matching_bootstrap_strategy_names(
    source_url: str | None,
    *,
    player_url: str | None = None,
    player_label: str | None = None,
) -> tuple[str, ...]:
    return tuple(
        strategy.name
        for strategy in matching_bootstrap_strategies(source_url, player_url=player_url, player_label=player_label)
    )


def bootstrap_parse_results(
    html: str,
    *,
    source_url: str | None = None,
    player_url: str | None = None,
    player_label: str | None = None,
) -> tuple[BootstrapParseResult, ...]:
    strategies = matching_bootstrap_strategies(source_url, player_url=player_url, player_label=player_label)
    if source_url and not strategies:
        return ()

    return tuple(
        strategy.parse(
            html,
            priority=strategy.priority(source_url=source_url, player_url=player_url, player_label=player_label),
        )
        for strategy in strategies
    )


def default_proxy_allowed_hosts() -> tuple[str, ...]:
    hosts: list[str] = []
    for group in (PLAYER_PAGE_HOSTS, *(strategy.host_patterns for strategy in BOOTSTRAP_STRATEGIES), BOOTSTRAP_MANIFEST_HOSTS):
        for host in group:
            if host not in hosts:
                hosts.append(host)
    return tuple(hosts)


def should_attempt_bootstrap_fetch(url: str) -> bool:
    return bool(matching_bootstrap_strategies(url))

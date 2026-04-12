from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

ManifestSource: TypeAlias = Literal["network", "dom", "js_eval", "iframe_dom"]
PlayerType: TypeAlias = Literal["hls", "dash", "progressive", "unknown"]


@dataclass(slots=True)
class SourceInfo:
    input: str | None
    type: Literal["url", "file"]


@dataclass(slots=True)
class ChannelInfo:
    id: int | None
    name: str | None
    heading: str | None


@dataclass(slots=True)
class PageInfo:
    title: str | None
    description: str | None
    canonicalUrl: str | None
    poster: str | None


@dataclass(slots=True)
class AlternatePlayer:
    label: str | None
    url: str
    active: bool


@dataclass(slots=True)
class PrimaryPlayer:
    label: Literal["primary"]
    url: str | None


@dataclass(slots=True)
class PlayerInfo:
    primary: PrimaryPlayer
    alternates: list[AlternatePlayer] = field(default_factory=list)
    embedHtml: str | None = None


@dataclass(slots=True)
class RelatedChannel:
    name: str | None
    title: str | None
    url: str | None


@dataclass(slots=True)
class RelatedInfo:
    label: str | None
    channels: list[RelatedChannel] = field(default_factory=list)


@dataclass(slots=True)
class ManifestResult:
    url: str
    player_type: PlayerType
    found_at_url: str
    source: ManifestSource


@dataclass(slots=True)
class PlayerResolution:
    label: str
    player_page_url: str
    player_final_url: str | None = None
    manifests: list[ManifestResult] = field(default_factory=list)
    iframes_seen: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class WrapperCatalog:
    source: SourceInfo
    channel: ChannelInfo
    page: PageInfo
    player: PlayerInfo
    related: RelatedInfo
    resolution: list[PlayerResolution] | None = None


@dataclass(slots=True)
class CatalogChannel:
    channel_id: int
    name: str
    watch_url: str
    search_hint: str | None
    group_letter: str | None
    country_code: str
    country_label: str

    @property
    def meta_id(self) -> str:
        return f"dlhd:ch:{self.channel_id}"


@dataclass(slots=True)
class ScheduleChannelLink:
    channel_id: int
    name: str
    country_code: str
    country_label: str


@dataclass(slots=True)
class LiveEvent:
    meta_id: str
    title: str
    time_text: str
    day_label: str
    category: str
    channels: list[ScheduleChannelLink] = field(default_factory=list)
    country_codes: list[str] = field(default_factory=list)
    ordinal: int = 0

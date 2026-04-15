from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
import json

from app import settings


@dataclass(frozen=True)
class ConfigChoice:
    value: str
    label: str

    @property
    def manifest_value(self) -> str:
        return f"{self.value}|{self.label}"


@dataclass(frozen=True)
class ConfigField:
    key: str
    title: str
    default: str
    choices: tuple[ConfigChoice, ...]

    def normalize(self, raw_value: object) -> str | None:
        if not isinstance(raw_value, (str, int, float, bool)):
            return None

        value = str(raw_value).split("|", 1)[0].strip()
        if value in {choice.value for choice in self.choices}:
            return value
        return None


def _timezone_choices() -> tuple[ConfigChoice, ...]:
    choices: list[ConfigChoice] = []
    for minutes in range(-12 * 60, 14 * 60 + 1, 30):
        sign = "+" if minutes >= 0 else "-"
        absolute = abs(minutes)
        hours = absolute // 60
        mins = absolute % 60
        choices.append(ConfigChoice(str(minutes), f"GMT {sign}{hours:02d}:{mins:02d}"))
    return tuple(choices)


def _country_choices() -> tuple[ConfigChoice, ...]:
    choices = [ConfigChoice("all", "All Countries")]
    for country_code in settings.VISIBLE_COUNTRY_CODES:
        choices.append(ConfigChoice(country_code, settings.COUNTRY_LABELS[country_code]))
    return tuple(choices)


def config_fields() -> tuple[ConfigField, ...]:
    return (
        ConfigField(
            key="scheduleOffsetMin",
            title="Schedule Timezone",
            default=str(settings.SCHEDULE_DISPLAY_GMT_OFFSET_MINUTES),
            choices=_timezone_choices(),
        ),
        ConfigField(
            key="catalogMode",
            title="Catalog Mode",
            default="full",
            choices=(
                ConfigChoice("full", "All catalogs"),
                ConfigChoice("focused", "Only all + one country + global"),
            ),
        ),
        ConfigField(
            key="preferredCountryCode",
            title="Preferred Country",
            default="all",
            choices=_country_choices(),
        ),
        ConfigField(
            key="channelStreamResults",
            title="Channel Stream Options",
            default=str(settings.CHANNEL_STREAM_MAX_RESULTS),
            choices=(
                ConfigChoice("1", "Best only"),
                ConfigChoice("2", "Two options"),
            ),
        ),
        ConfigField(
            key="liveStreamResults",
            title="Live Stream Options",
            default=str(settings.LIVE_STREAM_MAX_RESULTS),
            choices=(
                ConfigChoice("1", "Best only"),
                ConfigChoice("2", "Two options"),
                ConfigChoice("4", "More options"),
            ),
        ),
        ConfigField(
            key="eventStaleAfterMinutes",
            title="Hide Finished Events After",
            default=str(settings.EVENT_STALE_AFTER_MINUTES),
            choices=(
                ConfigChoice("120", "2 hours"),
                ConfigChoice("360", "6 hours"),
                ConfigChoice("720", "12 hours"),
            ),
        ),
    )


def config_field_map() -> dict[str, ConfigField]:
    return {field.key: field for field in config_fields()}


def configure_select_fields() -> list[dict[str, object]]:
    return [
        {
            "key": field.key,
            "title": field.title,
            "default": field.default,
            "options": [(choice.value, choice.label) for choice in field.choices],
        }
        for field in config_fields()
    ]


def manifest_config_fields() -> list[dict[str, object]]:
    return [
        {
            "key": field.key,
            "type": "select",
            "default": field.default,
            "title": field.title,
            "options": [choice.manifest_value for choice in field.choices],
        }
        for field in config_fields()
    ]


def normalize_user_config(data: Mapping[str, object]) -> tuple[dict[str, str], list[str]]:
    fields = config_field_map()
    normalized: dict[str, str] = {}
    issues: list[str] = []

    for raw_key, raw_value in data.items():
        key = str(raw_key)
        field = fields.get(key)
        if field is None:
            issues.append(f"unknown_key:{key}")
            continue

        value = field.normalize(raw_value)
        if value is None:
            issues.append(f"invalid_value:{key}")
            continue

        normalized[key] = value

    return normalized, issues


def parse_user_config(config: str | None) -> tuple[dict[str, str], list[str]]:
    if not config:
        return {}, []

    if config.startswith("cfg-"):
        return decode_user_config_token(config)

    try:
        parsed = json.loads(config)
    except (json.JSONDecodeError, TypeError):
        return {}, ["invalid_json"]

    if not isinstance(parsed, dict):
        return {}, ["not_object"]

    return normalize_user_config(parsed)


def decode_user_config_token(config: str) -> tuple[dict[str, str], list[str]]:
    token = config.removeprefix("cfg-")
    padding = "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode(token + padding).decode("utf-8")
    except Exception:
        return {}, ["invalid_token"]
    return parse_user_config(decoded)


def encode_user_config(config: Mapping[str, object]) -> str:
    normalized, _ = normalize_user_config(config)
    raw = json.dumps(normalized, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "cfg-" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _config_value(config: Mapping[str, str], key: str, default: str) -> str:
    return str(config.get(key, default)).split("|", 1)[0]


def _int_config_value(config: Mapping[str, str], key: str, default: int) -> int:
    try:
        return int(_config_value(config, key, str(default)))
    except ValueError:
        return default


def schedule_offset_minutes(config: Mapping[str, str]) -> int:
    return _int_config_value(config, "scheduleOffsetMin", settings.SCHEDULE_DISPLAY_GMT_OFFSET_MINUTES)


def catalog_mode(config: Mapping[str, str]) -> str:
    return _config_value(config, "catalogMode", "full")


def preferred_country_code(config: Mapping[str, str]) -> str | None:
    preferred = _config_value(config, "preferredCountryCode", "all")
    if preferred in {"", "all"}:
        return None
    return preferred if preferred in settings.COUNTRY_LABELS else None


def channel_stream_result_limit(config: Mapping[str, str]) -> int:
    return _int_config_value(config, "channelStreamResults", settings.CHANNEL_STREAM_MAX_RESULTS)


def live_stream_result_limit(config: Mapping[str, str]) -> int:
    return _int_config_value(config, "liveStreamResults", settings.LIVE_STREAM_MAX_RESULTS)


def event_stale_after_minutes(config: Mapping[str, str]) -> int:
    return _int_config_value(config, "eventStaleAfterMinutes", settings.EVENT_STALE_AFTER_MINUTES)

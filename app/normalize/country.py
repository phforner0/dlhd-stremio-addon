from __future__ import annotations

import re
import unicodedata

from app.settings import COUNTRY_LABELS


def _normalized(value: str | None) -> str:
    raw = value or ""
    ascii_value = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_value).strip().lower()


COUNTRY_PATTERNS = {
    "br": [r"\bbrasil\b", r"\bbrazil\b"],
    "us": [r"\busa\b", r"\bunited states\b"],
    "gb": [r"\buk\b", r"\bunited kingdom\b"],
    "es": [r"\bspain\b", r"\bespana\b", r"\bespanol\b$"],
    "fr": [r"\bfrance\b"],
    "it": [r"\bitaly\b", r"\bitalia\b"],
    "pt": [r"\bportugal\b"],
    "tr": [r"\bturkey\b", r"\bturkiye\b"],
    "pl": [r"\bpoland\b"],
    "ca": [r"\bcanada\b", r"\bca\b$"],
    "mx": [r"\bmexico\b", r"\bmx\b$"],
    "de": [r"\bgermany\b", r"\bde\b$"],
}

REGIONAL_PATTERNS = [
    r"\bmena\b",
    r"\bafrique\b",
    r"\barabic\b",
    r"\benglish\b",
    r"\ben espanol\b",
    r"\bworld\b",
    r"\binternational\b",
    r"\bglobal\b",
]


def classify_channel_country(name: str, search_hint: str | None = None) -> tuple[str, str]:
    haystack = " | ".join(part for part in (_normalized(name), _normalized(search_hint)) if part)
    for country_code, patterns in COUNTRY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, haystack):
                return country_code, COUNTRY_LABELS[country_code]

    for pattern in REGIONAL_PATTERNS:
        if re.search(pattern, haystack):
            return "global", COUNTRY_LABELS["global"]

    return "global", COUNTRY_LABELS["global"]


def classify_event_countries(channel_codes: list[str]) -> list[str]:
    explicit = [code for code in dict.fromkeys(channel_codes) if code != "global"]
    if explicit:
        return explicit
    return ["global"]

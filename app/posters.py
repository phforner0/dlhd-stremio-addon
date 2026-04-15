from __future__ import annotations

from textwrap import wrap
from xml.sax.saxutils import escape

from app import settings


def _wrapped_lines(text: str, *, width: int, max_lines: int) -> list[str]:
    normalized = " ".join((text or "").split())
    if not normalized:
        return []

    lines = wrap(normalized, width=width, break_long_words=True, break_on_hyphens=False)
    if len(lines) <= max_lines:
        return lines

    truncated = lines[: max_lines - 1]
    remainder = " ".join(lines[max_lines - 1 :])
    last_line = wrap(remainder, width=max(8, width - 2), break_long_words=True, break_on_hyphens=False)
    truncated.append((last_line[0] if last_line else remainder)[: width - 1].rstrip() + "...")
    return truncated


def _text_block(lines: list[str], *, x: int, start_y: int, line_height: int, font_size: int, weight: int, fill: str) -> str:
    if not lines:
        return ""

    tspans = []
    for index, line in enumerate(lines):
        tspans.append(f'<tspan x="{x}" y="{start_y + (index * line_height)}">{escape(line)}</tspan>')

    return (
        f'<text fill="{fill}" font-size="{font_size}" font-weight="{weight}" '
        f'font-family="Arial, Helvetica, sans-serif">{"".join(tspans)}</text>'
    )


def render_svg_poster(title: str, subtitle: str, accent_code: str) -> str:
    accent = settings.COUNTRY_COLORS.get(accent_code, settings.COUNTRY_COLORS["global"])
    safe_title = title[:80] or settings.ADDON_NAME
    safe_subtitle = subtitle[:140] or settings.ADDON_DESCRIPTION
    title_lines = _wrapped_lines(safe_title, width=16, max_lines=4)
    subtitle_lines = _wrapped_lines(safe_subtitle, width=30, max_lines=5)
    title_block = _text_block(title_lines, x=72, start_y=250, line_height=64, font_size=54, weight=700, fill="#f8fafc")
    subtitle_block = _text_block(subtitle_lines, x=72, start_y=628, line_height=32, font_size=24, weight=400, fill="#dbe4f0")
    safe_label = escape(safe_title)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900" viewBox="0 0 600 900" role="img" aria-label="{safe_label}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0f172a"/>
      <stop offset="55%" stop-color="#111827"/>
      <stop offset="100%" stop-color="#020617"/>
    </linearGradient>
    <linearGradient id="accentGlow" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{accent}" stop-opacity="0.9"/>
      <stop offset="100%" stop-color="{accent}" stop-opacity="0.12"/>
    </linearGradient>
  </defs>
  <rect width="600" height="900" fill="url(#bg)"/>
  <circle cx="490" cy="126" r="180" fill="url(#accentGlow)"/>
  <circle cx="88" cy="820" r="120" fill="{accent}" opacity="0.12"/>
  <rect x="38" y="38" width="524" height="824" rx="34" fill="#08111f" stroke="#1e293b" stroke-width="2"/>
  <rect x="38" y="38" width="524" height="18" rx="9" fill="{accent}"/>
  <rect x="72" y="92" width="132" height="40" rx="20" fill="{accent}" opacity="0.92"/>
  <text x="94" y="119" fill="#020617" font-size="20" font-weight="700" font-family="Arial, Helvetica, sans-serif">DLHD LIVE</text>
  <text x="72" y="174" fill="#93c5fd" font-size="22" font-weight="700" font-family="Arial, Helvetica, sans-serif">STREMIO ADDON</text>
  {title_block}
  <rect x="72" y="570" width="456" height="2" rx="1" fill="#243041"/>
  {subtitle_block}
  <text x="72" y="828" fill="#64748b" font-size="18" font-weight="600" font-family="Arial, Helvetica, sans-serif">{escape(settings.ADDON_NAME.upper()[:36])}</text>
</svg>"""

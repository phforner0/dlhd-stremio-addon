from __future__ import annotations

from xml.sax.saxutils import escape

from app import settings


def render_svg_poster(title: str, subtitle: str, accent_code: str) -> str:
    accent = settings.COUNTRY_COLORS.get(accent_code, settings.COUNTRY_COLORS["global"])
    safe_title = escape(title[:80] or settings.ADDON_NAME)
    safe_subtitle = escape(subtitle[:120] or settings.ADDON_DESCRIPTION)
    return f"""<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"600\" height=\"900\" viewBox=\"0 0 600 900\" role=\"img\" aria-label=\"{safe_title}\">
  <defs>
    <linearGradient id=\"bg\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"1\">
      <stop offset=\"0%\" stop-color=\"#111827\"/>
      <stop offset=\"100%\" stop-color=\"#030712\"/>
    </linearGradient>
  </defs>
  <rect width=\"600\" height=\"900\" fill=\"url(#bg)\"/>
  <rect x=\"42\" y=\"42\" width=\"516\" height=\"816\" rx=\"28\" fill=\"#0f172a\" stroke=\"{accent}\" stroke-width=\"4\"/>
  <rect x=\"42\" y=\"42\" width=\"516\" height=\"18\" rx=\"9\" fill=\"{accent}\"/>
  <text x=\"60\" y=\"150\" fill=\"#93c5fd\" font-size=\"26\" font-family=\"Arial, sans-serif\">DLHD</text>
  <foreignObject x=\"60\" y=\"190\" width=\"480\" height=\"380\">
    <div xmlns=\"http://www.w3.org/1999/xhtml\" style=\"color:#ffffff;font:700 48px/1.12 Arial, sans-serif;\">{safe_title}</div>
  </foreignObject>
  <foreignObject x=\"60\" y=\"610\" width=\"480\" height=\"160\">
    <div xmlns=\"http://www.w3.org/1999/xhtml\" style=\"color:#d1d5db;font:400 28px/1.3 Arial, sans-serif;\">{safe_subtitle}</div>
  </foreignObject>
</svg>"""

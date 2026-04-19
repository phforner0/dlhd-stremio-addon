from __future__ import annotations

from app.settings import _build_proxy_allowed_hosts


def test_proxy_allowed_hosts_merge_defaults_with_legacy_env_list() -> None:
    hosts = _build_proxy_allowed_hosts(
        "dlstreams.top,.dlstreams.top,embedkclx.sbs,.embedkclx.sbs,vid.aivideox.site,.aivideox.site",
        strict=False,
    )

    assert "enviromentalspa2.sbs" in hosts
    assert ".enviromentalspa2.sbs" in hosts
    assert "viewembed.ru" in hosts
    assert "soyspace.cyou" in hosts
    assert ".aiphotofree.site" in hosts


def test_proxy_allowed_hosts_strict_mode_keeps_only_explicit_hosts() -> None:
    hosts = _build_proxy_allowed_hosts(
        "dlstreams.top,.dlstreams.top",
        strict=True,
    )

    assert hosts == ("dlstreams.top", ".dlstreams.top")

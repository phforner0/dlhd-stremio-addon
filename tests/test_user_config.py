from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from app.main import app
from app.user_config import configure_select_fields, encode_user_config, manifest_config_fields, parse_user_config


def test_parse_user_config_reports_unknown_keys_and_invalid_values() -> None:
    parsed, issues = parse_user_config('{"catalogMode":"broken","channelStreamResults":"2","oops":"x"}')

    assert parsed == {"channelStreamResults": "2"}
    assert "invalid_value:catalogMode" in issues
    assert "unknown_key:oops" in issues


def test_encode_user_config_normalizes_display_values() -> None:
    token = encode_user_config(
        {
            "catalogMode": "focused|Only all + one country + global",
            "preferredCountryCode": "br|Brazil",
            "channelStreamResults": "2|Two options",
        }
    )

    parsed, issues = parse_user_config(token)

    assert issues == []
    assert parsed == {
        "catalogMode": "focused",
        "preferredCountryCode": "br",
        "channelStreamResults": "2",
    }


def test_manifest_and_configure_fields_share_same_keys() -> None:
    manifest_keys = [field["key"] for field in manifest_config_fields()]
    configure_keys = [field["key"] for field in configure_select_fields()]

    assert manifest_keys == configure_keys


def test_invalid_config_token_is_logged_once_per_request(caplog) -> None:
    client = TestClient(app)
    caplog.set_level(logging.INFO, logger="dlhd.addon")

    response = client.get("/cfg-not-a-real-token/manifest.json")

    assert response.status_code == 200
    addon_messages = [record.getMessage() for record in caplog.records if record.name == "dlhd.addon"]
    assert any("invalid_user_config" in message for message in addon_messages)
    assert any("invalid_token" in message for message in addon_messages)

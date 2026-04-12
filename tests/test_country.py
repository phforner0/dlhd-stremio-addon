from app.normalize.country import classify_channel_country, classify_event_countries


def test_classify_brazil_channel() -> None:
    code, label = classify_channel_country("ESPN Brasil")
    assert code == "br"
    assert label == "Brazil"


def test_classify_united_kingdom_channel() -> None:
    code, label = classify_channel_country("TNT Sports 1 UK")
    assert code == "gb"
    assert label == "United Kingdom"


def test_classify_ambiguous_channel_as_global() -> None:
    code, label = classify_channel_country("Comedy Central")
    assert code == "global"
    assert label == "Global / Regional"


def test_event_country_codes_drop_global_when_explicit_exists() -> None:
    assert classify_event_countries(["br", "global"]) == ["br"]


def test_event_country_codes_fall_back_to_global() -> None:
    assert classify_event_countries(["global", "global"]) == ["global"]

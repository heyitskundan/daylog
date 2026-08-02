from daylog.util import human_duration, iso, local_date_str, parse_iso, utcnow


def test_iso_parse_round_trip():
    now = utcnow()
    assert parse_iso(iso(now)).replace(microsecond=0) == now.replace(microsecond=0)


def test_iso_naive_datetime_assumed_utc():
    from datetime import datetime

    naive = datetime(2026, 6, 23, 14, 5, 0)
    assert iso(naive) == "2026-06-23T14:05:00+00:00"


def test_parse_iso_handles_trailing_z():
    assert parse_iso("2026-06-23T14:05:00Z") == parse_iso("2026-06-23T14:05:00+00:00")


def test_human_duration_seconds():
    assert human_duration(45) == "45s"


def test_human_duration_minutes():
    assert human_duration(90) == "1m 30s"
    assert human_duration(120) == "2m"


def test_human_duration_hours():
    assert human_duration(3661) == "1h 1m"


def test_local_date_str_format():
    from datetime import datetime

    dt = datetime(2026, 6, 23, 9, 30)
    assert local_date_str(dt) == "2026-06-23"

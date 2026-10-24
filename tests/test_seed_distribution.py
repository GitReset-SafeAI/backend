"""Keep SQL pairs and the requested synthetic geographic split consistent."""

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_seed_distribution_and_pairing() -> None:
    rows = json.loads(
        (ROOT / "sample-data/synthetic_map_reports.json").read_text(encoding="utf-8")
    )["reports"]
    assert len(rows) == 100
    assert Counter(row["region"] for row in rows) == {"colombo": 50, "galle": 30, "other": 20}
    assert len({row["cluster"] for row in rows if row["region"] == "other"}) == 10
    for row in rows:
        assert not row["original_text"].startswith("Synthetic demo:")
        assert not row["translated_text"].startswith("Synthetic demo:")
        if row["region"] == "colombo":
            assert 6.84 < row["latitude"] < 6.96
            assert 79.83 < row["longitude"] < 79.93
        elif row["region"] == "galle":
            assert 5.99 < row["latitude"] < 6.06
            assert 80.19 < row["longitude"] < 80.27
    sql = (ROOT / "supabase/seed.sql").read_text(encoding="utf-8")
    reports, analyses = sql.split("insert into public.incident_ai_analyses (")
    ids = re.findall(r"^        \('([\da-f-]{36})', demo_user,", reports, re.M)
    pairs = re.findall(r"^        \('([\da-f-]{36})', '([\da-f-]{36})',", analyses, re.M)
    assert len(ids) == len(set(ids)) == 100
    assert len(pairs) == len({pair[0] for pair in pairs}) == 100
    assert {pair[1] for pair in pairs} == set(ids) == {row["id"] for row in rows}
    assert "original_text like" not in sql.lower()
    assert "where id = ANY(fixture_report_ids)" in sql

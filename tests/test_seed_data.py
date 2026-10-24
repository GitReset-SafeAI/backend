"""The SQL seed must contain 100 valid, uniquely paired report-analysis fixtures."""

import json
import re
from datetime import datetime
from pathlib import Path

from app.modules.incident_ai.schemas import IncidentAnalysis
from app.schemas.report import ReportSubmission

ROOT = Path(__file__).resolve().parents[1]


def test_seed_has_exactly_100_matched_pairs() -> None:
    sql = (ROOT / "supabase/seed.sql").read_text(encoding="utf-8")
    report_block, analysis_block = sql.split("insert into public.incident_ai_analyses (")
    report_ids = re.findall(r"^        \('([\da-f-]{36})', demo_user,", report_block, re.M)
    pairs = re.findall(r"^        \('([\da-f-]{36})', '([\da-f-]{36})',", analysis_block, re.M)
    assert len(report_ids) == len(set(report_ids)) == 100
    assert len(pairs) == len({pair[0] for pair in pairs}) == 100
    assert {pair[1] for pair in pairs} == set(report_ids)
    assert sql.count("ON CONFLICT (id) DO UPDATE SET") == 2
    assert "delete from" not in sql.lower()


def test_seed_values_match_current_contracts() -> None:
    data = json.loads((ROOT / "sample-data/synthetic_map_reports.json").read_text(encoding="utf-8"))
    assert data["synthetic"] is True
    rows = data["reports"]
    assert len(rows) == 100
    for row in rows:
        report = ReportSubmission.model_validate(
            {field: row[field] for field in ReportSubmission.model_fields if field in row}
        )
        analysis = IncidentAnalysis(
            id=row["analysis_id"],
            report_id=row["id"],
            analysis_status="completed",
            IncidentCategory=row["incident_category"],
            SafetyConcernCategory=row["safety_concern_category"],
            reported_time=row["reported_time"],
            street_lights=row["street_lights"],
            immediate_danger_reported=None,
            severity=row["severity"],
            confidence=row["confidence"],
            extracted_details=row["extracted_details"],
        )
        assert report.original_text.startswith("Synthetic demo:")
        assert analysis.extracted_details.summary
        assert datetime.fromisoformat(row["submitted_at"]) > report.occurred_at

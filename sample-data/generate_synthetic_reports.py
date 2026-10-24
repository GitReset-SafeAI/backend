"""Generate the synthetic map dataset.

SYNTHETIC DATA. Every row is invented. The clusters are placed on public
landmarks so the demo is recognisable, and they do not describe real
harassment, real reports, or real risk at those places. Regenerate with:

    python sample-data/generate_synthetic_reports.py
"""

import json
import random
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

SEED = 20261023
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "synthetic_map_reports.json"
SEED_SQL = HERE.parent / "supabase" / "seed.sql"

# Stable ids, so regenerating does not churn the seed file.
NAMESPACE = uuid.UUID("3f2b1a76-0000-4000-8000-5afe20261023")

# Neutral, invented report bodies, prefixed the way the reporting module's own
# documented example is so synthetic rows are obvious in the database.
# language_hint uses that module's vocabulary, including si-Latn for Sinhala
# written in English letters.
PREFIX = "Synthetic demo: "

DESCRIPTIONS = {
    "following_night": {
        "en": "someone followed me from the station for several minutes.",
        "si": "ස්ථානයෙන් පිටව යන විට කෙනෙකු මා පසුපස පැමිණියා.",
        "si-Latn": "bus eken bassa gaman kenek mage passen awa.",
    },
    "dark_lane": {
        "en": "the lane had no working lights and felt unsafe.",
        "si": "මෙම පාරේ ආලෝකය නොතිබූ අතර අනාරක්ෂිත බවක් දැනුණා.",
        "si-Latn": "light nathi nisa e parata yanna bayai.",
    },
    "crowd_contact": {
        "en": "someone pushed against me while boarding and would not move away.",
        "si": "බසයට නගින විට කෙනෙකු ළං වී ඉවත් නොවුණා.",
        "si-Latn": "bus ekata nagina kota kenek langa wela ayin une na.",
    },
    "staring_evening": {
        "en": "a man kept staring and moved closer when I moved away.",
        "si": "පුද්ගලයෙකු දිගටම බලා සිටි අතර මා ඉවත් වූ විට ළං වුණා.",
        "si-Latn": "kenek digatama balagena hitiya, man ayin wenakota langa una.",
    },
}

LANGUAGES = ("en", "si", "si-Latn")
INPUT_METHODS = ("text", "text", "text", "voice")

# The reporting module's own context vocabularies, which differ in case and
# wording from the incident-AI ones; they are not interchangeable.
LIGHTING = {
    "well_lit": "WELL_LIT",
    "partially_lit": "PARTIALLY_LIT",
    "poorly_lit_or_dark": "POORLY_LIT_OR_DARK",
    "day_light": "DAYLIGHT",
    "not_sure": "UNKNOWN_OR_PREFER_NOT_TO_SAY",
}
SAFETY_STATUS = ("SAFE_NOW", "UNSURE", "MAY_STILL_BE_IN_DANGER", "PREFER_NOT_TO_SAY")

# (name, latitude, longitude, report count, metre spread, dominant profile)
CLUSTERS = [
    ("Bambalapitiya station approach", 6.8905, 79.8565, 12, 110, "following_night"),
    ("Majestic City side road", 6.8938, 79.8552, 8, 90, "dark_lane"),
    ("Pettah bus stand", 6.9361, 79.8565, 14, 150, "crowd_contact"),
    ("Nugegoda junction", 6.8649, 79.8997, 8, 120, "staring_evening"),
    ("Borella junction", 6.9157, 79.8777, 6, 100, "dark_lane"),
    ("Maradana underpass", 6.9293, 79.8653, 6, 80, "following_night"),
]

PROFILES = {
    "following_night": {
        "incident": ["UNWANTED_FOLLOWING"],
        "concern": [None],
        "lights": ["poorly_lit_or_dark", "partially_lit"],
        "times": ["night", "night", "night", "late_night", "evening"],
        "severity": (5, 6),
    },
    "dark_lane": {
        "incident": [None],
        "concern": ["POOR_STREET_LIGHTING"],
        "lights": ["poorly_lit_or_dark"],
        "times": ["late_night", "late_night", "late_night", "night"],
        "severity": (2, 5),
    },
    "crowd_contact": {
        "incident": ["UNWANTED_PHYSICAL_CONTACT"],
        "concern": [None],
        "lights": ["day_light", "well_lit"],
        "times": ["afternoon", "afternoon", "morning", "evening"],
        "severity": (5, 6),
    },
    "staring_evening": {
        "incident": ["UNCOMFORTABLE_STARING"],
        "concern": [None],
        "lights": ["partially_lit", "well_lit"],
        "times": ["evening", "evening", "evening", "night"],
        "severity": (3, 4),
    },
}

# Scattered reports that must NOT form a hotspot, so suppression is visible.
NOISE_POINTS = 46  # 54 clustered reports + 46 scattered reports = exactly 100.
NOISE_BOX = (6.84, 79.84, 6.95, 79.92)

METRES_PER_DEGREE = 111_320.0


def main() -> None:
    rng = random.Random(SEED)
    now = datetime.now(UTC)
    rows: list[dict] = []

    def add(latitude: float, longitude: float, profile: str, cluster: str | None) -> None:
        spec = PROFILES[profile]
        age_days = rng.randint(1, 29)
        index = len(rows) + 1
        report_id = str(uuid.uuid5(NAMESPACE, f"report-{index}"))
        language = rng.choice(LANGUAGES)
        ai_lights = rng.choice(spec["lights"])
        reported_lights = ai_lights
        reported_time = rng.choice(spec["times"])
        hour = {"morning": 8, "afternoon": 14, "evening": 18, "night": 22, "late_night": 2}[
            reported_time
        ]
        occurred_at = (
            now.astimezone(timezone(timedelta(hours=5, minutes=30))) - timedelta(days=age_days)
        ).replace(hour=hour, minute=rng.randrange(60), second=0, microsecond=0)
        behaviour = DESCRIPTIONS[profile]["en"]

        rows.append(
            {
                "id": report_id,
                "reference": f"synthetic-{index:03d}",
                "cluster": cluster,
                "analysis_id": str(uuid.uuid5(NAMESPACE, f"analysis-{index}")),
                "occurred_at": occurred_at.isoformat(),
                "submitted_at": (occurred_at + timedelta(minutes=10)).isoformat(),
                "latitude": round(latitude, 6),
                "longitude": round(longitude, 6),
                "original_text": PREFIX + DESCRIPTIONS[profile][language],
                "translated_text": PREFIX + DESCRIPTIONS[profile]["en"],
                "language_hint": language,
                "input_method": rng.choice(INPUT_METHODS),
                "lighting_condition": LIGHTING[reported_lights],
                "current_safety_status": rng.choice(SAFETY_STATUS),
                "incident_category": rng.choice(spec["incident"]),
                "safety_concern_category": rng.choice(spec["concern"]),
                # The map reads lighting from the incident-AI column only. The
                # reporter's own answer is private context that the reporting
                # module has not approved for map responses.
                "street_lights": ai_lights,
                "reported_time": reported_time,
                "severity": rng.randint(*spec["severity"]),
                "confidence": round(rng.uniform(0.45, 0.97), 2),
                "extracted_details": {
                    "summary": f"Synthetic interpretation: the reporter states that {behaviour}",
                    "reported_behaviours": [] if profile == "dark_lane" else [behaviour],
                    "reported_location": cluster,
                },
            }
        )

    for name, latitude, longitude, count, spread_m, profile in CLUSTERS:
        for _ in range(count):
            spread = spread_m / METRES_PER_DEGREE
            add(
                latitude + rng.gauss(0, spread),
                longitude + rng.gauss(0, spread),
                profile,
                name,
            )

    min_lat, min_lon, max_lat, max_lon = NOISE_BOX
    for _ in range(NOISE_POINTS):
        add(
            rng.uniform(min_lat, max_lat),
            rng.uniform(min_lon, max_lon),
            rng.choice(list(PROFILES)),
            None,
        )

    assert len(rows) == 100
    assert len({row["id"] for row in rows}) == 100
    assert len({row["analysis_id"] for row in rows}) == 100
    _write_seed_sql(rows)
    OUTPUT.write_text(
        json.dumps(
            {
                "synthetic": True,
                "generated_by": "sample-data/generate_synthetic_reports.py",
                "seed": SEED,
                "warning": "Invented data. Not real reports and not real risk at these places.",
                "reports": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} synthetic reports to {OUTPUT}")


def _sql(value) -> str:
    """Quote a Python value as a SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _write_seed_sql(rows: list[dict]) -> None:
    """Emit the same dataset as SQL, for a database-backed demo.

    Reports belong to the reporting module's public.reports, whose user_id
    references auth.users, so the seed attaches every row to one existing
    account rather than inventing one. Ids are deterministic, so re-running
    replaces the previous synthetic rows instead of duplicating them.
    """
    report_values = ",\n".join(
        "        ("
        + ", ".join(
            [
                _sql(row["id"]),
                "demo_user",
                _sql(row["original_text"]),
                _sql(row["translated_text"]),
                _sql(row["input_method"]),
                _sql(row["language_hint"]),
                str(row["latitude"]),
                str(row["longitude"]),
                _sql(row["occurred_at"]),
                _sql(row["lighting_condition"]),
                _sql(row["reported_time"]),
                _sql(row["current_safety_status"]),
                _sql(row["submitted_at"]),
            ]
        )
        + ")"
        for row in rows
    )

    analysis_values = ",\n".join(
        "        ("
        + ", ".join(
            [
                _sql(row["analysis_id"]),
                _sql(row["id"]),
                "'completed'",
                _sql(row["incident_category"]),
                _sql(row["safety_concern_category"]),
                _sql(row["reported_time"]),
                "NULL",
                _sql(row["severity"]),
                _sql(row["confidence"]),
                _sql(row["street_lights"]),
                "'" + json.dumps(row["extracted_details"]).replace("'", "''") + "'::jsonb",
            ]
        )
        + ")"
        for row in rows
    )

    SEED_SQL.write_text(
        f"""-- Synthetic demo data for the safety map.
--
-- Generated by sample-data/generate_synthetic_reports.py. Every row is
-- invented: these are not real reports, and they do not describe real
-- harassment or real risk at the places named. Report bodies are prefixed
-- "Synthetic demo: " so they are obvious in the database.
--
-- public.reports.user_id references auth.users, so this attaches the rows to
-- one existing account instead of inventing a user. Create an account first
-- if the project has none.
--
-- Exactly 100 synthetic reports and 100 matching synthetic analyses.
-- Safe to re-run: stable IDs are upserted; unrelated reports are not deleted.
-- Analyses are synthetic fixtures, not real LLM outputs. No API calls are made.
-- Historical submission times avoid counting bulk fixtures as live spam.
-- Apply all migrations first. Run this file in the Supabase SQL Editor.

begin;

do $$
declare
    demo_user uuid;
begin
    select id into demo_user from auth.users order by created_at limit 1;
    if demo_user is null then
        raise exception
            'No auth.users row exists. Create one account before seeding demo data.';
    end if;

    insert into public.reports (
        id, user_id, original_text, translated_text, input_method,
        language_hint, latitude, longitude, occurred_at,
        lighting_condition, reported_time, current_safety_status, submitted_at
    ) values
{report_values}
    ON CONFLICT (id) DO UPDATE SET
        original_text = EXCLUDED.original_text,
        translated_text = EXCLUDED.translated_text,
        input_method = EXCLUDED.input_method,
        language_hint = EXCLUDED.language_hint,
        latitude = EXCLUDED.latitude,
        longitude = EXCLUDED.longitude,
        occurred_at = EXCLUDED.occurred_at,
        lighting_condition = EXCLUDED.lighting_condition,
        reported_time = EXCLUDED.reported_time,
        current_safety_status = EXCLUDED.current_safety_status,
        submitted_at = EXCLUDED.submitted_at;

    insert into public.incident_ai_analyses (
        id, report_id, analysis_status, "IncidentCategory",
        "SafetyConcernCategory", reported_time, immediate_danger_reported,
        severity, confidence, street_lights, extracted_details
    ) values
{analysis_values}
    ON CONFLICT (id) DO UPDATE SET
        report_id = EXCLUDED.report_id,
        analysis_status = EXCLUDED.analysis_status,
        "IncidentCategory" = EXCLUDED."IncidentCategory",
        "SafetyConcernCategory" = EXCLUDED."SafetyConcernCategory",
        reported_time = EXCLUDED.reported_time,
        immediate_danger_reported = EXCLUDED.immediate_danger_reported,
        severity = EXCLUDED.severity,
        confidence = EXCLUDED.confidence,
        street_lights = EXCLUDED.street_lights,
        extracted_details = EXCLUDED.extracted_details;
end $$;

commit;
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

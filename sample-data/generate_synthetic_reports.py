"""Generate the synthetic map dataset.

SYNTHETIC DATA. Every row is invented. The clusters are placed on public
landmarks so the demo is recognisable, and they do not describe real
harassment, real reports, or real risk at those places. Regenerate with:

    python sample-data/generate_synthetic_reports.py
"""

import json
import random
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

SEED = 20261023
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "synthetic_map_reports.json"
SEED_SQL = HERE.parent / "supabase" / "seed.sql"

# Stable ids, so regenerating does not churn the seed file.
NAMESPACE = uuid.UUID("3f2b1a76-0000-4000-8000-5afe20261023")

# Neutral, invented report bodies. Deliberately plain: synthetic data should
# carry no more detail than the demo needs.
DESCRIPTIONS = {
    "following_night": [
        ("en", "Someone followed me from the station for several minutes."),
        ("si", "ස්ථානයෙන් පිටව යන විට කෙනෙකු මා පසුපස පැමිණියා."),
        ("mixed", "Bus eken බැස්ස ගමන් kenek mage passen awa."),
    ],
    "dark_lane": [
        ("en", "The lane had no working lights and felt unsafe."),
        ("si", "මෙම පාරේ ආලෝකය නොතිබූ අතර අනාරක්ෂිත බවක් දැනුණා."),
        ("mixed", "Light නැති nisa ratte eka parata yanna bayai."),
    ],
    "crowd_contact": [
        ("en", "Someone pushed against me while boarding and would not move away."),
        ("si", "බසයට නගින විට කෙනෙකු ළං වී ඉවත් නොවුණා."),
        ("mixed", "Bus ekata nagina kota kenek langa wela aine."),
    ],
    "staring_evening": [
        ("en", "A man kept staring and moved closer when I moved away."),
        ("si", "පුද්ගලයෙකු දිගටම බලා සිටි අතර මා ඉවත් වූ විට ළං වුණා."),
        ("mixed", "Ekka kenek digatama balagena hitiya, man යනකොටත් passen awa."),
    ],
}

INPUT_METHODS = ("text", "text", "text", "voice")
CROWD_LEVELS = ("empty", "few_people", "busy", "crowded", "not_sure")
LOCATION_SOURCES = ("device", "map_pin", "place_name")

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
        "incident": ["UNWANTED_FOLLOWING", "THREATENING_BEHAVIOUR"],
        "concern": ["PREVIOUS_SAFETY_CONCERNS_AT_LOCATION", None],
        "lights": ["poorly_lit_or_dark", "partially_lit"],
        "times": ["night", "night", "night", "late_night", "evening"],
        "severity": (5, 9),
    },
    "dark_lane": {
        "incident": [None, "UNCOMFORTABLE_STARING"],
        "concern": ["POOR_STREET_LIGHTING", "ISOLATED_OR_DESERTED_ROAD"],
        "lights": ["poorly_lit_or_dark"],
        "times": ["late_night", "late_night", "late_night", "night"],
        "severity": (2, 5),
    },
    "crowd_contact": {
        "incident": ["UNWANTED_PHYSICAL_CONTACT", "VERBAL_SEXUAL_HARASSMENT"],
        "concern": ["UNSAFE_BUS_STOP", None],
        "lights": ["day_light", "well_lit"],
        "times": ["afternoon", "afternoon", "morning", "evening"],
        "severity": (4, 8),
    },
    "staring_evening": {
        "incident": ["UNCOMFORTABLE_STARING", "VERBAL_SEXUAL_HARASSMENT"],
        "concern": [None, "FEELING_UNSAFE_WITHOUT_CONFIRMED_INCIDENT"],
        "lights": ["partially_lit", "well_lit"],
        "times": ["evening", "evening", "evening", "night"],
        "severity": (2, 6),
    },
}

# Scattered reports that must NOT form a hotspot, so suppression is visible.
NOISE_POINTS = 24
NOISE_BOX = (6.84, 79.84, 6.95, 79.92)

METRES_PER_DEGREE = 111_320.0


def main() -> None:
    rng = random.Random(SEED)
    now = datetime.now(UTC)
    rows: list[dict] = []

    def add(latitude: float, longitude: float, profile: str, cluster: str | None) -> None:
        spec = PROFILES[profile]
        age_days = rng.randint(0, 29)
        index = len(rows) + 1
        report_id = str(uuid.uuid5(NAMESPACE, f"report-{index}"))
        language, description = rng.choice(DESCRIPTIONS[profile])
        ai_lights = rng.choice(spec["lights"])
        # Reporters answer the optional lighting question about half the time.
        reported_lights = ai_lights if rng.random() < 0.5 else "not_sure"
        reported_time = rng.choice(spec["times"])

        rows.append(
            {
                "id": report_id,
                "reference": f"synthetic-{index:03d}",
                "cluster": cluster,
                "analysis_id": str(uuid.uuid5(NAMESPACE, f"analysis-{index}")),
                "occurred_at": (
                    now - timedelta(days=age_days, hours=rng.randint(0, 23))
                ).isoformat(),
                "latitude": round(latitude, 6),
                "longitude": round(longitude, 6),
                "description": description,
                "report_language": language,
                "input_method": rng.choice(INPUT_METHODS),
                "location_source": rng.choice(LOCATION_SOURCES),
                "location_accuracy_m": rng.choice([8, 15, 25, 40]),
                "crowd_level": rng.choice(CROWD_LEVELS),
                "others_nearby": rng.choice([True, False, None]),
                "reported_street_lights": reported_lights,
                "incident_category": rng.choice(spec["incident"]),
                "safety_concern_category": rng.choice(spec["concern"]),
                # The view prefers what the reporter said over what the AI
                # inferred, so the no-database path resolves it the same way.
                "street_lights": ai_lights if reported_lights == "not_sure" else reported_lights,
                "ai_street_lights": ai_lights,
                "reported_time": reported_time,
                "severity": rng.randint(*spec["severity"]),
                "confidence": round(rng.uniform(0.45, 0.97), 2),
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
        + "\n"
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
    """Emit the same dataset as SQL, for a database-backed demo."""
    lines = [
        "-- Synthetic demo data for the safety map.",
        "--",
        "-- Generated by sample-data/generate_synthetic_reports.py. Every row is",
        "-- invented: these are not real reports, and they do not describe real",
        "-- harassment or real risk at the places named. Rows are marked",
        "-- is_synthetic so they can always be told apart and removed.",
        "--",
        "-- Safe to re-run: existing synthetic rows are replaced.",
        "",
        "BEGIN;",
        "",
        "DELETE FROM public.incident_ai_analyses",
        " WHERE report_id IN (SELECT id FROM public.incident_reports WHERE is_synthetic);",
        "DELETE FROM public.incident_reports WHERE is_synthetic;",
        "",
        "INSERT INTO public.incident_reports (",
        "    id, created_at, description, input_method, report_language,",
        "    occurred_at, reported_time, location, location_source,",
        "    location_accuracy_m, reported_street_lights, crowd_level,",
        "    others_nearby, is_synthetic",
        ") VALUES",
    ]
    values = []
    for row in rows:
        values.append(
            "    ("
            + ", ".join(
                [
                    _sql(row["id"]),
                    "now()",
                    _sql(row["description"]),
                    _sql(row["input_method"]),
                    _sql(row["report_language"]),
                    _sql(row["occurred_at"]),
                    _sql(row["reported_time"]),
                    "ST_SetSRID(ST_MakePoint("
                    f"{row['longitude']}, {row['latitude']}), 4326)::geography",
                    _sql(row["location_source"]),
                    _sql(row["location_accuracy_m"]),
                    _sql(row["reported_street_lights"]),
                    _sql(row["crowd_level"]),
                    _sql(row["others_nearby"]),
                    "true",
                ]
            )
            + ")"
        )
    lines.append(",\n".join(values) + ";")

    lines += [
        "",
        "INSERT INTO public.incident_ai_analyses (",
        '    id, report_id, analysis_status, "IncidentCategory",',
        '    "SafetyConcernCategory", reported_time, immediate_danger_reported,',
        "    severity, confidence, street_lights, extracted_details",
        ") VALUES",
    ]
    values = []
    for row in rows:
        values.append(
            "    ("
            + ", ".join(
                [
                    _sql(row["analysis_id"]),
                    _sql(row["id"]),
                    "'completed'",
                    _sql(row["incident_category"]),
                    _sql(row["safety_concern_category"]),
                    _sql(row["reported_time"]),
                    "false",
                    _sql(row["severity"]),
                    _sql(row["confidence"]),
                    _sql(row["ai_street_lights"]),
                    "'"
                    + json.dumps(
                        {
                            "summary": None,
                            "reported_behaviours": [],
                            "reported_location": None,
                        }
                    )
                    + "'::jsonb",
                ]
            )
            + ")"
        )
    lines.append(",\n".join(values) + ";")
    lines += ["", "COMMIT;", ""]

    SEED_SQL.write_text("\n".join(lines))


if __name__ == "__main__":
    main()

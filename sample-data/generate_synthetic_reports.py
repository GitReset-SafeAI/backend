"""Generate the synthetic map dataset.

SYNTHETIC DATA. Every row is invented. The clusters are placed on public
landmarks so the demo is recognisable, and they do not describe real
harassment, real reports, or real risk at those places. Regenerate with:

    python sample-data/generate_synthetic_reports.py
"""

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

SEED = 20261023
OUTPUT = Path(__file__).resolve().parent / "synthetic_map_reports.json"

# (name, latitude, longitude, report count, metre spread, dominant profile)
CLUSTERS = [
    ("Bambalapitiya station approach", 6.8905, 79.8565, 9, 110, "following_night"),
    ("Majestic City side road", 6.8938, 79.8552, 6, 90, "dark_lane"),
    ("Pettah bus stand", 6.9361, 79.8565, 13, 150, "crowd_contact"),
    ("Nugegoda junction", 6.8649, 79.8997, 5, 120, "staring_evening"),
    ("Borella junction", 6.9157, 79.8777, 4, 100, "dark_lane"),
    ("Maradana underpass", 6.9293, 79.8653, 3, 80, "following_night"),
]

PROFILES = {
    "following_night": {
        "incident": ["UNWANTED_FOLLOWING", "THREATENING_BEHAVIOUR"],
        "concern": ["PREVIOUS_SAFETY_CONCERNS_AT_LOCATION", None],
        "lights": ["poorly_lit_or_dark", "partially_lit"],
        "times": ["night", "late_night", "evening"],
        "severity": (5, 9),
    },
    "dark_lane": {
        "incident": [None, "UNCOMFORTABLE_STARING"],
        "concern": ["POOR_STREET_LIGHTING", "ISOLATED_OR_DESERTED_ROAD"],
        "lights": ["poorly_lit_or_dark"],
        "times": ["night", "late_night"],
        "severity": (2, 5),
    },
    "crowd_contact": {
        "incident": ["UNWANTED_PHYSICAL_CONTACT", "VERBAL_SEXUAL_HARASSMENT"],
        "concern": ["UNSAFE_BUS_STOP", None],
        "lights": ["day_light", "well_lit"],
        "times": ["morning", "afternoon", "evening"],
        "severity": (4, 8),
    },
    "staring_evening": {
        "incident": ["UNCOMFORTABLE_STARING", "VERBAL_SEXUAL_HARASSMENT"],
        "concern": [None, "FEELING_UNSAFE_WITHOUT_CONFIRMED_INCIDENT"],
        "lights": ["partially_lit", "well_lit"],
        "times": ["evening", "night"],
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
        rows.append(
            {
                "id": f"synthetic-{len(rows) + 1:03d}",
                "cluster": cluster,
                "occurred_at": (
                    now - timedelta(days=age_days, hours=rng.randint(0, 23))
                ).isoformat(),
                "latitude": round(latitude, 6),
                "longitude": round(longitude, 6),
                "incident_category": rng.choice(spec["incident"]),
                "safety_concern_category": rng.choice(spec["concern"]),
                "street_lights": rng.choice(spec["lights"]),
                "reported_time": rng.choice(spec["times"]),
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


if __name__ == "__main__":
    main()

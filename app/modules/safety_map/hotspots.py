"""Cluster eligible incidents into publishable areas.

DBSCAN on the haversine metric, so `eps` is a real distance on the ground
rather than a degree approximation that stretches with latitude.

Nothing here publishes a report. A cluster is published only when it clears the
aggregation floor, and its centre is rounded so the shape of the underlying
points cannot be recovered.
"""

from collections import Counter
from dataclasses import dataclass

import numpy as np
from sklearn.cluster import DBSCAN

from app.modules.safety_map.repository import EligibleIncident
from app.modules.safety_map.schemas import (
    MAX_SEVERITY,
    MIN_HOTSPOT_RADIUS_M,
    MIN_REPORTS_PER_HOTSPOT,
    POOR_LIGHTING_SHARE,
    POORLY_LIT,
    EvidenceLevel,
)

EARTH_RADIUS_M = 6_371_000.0

# Walking scale: two reports more than ~150 m apart are different places.
CLUSTER_EPS_M = 150.0

# ~110 m of latitude. Published centres are rounded to this so a centre cannot
# be traced back to the reports behind it.
CENTRE_DECIMALS = 3

# Reports stop contributing meaningfully after about a month.
RECENCY_HALF_LIFE_DAYS = 30.0


@dataclass(frozen=True, slots=True)
class Cluster:
    """An aggregated area, ready to publish."""

    id: str
    latitude: float
    longitude: float
    radius_m: float
    report_count: int
    intensity: float
    incident_categories: list[str]
    safety_concerns: list[str]
    peak_time_period: str | None
    most_recent_report_days_ago: int
    mean_confidence: float
    evidence_level: EvidenceLevel


def _weight(incident: EligibleIncident) -> float:
    """Severity, confidence and recency, each on its own 0-1 scale.

    Confidence weights the report rather than excluding it: a genuine report
    the AI read poorly is discounted, not deleted.
    """
    severity = (incident.severity if incident.severity is not None else 5) / MAX_SEVERITY
    confidence = incident.confidence if incident.confidence is not None else 0.5
    recency = 0.5 ** (incident.age_days / RECENCY_HALF_LIFE_DAYS)
    return max(0.05, severity) * max(0.05, confidence) * recency


def _evidence_level(report_count: int, mean_confidence: float) -> EvidenceLevel:
    if report_count < MIN_REPORTS_PER_HOTSPOT:
        return "insufficient"
    if report_count >= 6 and mean_confidence >= 0.6:
        return "moderate"
    return "limited"


def _radius_m(members: list[EligibleIncident], centre: tuple[float, float]) -> float:
    """Distance to the furthest member, never below the publication floor."""
    distances = [
        _haversine_m(centre, (incident.latitude, incident.longitude)) for incident in members
    ]
    return max(MIN_HOTSPOT_RADIUS_M, round(max(distances) + 30))


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = np.radians([a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return float(2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(h)))


def _top_values(values: list[str | None], limit: int = 3) -> list[str]:
    """Most common non-empty values. UNCLEAR says nothing and is dropped."""
    counted = Counter(value for value in values if value and value != "UNCLEAR")
    return [value for value, _ in counted.most_common(limit)]


def _lighting_concern(members: list[EligibleIncident]) -> bool:
    """Only poorly_lit_or_dark counts; not_sure and day_light say nothing."""
    poorly_lit = sum(1 for incident in members if incident.street_lights == POORLY_LIT)
    return poorly_lit / len(members) >= POOR_LIGHTING_SHARE


def _peak_period(members: list[EligibleIncident]) -> str | None:
    periods = [
        incident.reported_time for incident in members if incident.reported_time != "not_sure"
    ]
    if not periods:
        return None
    return Counter(periods).most_common(1)[0][0]


def find_hotspots(incidents: list[EligibleIncident]) -> tuple[list[Cluster], int]:
    """Return publishable clusters and the number withheld.

    Withheld clusters are counted so the map can say that areas were held back,
    instead of showing an empty space that reads as safety.
    """
    if len(incidents) < MIN_REPORTS_PER_HOTSPOT:
        return [], 0

    coordinates = np.radians([[incident.latitude, incident.longitude] for incident in incidents])
    labels = (
        DBSCAN(
            eps=CLUSTER_EPS_M / EARTH_RADIUS_M,
            # DBSCAN's own floor is the publication floor, so an area too small to
            # publish is never formed in the first place.
            min_samples=MIN_REPORTS_PER_HOTSPOT,
            metric="haversine",
        )
        .fit(coordinates)
        .labels_
    )

    grouped: dict[int, list[EligibleIncident]] = {}
    for label, incident in zip(labels, incidents, strict=True):
        if label == -1:
            continue
        grouped.setdefault(int(label), []).append(incident)

    clusters: list[Cluster] = []
    suppressed = 0
    weights = [_weight(incident) for incident in incidents]
    busiest = max(
        (sum(_weight(member) for member in members) for members in grouped.values()),
        default=max(weights, default=1.0),
    )

    for label, members in sorted(grouped.items()):
        if len(members) < MIN_REPORTS_PER_HOTSPOT:
            suppressed += 1
            continue

        centre = (
            float(np.mean([member.latitude for member in members])),
            float(np.mean([member.longitude for member in members])),
        )
        total_weight = sum(_weight(member) for member in members)
        confidences = [member.confidence for member in members if member.confidence is not None]
        mean_confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.5

        concerns = _top_values([member.safety_concern_category for member in members])
        if _lighting_concern(members) and "POOR_STREET_LIGHTING" not in concerns:
            concerns.insert(0, "POOR_STREET_LIGHTING")

        clusters.append(
            Cluster(
                id=f"hotspot-{label + 1}",
                latitude=round(centre[0], CENTRE_DECIMALS),
                longitude=round(centre[1], CENTRE_DECIMALS),
                radius_m=_radius_m(members, centre),
                report_count=len(members),
                intensity=round(min(1.0, total_weight / busiest), 2) if busiest else 0.0,
                incident_categories=_top_values([member.incident_category for member in members]),
                safety_concerns=concerns[:3],
                peak_time_period=_peak_period(members),
                most_recent_report_days_ago=min(member.age_days for member in members),
                mean_confidence=mean_confidence,
                evidence_level=_evidence_level(len(members), mean_confidence),
            )
        )

    # Points DBSCAN rejected are not a withheld area, but a run that produced
    # nothing from many reports is worth reporting as withheld evidence.
    if not clusters and len(incidents) >= MIN_REPORTS_PER_HOTSPOT:
        suppressed += 1

    return clusters, suppressed


def overall_evidence(clusters: list[Cluster]) -> EvidenceLevel:
    if not clusters:
        return "insufficient"
    if any(cluster.evidence_level == "moderate" for cluster in clusters):
        return "moderate"
    return "limited"

"""Deterministic, rule-based incident priority engine for MetroOps Response.

This is NOT a trained AI model. It is a transparent, hand-tuned rule set that
maps an incident to a 0-100 priority score, a severity band, the factors that
produced the score, and decision-support actions. Every number lives in the
TUNING section below so it can be adjusted after expert review.

Recommended actions are decision support only. They are not official Gujarat
Metro operating procedures, and the engine never controls trains, doors,
escalators or any safety-critical equipment.
"""

from __future__ import annotations

# --- TUNING: all weights and thresholds live here (adjust after expert review) ---

# Baseline risk per category (0-40). Unknown categories fall back to OTHER.
CATEGORY_WEIGHTS = {
    "Passenger Safety": 40,
    "Infrastructure": 35,
    "Equipment Failure": 25,
    "Service Disruption": 25,
    "Crowding": 20,
    "Other": 10,
}
_DEFAULT_CATEGORY_WEIGHT = CATEGORY_WEIGHTS["Other"]

# A reported immediate safety concern is a significant, fixed escalation.
SAFETY_CONCERN_POINTS = 40

# Passenger impact is capped (max 20) so a large crowd alone cannot outrank a
# genuine safety emergency, which carries both category and safety points.
PASSENGER_TIERS = [
    (500, 20),  # more than 500
    (200, 15),  # 201-500
    (50, 10),   # 51-200
    (0, 5),     # 1-50
]  # 0 passengers contributes 0

SCORE_MIN, SCORE_MAX = 0, 100

# Severity bands by score. Checked high-to-low; first match wins.
SEVERITY_THRESHOLDS = [
    (80, "CRITICAL"),
    (60, "HIGH"),
    (35, "MEDIUM"),
    (0, "LOW"),
]

# A reported immediate safety concern never ranks below this band, so a large
# passenger count on a non-safety incident can never outrank a safety emergency.
SAFETY_FLOOR_SEVERITY = "HIGH"

# --- end TUNING ---

VALID_CATEGORIES = tuple(CATEGORY_WEIGHTS)

SEVERITY_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

BASIS = "Deterministic rule-based baseline, not a trained AI prediction."


def _passenger_points(passengers: int) -> int:
    for threshold, points in PASSENGER_TIERS:
        if passengers > threshold:
            return points
    return 0


def _coerce_passengers(value) -> int:
    """Defensive: clamp to a non-negative int; unparseable input scores 0."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, n)


def _severity(score: int) -> str:
    for threshold, label in SEVERITY_THRESHOLDS:
        if score >= threshold:
            return label
    return "LOW"


def _recommended_actions(severity: str, category: str, safety: bool, passengers: int) -> list[str]:
    actions: list[str] = []
    if safety:
        actions.append("Immediate safety concern reported: dispatch station response staff to assess on site.")
    if severity == "CRITICAL":
        actions.append("Escalate to the operations control room now for coordinated response.")
    elif severity == "HIGH":
        actions.append("Notify the duty operations manager and monitor closely.")

    category_action = {
        "Equipment Failure": "Request a maintenance/technical team assessment of the affected equipment.",
        "Infrastructure": "Request an engineering inspection of the affected infrastructure.",
        "Crowding": "Consider crowd-management measures: staff marshalling and passenger-flow control.",
        "Service Disruption": "Prepare passenger communications about the service status.",
        "Passenger Safety": "Ensure first-aid / medical readiness in case of injuries.",
        "Other": "Review the description and route the incident to the appropriate team.",
    }.get(category, "Review the description and route the incident to the appropriate team.")
    actions.append(category_action)

    if passengers > 200:
        actions.append("Account for significant passenger volume when assigning resources.")

    actions.append("Decision support only: confirm against official Gujarat Metro procedures before acting.")
    return actions


def analyze(incident: dict) -> dict:
    """Score an incident and explain the result.

    Reads category, passengers_affected and safety_concern. The original
    category and description are preserved on the incident record by the caller;
    this function only reads inputs and returns a fresh analysis dict.
    Malformed inputs are coerced to safe defaults rather than raising.
    """
    raw_category = incident.get("category")
    category = raw_category if raw_category in CATEGORY_WEIGHTS else "Other"
    passengers = _coerce_passengers(incident.get("passengers_affected"))
    safety = bool(incident.get("safety_concern"))

    factors: list[dict] = []
    category_points = CATEGORY_WEIGHTS.get(category, _DEFAULT_CATEGORY_WEIGHT)
    label = f"Category: {raw_category}" if category == raw_category else f"Category: {raw_category!r} (unrecognised, scored as Other)"
    factors.append({"factor": label, "points": category_points})

    pax_points = _passenger_points(passengers)
    if pax_points:
        factors.append({"factor": f"Passengers affected: {passengers}", "points": pax_points})

    if safety:
        factors.append({"factor": "Immediate safety concern reported", "points": SAFETY_CONCERN_POINTS})

    raw_score = category_points + pax_points + (SAFETY_CONCERN_POINTS if safety else 0)
    score = max(SCORE_MIN, min(SCORE_MAX, raw_score))
    severity = _severity(score)
    if safety and SEVERITY_ORDER.index(severity) < SEVERITY_ORDER.index(SAFETY_FLOOR_SEVERITY):
        severity = SAFETY_FLOOR_SEVERITY

    summary = (
        f"{severity} priority (score {score}/100): "
        f"{category.lower()} incident"
        + (", immediate safety concern" if safety else "")
        + (f", ~{passengers} passengers affected" if passengers else "")
        + "."
    )

    return {
        "score": score,
        "severity": severity,
        "summary": summary,
        "factors": factors,
        "recommended_actions": _recommended_actions(severity, category, safety, passengers),
        "basis": BASIS,
    }

"""Automated tests for the MetroOps incident engine and API integration.

Run: .venv/Scripts/python.exe -m unittest -v
Uses only the standard library (unittest). The API is exercised by calling the
route functions directly, since httpx (required by Starlette's TestClient) is
not installed and Phase 3 adds no dependencies.
"""

import unittest

from pydantic import ValidationError
from fastapi import HTTPException

import incident_engine as engine
import main
from main import IncidentIn, Category, StatusUpdate, IncidentStatus


def make(category="Crowding", passengers=0, safety=False, description="desc", station="Stn"):
    return {
        "category": category,
        "passengers_affected": passengers,
        "safety_concern": safety,
        "description": description,
        "station": station,
    }


class EngineScoringTests(unittest.TestCase):
    def test_every_category_valid(self):
        for cat in engine.VALID_CATEGORIES:
            a = engine.analyze(make(category=cat))
            self.assertIn(a["severity"], {"LOW", "MEDIUM", "HIGH", "CRITICAL"})
            self.assertTrue(engine.SCORE_MIN <= a["score"] <= engine.SCORE_MAX)
            self.assertTrue(a["factors"])
            self.assertTrue(a["recommended_actions"])

    def test_zero_passengers_contribute_nothing(self):
        a = engine.analyze(make(category="Other", passengers=0))
        self.assertEqual(a["score"], engine.CATEGORY_WEIGHTS["Other"])
        self.assertFalse(any("Passengers" in f["factor"] for f in a["factors"]))

    def test_large_passenger_count_capped(self):
        a = engine.analyze(make(category="Crowding", passengers=10000))
        pax = next(f for f in a["factors"] if "Passengers" in f["factor"])
        self.assertEqual(pax["points"], 20)

    def test_safety_concern_escalates(self):
        base = engine.analyze(make(category="Crowding", passengers=0, safety=False))
        esc = engine.analyze(make(category="Crowding", passengers=0, safety=True))
        self.assertEqual(esc["score"] - base["score"], engine.SAFETY_CONCERN_POINTS)
        self.assertTrue(any("safety concern" in f["factor"].lower() for f in esc["factors"]))

    def test_passengers_do_not_override_safety_emergency(self):
        crowd = engine.analyze(make(category="Crowding", passengers=10000, safety=False))
        emergency = engine.analyze(make(category="Passenger Safety", passengers=0, safety=True))
        self.assertEqual(emergency["severity"], "CRITICAL")
        self.assertGreater(emergency["score"], crowd["score"])
        self.assertNotEqual(crowd["severity"], "CRITICAL")

    def test_safety_concern_floored_above_any_non_safety(self):
        # Low-category safety emergency (score 50 -> would be MEDIUM) must still
        # rank at least HIGH, above a large non-safety crowd (score 55, MEDIUM).
        safety = engine.analyze(make(category="Other", passengers=0, safety=True))
        non_safety = engine.analyze(make(category="Infrastructure", passengers=600, safety=False))
        self.assertEqual(safety["severity"], "HIGH")
        self.assertEqual(non_safety["severity"], "MEDIUM")
        self.assertGreater(engine.SEVERITY_ORDER.index(safety["severity"]),
                           engine.SEVERITY_ORDER.index(non_safety["severity"]))

    def test_severity_boundaries(self):
        # Exact threshold values map to the higher band.
        cases = {0: "LOW", 34: "LOW", 35: "MEDIUM", 59: "MEDIUM",
                 60: "HIGH", 79: "HIGH", 80: "CRITICAL", 100: "CRITICAL"}
        for score, expected in cases.items():
            self.assertEqual(engine._severity(score), expected, score)

    def test_boundary_via_full_analysis(self):
        self.assertEqual(engine.analyze(make("Equipment Failure", 0))["severity"], "LOW")       # 25
        self.assertEqual(engine.analyze(make("Equipment Failure", 100))["severity"], "MEDIUM")  # 35
        self.assertEqual(engine.analyze(make("Passenger Safety", 10000))["severity"], "HIGH")   # 60
        self.assertEqual(engine.analyze(make("Passenger Safety", 0, True))["severity"], "CRITICAL")  # 80

    def test_max_input_hits_cap(self):
        a = engine.analyze(make("Passenger Safety", 10000, True))
        self.assertEqual(a["score"], 100)

    def test_score_range_over_sweep(self):
        for cat in list(engine.VALID_CATEGORIES) + ["Nonsense"]:
            for pax in [0, 1, 50, 51, 200, 201, 500, 501, 10 ** 9]:
                for safety in (True, False):
                    s = engine.analyze(make(cat, pax, safety))["score"]
                    self.assertTrue(0 <= s <= 100)

    def test_deterministic(self):
        inc = make("Service Disruption", 300, True)
        self.assertEqual(engine.analyze(inc), engine.analyze(inc))

    def test_recommended_actions_rules(self):
        a = engine.analyze(make("Passenger Safety", 0, True))
        self.assertTrue(any("dispatch station response" in x.lower() for x in a["recommended_actions"]))
        self.assertTrue(any("control room" in x.lower() for x in a["recommended_actions"]))
        self.assertTrue(any("decision support" in x.lower() for x in a["recommended_actions"]))

    def test_basis_not_ai_claim(self):
        self.assertIn("not a trained AI", engine.analyze(make())["basis"])


# PLACEHOLDER_MALFORMED


class EngineMalformedInputTests(unittest.TestCase):
    def test_unknown_category_scored_as_other(self):
        a = engine.analyze(make(category="Fire"))
        self.assertEqual(a["score"], engine.CATEGORY_WEIGHTS["Other"])
        self.assertEqual(a["severity"], "LOW")

    def test_negative_passengers_coerced_to_zero(self):
        a = engine.analyze(make(category="Other", passengers=-500))
        self.assertEqual(a["score"], engine.CATEGORY_WEIGHTS["Other"])

    def test_passengers_as_string(self):
        self.assertEqual(engine.analyze(make("Other", "250"))["score"],
                         engine.CATEGORY_WEIGHTS["Other"] + 15)

    def test_unparseable_passengers(self):
        self.assertEqual(engine.analyze(make("Other", "lots"))["score"],
                         engine.CATEGORY_WEIGHTS["Other"])

    def test_empty_dict(self):
        a = engine.analyze({})
        self.assertEqual(a["severity"], "LOW")
        self.assertEqual(a["score"], engine.CATEGORY_WEIGHTS["Other"])


class ApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        main._incidents.clear()

    def test_health_unchanged(self):
        self.assertEqual(main.health(), {"status": "ok"})

    def test_create_attaches_analysis_and_preserves_fields(self):
        rec = main.create_incident(IncidentIn(
            station="Central", category=Category.passenger_safety,
            description="person on tracks", passengers_affected=10, safety_concern=True))
        self.assertTrue(rec["id"].startswith("INC-"))
        self.assertEqual(rec["status"], "Active")
        self.assertEqual(rec["category"], "Passenger Safety")   # preserved
        self.assertEqual(rec["description"], "person on tracks")  # preserved
        self.assertIn("created_at", rec)
        self.assertEqual(rec["analysis"]["severity"], "CRITICAL")
        self.assertTrue(0 <= rec["analysis"]["score"] <= 100)

    def test_list_returns_stored_with_analysis(self):
        main.create_incident(IncidentIn(
            station="A", category=Category.crowding, description="busy",
            passengers_affected=5, safety_concern=False))
        incidents = main.list_incidents()
        self.assertEqual(len(incidents), 1)
        self.assertIn("analysis", incidents[0])

    def test_response_has_phase2_fields(self):
        rec = main.create_incident(IncidentIn(
            station="A", category=Category.other, description="x",
            passengers_affected=0, safety_concern=False))
        for field in ("id", "created_at", "status", "station", "category",
                      "description", "passengers_affected", "safety_concern"):
            self.assertIn(field, rec)


class ValidationTests(unittest.TestCase):
    def test_missing_description_rejected(self):
        with self.assertRaises(ValidationError):
            IncidentIn(station="A", category=Category.other,
                       passengers_affected=1, safety_concern=False)

    def test_negative_passengers_rejected(self):
        with self.assertRaises(ValidationError):
            IncidentIn(station="A", category=Category.other, description="x",
                       passengers_affected=-1, safety_concern=False)

    def test_bad_category_rejected(self):
        with self.assertRaises(ValidationError):
            IncidentIn(station="A", category="Fire", description="x",
                       passengers_affected=1, safety_concern=False)

    def test_whitespace_station_rejected(self):
        with self.assertRaises(ValidationError):
            IncidentIn(station="   ", category=Category.other, description="x",
                       passengers_affected=1, safety_concern=False)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        main._incidents.clear()
        self.rec = main.create_incident(IncidentIn(
            station="A", category=Category.crowding, description="busy",
            passengers_affected=10, safety_concern=False))
        self.id = self.rec["id"]

    def _patch(self, status):
        return main.update_status(self.id, StatusUpdate(status=status))

    def test_starts_active_with_seeded_history(self):
        self.assertEqual(self.rec["status"], "Active")
        self.assertEqual(len(self.rec["history"]), 1)
        self.assertEqual(self.rec["history"][0]["from"], None)
        self.assertEqual(self.rec["history"][0]["to"], "Active")
        self.assertEqual(self.rec["history"][0]["at"], self.rec["created_at"])

    def test_full_allowed_lifecycle(self):
        for nxt in ("Acknowledged", "In Progress", "Resolved"):
            out = self._patch(nxt)
            self.assertEqual(out["status"], nxt)
        self.assertEqual([h["to"] for h in self.rec["history"]],
                         ["Active", "Acknowledged", "In Progress", "Resolved"])

    def test_history_records_from_to_and_time(self):
        self._patch("Acknowledged")
        last = self.rec["history"][-1]
        self.assertEqual(last["from"], "Active")
        self.assertEqual(last["to"], "Acknowledged")
        self.assertTrue(last["at"])

    def test_reject_skip_transition(self):
        with self.assertRaises(HTTPException) as ctx:
            self._patch("In Progress")  # Active -> In Progress skips Acknowledged
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.rec["status"], "Active")  # unchanged

    def test_reject_direct_resolve(self):
        with self.assertRaises(HTTPException) as ctx:
            self._patch("Resolved")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_reject_backward_transition(self):
        self._patch("Acknowledged")
        with self.assertRaises(HTTPException) as ctx:
            self._patch("Active")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_reject_change_after_resolved(self):
        for nxt in ("Acknowledged", "In Progress", "Resolved"):
            self._patch(nxt)
        with self.assertRaises(HTTPException) as ctx:
            self._patch("Acknowledged")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_nonexistent_incident_404(self):
        with self.assertRaises(HTTPException) as ctx:
            main.update_status("INC-9999", StatusUpdate(status=IncidentStatus.acknowledged))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_invalid_status_value_rejected(self):
        with self.assertRaises(ValidationError):
            StatusUpdate(status="Nonsense")

    def test_scoring_unchanged_by_workflow(self):
        before = dict(self.rec["analysis"])
        self._patch("Acknowledged")
        self.assertEqual(self.rec["analysis"], before)


if __name__ == "__main__":
    unittest.main()

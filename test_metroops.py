"""Automated tests for the MetroOps incident engine and API integration.

Run: .venv/Scripts/python.exe -m unittest -v
Uses only the standard library (unittest). The API is exercised by calling the
route functions directly, since httpx (required by Starlette's TestClient) is
not installed and Phase 3 adds no dependencies.

Every test runs against a TEMPORARY SQLite database, never the real data/ file:
METROOPS_DB is pointed at a throwaway path before main is imported, and the
persistence tests build their own IncidentRepo on temp paths.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest

os.environ["METROOPS_DB"] = os.path.join(tempfile.mkdtemp(), "import.db")

from pydantic import ValidationError
from fastapi import HTTPException

import incident_engine as engine
import main
from incident_repo import IncidentRepo
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
        self._tmp = tempfile.mkdtemp()
        main.repo = IncidentRepo(os.path.join(self._tmp, "test.db"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

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
        self._tmp = tempfile.mkdtemp()
        main.repo = IncidentRepo(os.path.join(self._tmp, "test.db"))
        self.rec = main.create_incident(IncidentIn(
            station="A", category=Category.crowding, description="busy",
            passengers_affected=10, safety_concern=False))
        self.id = self.rec["id"]

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

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
        self.assertEqual([h["to"] for h in main.repo.get(self.id)["history"]],
                         ["Active", "Acknowledged", "In Progress", "Resolved"])

    def test_history_records_from_to_and_time(self):
        self._patch("Acknowledged")
        last = main.repo.get(self.id)["history"][-1]
        self.assertEqual(last["from"], "Active")
        self.assertEqual(last["to"], "Acknowledged")
        self.assertTrue(last["at"])

    def test_reject_skip_transition(self):
        with self.assertRaises(HTTPException) as ctx:
            self._patch("In Progress")  # Active -> In Progress skips Acknowledged
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(main.repo.get(self.id)["status"], "Active")  # unchanged

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
        self.assertEqual(main.repo.get(self.id)["analysis"], before)


class PersistenceTests(unittest.TestCase):
    """SQLite-specific behaviour: init, round-trips, transactions, restart."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.path = os.path.join(self._tmp, "persist.db")
        self.repo = IncidentRepo(self.path)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _fields(self, **over):
        base = dict(station="Stn", category="Crowding", description="busy",
                    passengers_affected=10, safety_concern=False)
        base.update(over)
        return base

    def test_init_creates_schema_file(self):
        self.assertTrue(os.path.exists(self.path))
        with sqlite3.connect(self.path) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("incidents", tables)
        self.assertIn("status_history", tables)

    def test_create_and_retrieve_round_trip(self):
        rec = self.repo.create(self._fields(safety_concern=True), {"score": 42, "severity": "MEDIUM"})
        got = self.repo.get(rec["id"])
        self.assertEqual(got["id"], rec["id"])
        self.assertEqual(got["station"], "Stn")
        self.assertEqual(got["safety_concern"], True)          # stored 1 -> bool
        self.assertEqual(got["analysis"], {"score": 42, "severity": "MEDIUM"})
        self.assertEqual(got["status"], "Active")
        self.assertEqual(got["history"], [{"from": None, "to": "Active", "at": rec["created_at"]}])

    def test_status_and_history_persist(self):
        rec = self.repo.create(self._fields(), {"score": 1})
        self.assertTrue(self.repo.apply_transition(rec["id"], "Active", "Acknowledged"))
        got = self.repo.get(rec["id"])
        self.assertEqual(got["status"], "Acknowledged")
        self.assertEqual([h["to"] for h in got["history"]], ["Active", "Acknowledged"])
        self.assertEqual(got["history"][-1]["from"], "Active")

    def test_transition_compare_and_swap_guards_stale_state(self):
        rec = self.repo.create(self._fields(), {"score": 1})
        # expected_current no longer matches -> refused, nothing written.
        self.assertFalse(self.repo.apply_transition(rec["id"], "Resolved", "Acknowledged"))
        got = self.repo.get(rec["id"])
        self.assertEqual(got["status"], "Active")
        self.assertEqual(len(got["history"]), 1)

    def test_transaction_rolls_back_on_failed_history_insert(self):
        rec = self.repo.create(self._fields(), {"score": 1})
        # An invalid target status trips the CHECK constraint during the UPDATE,
        # so the whole transaction must roll back: status and history unchanged.
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.apply_transition(rec["id"], "Active", "Bogus")
        got = self.repo.get(rec["id"])
        self.assertEqual(got["status"], "Active")
        self.assertEqual(len(got["history"]), 1)

    def test_data_survives_reconnection(self):
        rec = self.repo.create(self._fields(), {"score": 7})
        self.repo.apply_transition(rec["id"], "Active", "Acknowledged")
        # A fresh repo on the same file simulates an application restart.
        reopened = IncidentRepo(self.path)
        got = reopened.get(rec["id"])
        self.assertEqual(got["status"], "Acknowledged")
        self.assertEqual(got["analysis"], {"score": 7})
        self.assertEqual([h["to"] for h in got["history"]], ["Active", "Acknowledged"])
        self.assertEqual(len(reopened.list_all()), 1)

    def test_ids_are_unique_and_sequential(self):
        a = self.repo.create(self._fields(), {"score": 1})
        b = self.repo.create(self._fields(), {"score": 1})
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual([r["id"] for r in self.repo.list_all()], [a["id"], b["id"]])


class FailureResilienceTests(unittest.TestCase):
    """Phase 6: the database layer must fail loudly, never report a false success."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.path = os.path.join(self._tmp, "resl.db")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _fields(self):
        return dict(station="A", category="Crowding", description="d",
                    passengers_affected=1, safety_concern=False)

    def test_invalid_db_path_raises_not_silent(self):
        # A path whose parent is an existing file cannot be opened as a database.
        blocker = os.path.join(self._tmp, "afile")
        with open(blocker, "w") as f:
            f.write("x")
        with self.assertRaises((sqlite3.OperationalError, OSError)):
            IncidentRepo(os.path.join(blocker, "nested.db"))

    def test_write_failure_surfaces_and_reports_no_success(self):
        repo = IncidentRepo(self.path)
        repo.create(self._fields(), {"score": 1})
        # Database becomes unusable (table dropped underneath it): writes must raise.
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TABLE incidents")
        with self.assertRaises(sqlite3.OperationalError):
            repo.create(self._fields(), {"score": 1})
        with self.assertRaises(sqlite3.OperationalError):
            repo.list_all()

    def test_html_like_description_stored_verbatim(self):
        # Backend stores untrusted text as-is; the frontend renders it with
        # textContent, so storage must neither mangle nor interpret it.
        repo = IncidentRepo(self.path)
        payload = '<img src=x onerror="alert(1)">'
        rec = repo.create({**self._fields(), "description": payload}, {"score": 1})
        self.assertEqual(repo.get(rec["id"])["description"], payload)


class ApiResilienceTests(unittest.TestCase):
    """Phase 6: API/workflow edge cases on top of the persisted store."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        main.repo = IncidentRepo(os.path.join(self._tmp, "api.db"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _create(self, **over):
        base = dict(station="A", category=Category.crowding, description="d",
                    passengers_affected=1, safety_concern=False)
        return main.create_incident(IncidentIn(**{**base, **over}))

    def test_repeated_submissions_are_distinct_and_persist(self):
        a, b = self._create(), self._create()
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual([i["id"] for i in main.list_incidents()], [a["id"], b["id"]])

    def test_stale_or_concurrent_update_returns_409(self):
        rec = self._create()
        # Force the compare-and-swap to miss, as a lost race would.
        orig = main.repo.apply_transition
        main.repo.apply_transition = lambda *a, **k: False
        try:
            with self.assertRaises(HTTPException) as ctx:
                main.update_status(rec["id"], StatusUpdate(status=IncidentStatus.acknowledged))
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            main.repo.apply_transition = orig

    def test_persisted_analysis_matches_engine(self):
        inc = IncidentIn(station="Thaltej", category=Category.passenger_safety,
                         description="x", passengers_affected=250, safety_concern=True)
        rec = main.create_incident(inc)
        # Scoring must survive the JSON round-trip through SQLite unchanged.
        self.assertEqual(rec["analysis"], engine.analyze(inc.model_dump(mode="json")))

    def test_illegal_transition_leaves_record_untouched(self):
        rec = self._create()
        with self.assertRaises(HTTPException):
            main.update_status(rec["id"], StatusUpdate(status=IncidentStatus.resolved))
        after = main.repo.get(rec["id"])
        self.assertEqual(after["status"], "Active")
        self.assertEqual(len(after["history"]), 1)


if __name__ == "__main__":
    unittest.main()

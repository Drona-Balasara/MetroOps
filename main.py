from datetime import datetime, timezone
from enum import Enum
from itertools import count
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

import incident_engine

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="MetroOps Response")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# In-memory store for this phase; replaced by a database in a later phase.
_incidents: list[dict] = []
_id_counter = count(1)


class Category(str, Enum):
    equipment_failure = "Equipment Failure"
    passenger_safety = "Passenger Safety"
    crowding = "Crowding"
    service_disruption = "Service Disruption"
    infrastructure = "Infrastructure"
    other = "Other"


class IncidentStatus(str, Enum):
    active = "Active"
    acknowledged = "Acknowledged"
    in_progress = "In Progress"
    resolved = "Resolved"


# Operator workflow lifecycle: each status may only advance to the next step.
# Any transition not listed here (backward moves, skips, changes on a resolved
# incident) is rejected. All actions are advisory and operator-controlled.
ALLOWED_TRANSITIONS = {
    "Active": {"Acknowledged"},
    "Acknowledged": {"In Progress"},
    "In Progress": {"Resolved"},
    "Resolved": set(),
}


class IncidentIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    station: str = Field(min_length=1, max_length=120)
    category: Category
    description: str = Field(min_length=1, max_length=2000)
    passengers_affected: int = Field(ge=0)
    safety_concern: bool


class StatusUpdate(BaseModel):
    status: IncidentStatus


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/incidents")
def list_incidents() -> list[dict]:
    return _incidents


@app.post("/incidents", status_code=201)
def create_incident(incident: IncidentIn) -> dict:
    record = {
        "id": f"INC-{next(_id_counter):04d}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "Active",
        **incident.model_dump(mode="json"),
    }
    record["analysis"] = incident_engine.analyze(record)
    record["history"] = [{"from": None, "to": record["status"], "at": record["created_at"]}]
    _incidents.append(record)
    return record


@app.patch("/incidents/{incident_id}/status")
def update_status(incident_id: str, update: StatusUpdate) -> dict:
    record = next((r for r in _incidents if r["id"] == incident_id), None)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

    current, new = record["status"], update.status.value
    if new not in ALLOWED_TRANSITIONS.get(current, set()):
        raise HTTPException(status_code=409, detail=f"Cannot change status from '{current}' to '{new}'")

    record["status"] = new
    record["history"].append({"from": current, "to": new, "at": datetime.now(timezone.utc).isoformat()})
    return record

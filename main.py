from enum import Enum
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

import incident_engine
from incident_repo import IncidentRepo

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="MetroOps Response")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Single source of truth: a local SQLite file (path from METROOPS_DB, else data/).
# Schema is created here at startup. Tests swap this out for a temporary database.
repo = IncidentRepo()


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
    return repo.list_all()


@app.post("/incidents", status_code=201)
def create_incident(incident: IncidentIn) -> dict:
    fields = incident.model_dump(mode="json")
    analysis = incident_engine.analyze(fields)
    return repo.create(fields, analysis)


@app.patch("/incidents/{incident_id}/status")
def update_status(incident_id: str, update: StatusUpdate) -> dict:
    record = repo.get(incident_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

    current, new = record["status"], update.status.value
    if new not in ALLOWED_TRANSITIONS.get(current, set()):
        raise HTTPException(status_code=409, detail=f"Cannot change status from '{current}' to '{new}'")

    if not repo.apply_transition(incident_id, current, new):
        raise HTTPException(status_code=409, detail="Status changed concurrently; please retry")
    return repo.get(incident_id)

from datetime import datetime, timezone
from enum import Enum
from itertools import count
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

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


class IncidentIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    station: str = Field(min_length=1, max_length=120)
    category: Category
    description: str = Field(min_length=1, max_length=2000)
    passengers_affected: int = Field(ge=0)
    safety_concern: bool


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
    _incidents.append(record)
    return record

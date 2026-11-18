import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent import run_break
from app.db import SessionLocal, init_db
from app.models import Run
from app.repo import run_to_dict, save_run

app = FastAPI(title="Breaker")
STATIC = Path(__file__).parent / "static"
LIVE: dict[str, dict] = {}   # in-memory status for runs still executing


class BreakRequest(BaseModel):
    problem: str = Field(min_length=10)
    solution: str = Field(min_length=1)
    language: str = Field(default="python", pattern="^(python|cpp)$")
    strategy: str = Field(default="generic", pattern="^(generic|boundary)$")


@app.on_event("startup")
def _startup():
    init_db()


def _worker(job: str, req: BreakRequest):
    try:
        r = run_break(req.problem, req.solution, req.language, strategy=req.strategy, n_tests=20)
        with SessionLocal() as db:
            run = save_run(db, req.problem, req.solution, req.language, req.strategy, r)
            LIVE[job] = {"status": "done", "run_id": run.id}
    except Exception as e:
        LIVE[job] = {"status": "error", "error": f"{type(e).__name__}: {e}"}


@app.post("/break")
def start_break(req: BreakRequest, bg: BackgroundTasks):
    job = uuid.uuid4().hex
    LIVE[job] = {"status": "running"}
    bg.add_task(_worker, job, req)
    return {"job": job}


@app.get("/jobs/{job}")
def job_status(job: str):
    st = LIVE.get(job)
    if st is None:
        raise HTTPException(404, "unknown job")
    return st


@app.get("/runs/{run_id}")
def get_run(run_id: int):
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return run_to_dict(run, detail=True)


@app.get("/runs")
def list_runs(limit: int = 20):
    with SessionLocal() as db:
        rows = db.scalars(select(Run).order_by(Run.created_at.desc()).limit(min(limit, 100))).all()
        return [run_to_dict(r) for r in rows]


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
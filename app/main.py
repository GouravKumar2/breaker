import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import llm
from app.agent import run_break
from app.config import settings
from app.db import SessionLocal, init_db
from app.models import Run
from app.repo import run_to_dict, save_run

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if settings.llm_cache:
        llm.enable_cache(settings.llm_cache)
    yield


app = FastAPI(title="Breaker", lifespan=lifespan)
STATIC = Path(__file__).parent / "static"
LIVE: dict[str, dict] = {}   # in-memory status for runs still executing


class BreakRequest(BaseModel):
    problem: str = Field(min_length=10, max_length=20_000)
    solution: str = Field(min_length=1, max_length=50_000)
    language: str = Field(default="python", pattern="^(python|cpp)$")
    strategy: str = Field(default="generic", pattern="^(generic|boundary)$")


def _worker(job: str, req: BreakRequest):
    st = LIVE[job]
    try:
        r = run_break(req.problem, req.solution, req.language, strategy=req.strategy, n_tests=20,
                      on_step=lambda step: st["steps"].append(step),
                      on_phase=lambda p: st.__setitem__("current", p))
        with SessionLocal() as db:
            run = save_run(db, req.problem, req.solution, req.language, req.strategy, r)
        st.update(status="done", run_id=run.id, current="")
    except Exception as e:
        st.update(status="error", error=f"{type(e).__name__}: {e}", current="")


@app.post("/break")
def start_break(req: BreakRequest, bg: BackgroundTasks):
    job = uuid.uuid4().hex
    LIVE[job] = {"status": "running", "steps": [], "current": "Queued"}
    bg.add_task(_worker, job, req)
    return {"job": job}


@app.get("/jobs/{job}")
def job_status(job: str):
    st = LIVE.get(job)
    if st is None:
        raise HTTPException(404, "unknown job")
    return {**st, "steps": list(st.get("steps", []))}


@app.get("/examples")
def examples():
    return json.loads((STATIC / "examples.json").read_text(encoding="utf-8"))


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
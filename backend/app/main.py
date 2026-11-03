import asyncio
import csv
import hmac
import logging
import re
import shutil
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from .config import CORS_ORIGINS, DATA_DIR, MAX_UPLOAD_MB, MAX_JOBS_PER_HOUR, INTERNAL_TOKEN
from .schemas import ClarifyRequest, ColumnSelection, JobView, ReviewRequest, SourceSelection
from .store import JobStore, now_iso
from .agents.workflow import _read, deliver, discover
from .services.agent_client import call_agent

DATA_DIR.mkdir(parents=True, exist_ok=True)
STORE = JobStore(DATA_DIR)
app = FastAPI(title="Fetch Dataset Engine", version="0.1.0")
logger = logging.getLogger("fetch")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False, allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type", "Last-Event-ID"])
_job_requests = defaultdict(deque)
_rate_lock = threading.Lock()
INTERNAL_FILES = {"input.csv", "staged.csv", "engineered.csv", "dataset.csv", "dataset.json", "prepare.py"}

@app.get("/health")
def health():
    return {"status": "ok"}

def require_internal_token(token: str | None):
    if not INTERNAL_TOKEN or not token or not hmac.compare_digest(token, INTERNAL_TOKEN):
        raise HTTPException(401, "Internal service authentication failed")

def _missing_requested_fields(requirements: list[str], columns: list[str]) -> list[str]:
    tokens = [set(re.findall(r"[a-z0-9]+", str(column).lower())) for column in columns]
    synonyms = {"year": {"year", "date", "period"}, "country": {"country", "nation", "location", "entity"}, "prevalence": {"prevalence", "share", "rate", "percentage", "percent", "proportion"}}
    missing = []
    for requirement in requirements:
        wanted = set(re.findall(r"[a-z0-9]+", str(requirement).lower()))
        if not wanted:
            continue
        if "hiv" in wanted and ("prevalence" in wanted or len(wanted) == 1):
            wanted = {"hiv", "prevalence"} if "prevalence" in wanted else {"hiv"}
        def field_present(term: str) -> bool:
            choices = synonyms.get(term, {term})
            return any(bool(choices & column_tokens) for column_tokens in tokens)
        if not all(field_present(term) for term in wanted):
            missing.append(str(requirement))
    return missing

@app.api_route("/internal/jobs/{job_id}/files/{filename}", methods=["GET", "PUT"], include_in_schema=False)
async def agent_file_exchange(job_id: str, filename: str, request: Request, x_fetch_internal_token: str | None = Header(None)):
    require_internal_token(x_fetch_internal_token)
    try:
        uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(404, "Job not found") from exc
    allowed_upload = Path(filename).name == filename and filename.startswith("upload.") and Path(filename).suffix.lower() in {".csv", ".json", ".jsonl", ".ndjson", ".xlsx", ".txt"}
    if filename not in INTERNAL_FILES and not allowed_upload:
        raise HTTPException(404, "File not found")
    path = DATA_DIR / "jobs" / job_id / filename
    if request.method == "GET":
        if not path.is_file():
            raise HTTPException(404, "File not found")
        return FileResponse(path)
    body = await request.body()
    if len(body) > max(MAX_UPLOAD_MB * 4, 100) * 1024 * 1024:
        raise HTTPException(413, "Internal artifact exceeds size limit")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(body)
    return Response(status_code=204)

@app.get("/api/jobs", response_model=list[JobView])
def list_jobs():
    return STORE.list()

@app.post("/api/clarify")
def clarify_objective(request: ClarifyRequest):
    state = call_agent("otto", {"action": "clarify", "objective": request.objective, "answers": request.answers})
    return state.get("clarification", {"question": "Please add a little more detail about the dataset you need.", "ready": False, "provider": None})

@app.get("/api/jobs/{job_id}", response_model=JobView)
def get_job(job_id: str):
    job = STORE.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job

@app.post("/api/jobs/{job_id}/source", response_model=JobView)
def select_source(job_id: str, selection: SourceSelection):
    job = STORE.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "awaiting_columns":
        raise HTTPException(409, "Job is not ready for source selection")
    listings = (job.get("proposal") or {}).get("discovery_results", [])
    listing = next((item for item in listings if item.get("url") == selection.url), None)
    if not listing:
        raise HTTPException(422, "Choose a source from this job's search results")
    job["status"] = "running"
    job["updated_at"] = now_iso()
    STORE.save(job)
    try:
        base_state = {"objective": job["objective"], "job_dir": str(DATA_DIR / "jobs" / job_id), "events": job["events"], "proposal": job["proposal"], "action": "acquire_source", "source_url": selection.url}
        acquired = call_agent("forger", base_state)
        profiled = call_agent("scout", {"objective": job["objective"], "job_dir": str(DATA_DIR / "jobs" / job_id), "events": acquired.get("events", []), "proposal": job["proposal"], "use_staged": True, "source_type": "web_source"})
        proposal = profiled["proposal"]
        missing = _missing_requested_fields(proposal.get("requirements", []), [column["name"] for column in proposal["profile"]["columns"]])
        if missing:
            raise ValueError(f"This source is missing requested fields: {', '.join(missing)}. Try another listing or attach a dataset with those fields.")
        proposal["source"] = "web_source"
        proposal["selected_source"] = {**listing, "download_url": acquired.get("acquired_source", {}).get("url"), "rows": acquired.get("acquired_source", {}).get("rows")}
        proposal["discovery_results"] = listings
        proposal["candidate_columns"] = proposal["profile"]["columns"]
        proposal["candidate_rows"] = acquired.get("acquired_source", {}).get("rows", proposal["profile"].get("rows_sampled", 0))
        job["proposal"] = proposal
        job["events"] = profiled.get("events", [])
        job["status"] = "awaiting_columns"
        job["error"] = None
    except HTTPException:
        job["status"] = "awaiting_columns"
        job["updated_at"] = now_iso()
        STORE.save(job)
        raise
    except Exception as exc:
        job["status"] = "awaiting_columns"
        job["error"] = str(exc) if isinstance(exc, ValueError) else "Forger could not retrieve this listing. Try another source or use a downloaded file."
        job["updated_at"] = now_iso()
        STORE.save(job)
        logger.exception("Source acquisition for job %s failed", job_id)
        raise HTTPException(422, job["error"]) from exc
    job["updated_at"] = now_iso()
    STORE.save(job)
    return job

async def _save_upload(upload: UploadFile, job_dir: Path):
    allowed = {".csv", ".json", ".jsonl", ".ndjson", ".xlsx", ".txt"}
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(415, "Upload CSV, JSON, JSONL, XLSX, or delimited TXT")
    path = job_dir / f"upload{suffix}"
    total = 0
    try:
        with path.open("wb") as out:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(413, f"File exceeds {MAX_UPLOAD_MB} MB limit")
                out.write(chunk)
        df = _read(str(path))
        if df.empty or len(df.columns) < 1 or len(df.columns) > 500:
            raise HTTPException(422, "Dataset must contain rows and between 1 and 500 columns")
        if any(not str(c).strip() for c in df.columns) or not df.columns.is_unique:
            raise HTTPException(422, "Dataset column names must be non-empty and unique")
        df.to_csv(job_dir / "input.csv", index=False)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, "Could not parse the uploaded tabular file") from exc
    return path

@app.post("/api/jobs", response_model=JobView, status_code=202)
async def create_job(request: Request, background_tasks: BackgroundTasks, objective: str = Form(..., min_length=12, max_length=2000), file: UploadFile | None = File(None)):
    peer = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _rate_lock:
        attempts = _job_requests[peer]
        while attempts and now - attempts[0] > 3600:
            attempts.popleft()
        if len(attempts) >= MAX_JOBS_PER_HOUR:
            raise HTTPException(429, f"Demo limit reached. Try again later (maximum {MAX_JOBS_PER_HOUR} jobs per hour per IP).")
        attempts.append(now)
    job_id = str(uuid.uuid4())
    folder = DATA_DIR / "jobs" / job_id
    folder.mkdir(parents=True, mode=0o700)
    source = await _save_upload(file, folder) if file else None
    timestamp = now_iso()
    job = {"id": job_id, "status": "running", "objective": objective, "created_at": timestamp, "updated_at": timestamp, "events": [], "proposal": None, "error": None}
    STORE.save(job)
    background_tasks.add_task(_run_job, job, str(source) if source else None, str(folder))
    return job

def _run_job(job, source_path, job_dir):
    try:
        # LangGraph nodes are bounded to local CSV operations; keep the event log observable.
        def progress(snapshot):
            job["events"] = snapshot.get("events", [])
            job["updated_at"] = now_iso()
            STORE.save(job)
        result = discover(job["objective"], source_path, job_dir, progress)
        job["events"] = result["events"]
        job["proposal"] = result["proposal"]
        job["status"] = "awaiting_columns"
        job["updated_at"] = now_iso()
        STORE.save(job)
    except Exception:
        job["status"] = "failed"
        job["error"] = "Workflow failed while processing the dataset. Check server logs using the job ID."
        job["updated_at"] = now_iso()
        STORE.save(job)
        logger.exception("Fetch job %s failed", job["id"])

def _run_delivery(job, selected_columns, target_column, job_dir):
    try:
        progress = lambda snapshot: (job.update(events=snapshot.get("events", []), updated_at=now_iso()), STORE.save(job))
        result = deliver({"objective": job["objective"], "job_dir": job_dir, "events": job["events"], "proposal": job["proposal"]}, selected_columns, target_column, progress)
        job.update(events=result["events"], proposal=result["proposal"], status="awaiting_review", updated_at=now_iso())
        STORE.save(job)
    except Exception:
        job.update(status="failed", error="Dataset engineering failed. Check server logs using the job ID.", updated_at=now_iso())
        STORE.save(job)
        logger.exception("Fetch delivery %s failed", job["id"])

@app.get("/api/jobs/{job_id}/events")
async def stream_events(job_id: str):
    if not STORE.get(job_id):
        raise HTTPException(404, "Job not found")
    async def generate():
        sent = 0
        while True:
            job = STORE.get(job_id)
            if job is None:
                yield "event: error\ndata: {}\n\n"
                return
            for index, event in enumerate(job["events"]):
                if index >= sent:
                    import json
                    yield f"id: {index + 1}\nevent: progress\ndata: {json.dumps(event)}\n\n"
                    sent = index + 1
            if job["status"] not in {"queued", "running"}:
                yield f"event: status\ndata: {job['status']}\n\n"
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.post("/api/jobs/{job_id}/columns", response_model=JobView, status_code=202)
def select_columns(job_id: str, selection: ColumnSelection, background_tasks: BackgroundTasks):
    job = STORE.get(job_id)
    if not job: raise HTTPException(404, "Job not found")
    if job["status"] != "awaiting_columns": raise HTTPException(409, "Job is not awaiting column selection")
    available = {c["name"] for c in job["proposal"]["candidate_columns"]}
    if len(set(selection.columns)) != len(selection.columns) or not set(selection.columns) <= available or selection.target not in selection.columns:
        raise HTTPException(422, "Choose unique dataset columns and a target included in that selection")
    job["status"] = "running"
    job["proposal"]["selected_columns"] = selection.columns
    job["proposal"]["target"] = selection.target
    job["updated_at"] = now_iso(); STORE.save(job)
    background_tasks.add_task(_run_delivery, job, selection.columns, selection.target, str(DATA_DIR / "jobs" / job_id))
    return job

@app.post("/api/jobs/{job_id}/review", response_model=JobView)
def review_job(job_id: str, review: ReviewRequest):
    job = STORE.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "awaiting_review":
        raise HTTPException(409, "Job is not awaiting review")
    if review.approved:
        job["status"] = "completed"
        job["events"].append({"agent": "Assembler", "message": "Human review approved. Exports are ready."})
    else:
        job["status"] = "failed"
        job["error"] = "Dataset proposal was rejected during review."
        shutil.rmtree(DATA_DIR / "jobs" / job_id, ignore_errors=True)
    job["updated_at"] = now_iso()
    STORE.save(job)
    return job

@app.get("/api/jobs/{job_id}/artifacts/{name}")
def get_artifact(job_id: str, name: str):
    job = STORE.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "completed":
        raise HTTPException(403, "Approve the dataset before downloading artifacts")
    if name not in {"dataset.csv", "dataset.json", "prepare.py"}:
        raise HTTPException(404, "Artifact not found")
    path = DATA_DIR / "jobs" / job_id / name
    if not path.is_file():
        raise HTTPException(404, "Artifact not found")
    return FileResponse(path, filename=name)

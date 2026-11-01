import asyncio
import csv
import hmac
import logging
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
from .schemas import ColumnSelection, JobView, ReviewRequest
from .store import JobStore, now_iso
from .agents.workflow import _read, deliver, discover

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

@app.get("/api/jobs/{job_id}", response_model=JobView)
def get_job(job_id: str):
    job = STORE.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
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

"""Single-role HTTP service used by one independently deployed Fetch agent."""
import os
from pathlib import Path
import tempfile
from typing import Any
import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .agents.workflow import otto, scout, forger, engineer, assemble, _read, _synthetic

ROLE = os.getenv("AGENT_ROLE", "").lower()
HANDLERS = {"otto": otto, "scout": scout, "forger": forger, "engineer": engineer, "assembler": assemble}
if ROLE not in HANDLERS:
    raise RuntimeError("AGENT_ROLE must be one of: otto, scout, forger, engineer, assembler")

app = FastAPI(title=f"Fetch {ROLE.title()} Agent", version="0.1.0")
STORAGE_URL = os.getenv("FETCH_STORAGE_URL", "http://api:8000").rstrip("/")
if "://" not in STORAGE_URL:
    STORAGE_URL = f"http://{STORAGE_URL}"
INTERNAL_TOKEN = os.getenv("FETCH_INTERNAL_TOKEN", "")

class AgentRequest(BaseModel):
    state: dict[str, Any]

@app.get("/health")
def health():
    return {"status": "ok", "agent": ROLE}

def storage_request(method: str, job_id: str, filename: str, content: bytes | None = None):
    if not INTERNAL_TOKEN:
        raise RuntimeError("FETCH_INTERNAL_TOKEN is not configured")
    response = httpx.request(method, f"{STORAGE_URL}/internal/jobs/{job_id}/files/{filename}", headers={"X-Fetch-Internal-Token": INTERNAL_TOKEN}, content=content, timeout=60.0)
    response.raise_for_status()
    return response.content

@app.post("/run")
def run_agent(request: AgentRequest):
    state = request.state
    job_id = Path(state["job_dir"]).name
    job_dir = Path(tempfile.gettempdir()) / "fetch-agents" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    # Agents run in separate containers, so workflow paths from the API are not
    # valid locally. Assemble outputs in this agent's temporary directory.
    state["job_dir"] = str(job_dir)
    if ROLE == "scout" and state.get("source_path"):
        upload_name = Path(state["source_path"]).name
        upload_path = job_dir / upload_name
        upload_path.write_bytes(storage_request("GET", job_id, upload_name))
        state["data"] = _read(str(upload_path))
    elif ROLE == "forger" and not state.get("source_path"):
        state["data"] = _synthetic(state["objective"])
    elif ROLE == "forger":
        staged_path = job_dir / "staged.csv"
        staged_path.write_bytes(storage_request("GET", job_id, "staged.csv"))
        state["data"] = pd.read_csv(staged_path)
    elif ROLE in {"engineer", "assembler"}:
        input_name = "staged.csv" if ROLE == "engineer" else "engineered.csv"
        data_path = job_dir / input_name
        try:
            data_path.write_bytes(storage_request("GET", job_id, input_name))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(409, f"Expected {input_name} from the previous workflow stage") from exc
            raise
        state["data"] = pd.read_csv(data_path)
    try:
        result = HANDLERS[ROLE](state)
        data = result.pop("data", None)
        if ROLE in {"scout", "forger"} and data is not None:
            storage_request("PUT", job_id, "staged.csv", data.to_csv(index=False).encode("utf-8"))
        elif ROLE == "engineer" and data is not None:
            storage_request("PUT", job_id, "engineered.csv", data.to_csv(index=False).encode("utf-8"))
        elif ROLE == "assembler":
            for artifact in ("dataset.csv", "dataset.json", "prepare.py"):
                storage_request("PUT", job_id, artifact, (job_dir / artifact).read_bytes())
        return {"state": result}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"{ROLE.title()} agent failed: {type(exc).__name__}") from exc

"""Single-role HTTP service used by one independently deployed Fetch agent."""
import os
from pathlib import Path
from typing import Any
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .agents.workflow import otto, scout, forger, engineer, assemble, _read, _synthetic

ROLE = os.getenv("AGENT_ROLE", "").lower()
HANDLERS = {"otto": otto, "scout": scout, "forger": forger, "engineer": engineer, "assembler": assemble}
if ROLE not in HANDLERS:
    raise RuntimeError("AGENT_ROLE must be one of: otto, scout, forger, engineer, assembler")

app = FastAPI(title=f"Fetch {ROLE.title()} Agent", version="0.1.0")

class AgentRequest(BaseModel):
    state: dict[str, Any]

@app.get("/health")
def health():
    return {"status": "ok", "agent": ROLE}

@app.post("/run")
def run_agent(request: AgentRequest):
    state = request.state
    job_dir = Path(state["job_dir"])
    job_dir.mkdir(parents=True, exist_ok=True)
    if ROLE == "scout" and state.get("source_path"):
        state["data"] = _read(state["source_path"])
    elif ROLE == "forger" and not state.get("source_path"):
        state["data"] = _synthetic(state["objective"])
    elif ROLE == "forger":
        state["data"] = pd.read_csv(job_dir / "staged.csv")
    elif ROLE in {"engineer", "assembler"}:
        input_name = "staged.csv" if ROLE == "engineer" else "engineered.csv"
        data_path = job_dir / input_name
        if not data_path.is_file():
            raise HTTPException(409, f"Expected {input_name} from the previous workflow stage")
        state["data"] = pd.read_csv(data_path)
    try:
        result = HANDLERS[ROLE](state)
        data = result.pop("data", None)
        if ROLE in {"scout", "forger"} and data is not None:
            data.to_csv(job_dir / "staged.csv", index=False)
        elif ROLE == "engineer" and data is not None:
            data.to_csv(job_dir / "engineered.csv", index=False)
        return {"state": result}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"{ROLE.title()} agent failed: {type(exc).__name__}") from exc

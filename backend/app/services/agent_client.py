"""HTTP client used by the LangGraph orchestrator to call independent agent services."""
import os
import httpx

AGENT_URLS = {
    "otto": os.getenv("OTTO_URL", "http://otto:8100"),
    "scout": os.getenv("SCOUT_URL", "http://scout:8100"),
    "forger": os.getenv("FORGER_URL", "http://forger:8100"),
    "engineer": os.getenv("ENGINEER_URL", "http://engineer:8100"),
    "assembler": os.getenv("ASSEMBLER_URL", "http://assembler:8100"),
}

def call_agent(role: str, state: dict):
    try:
        response = httpx.post(f"{AGENT_URLS[role]}/run", json={"state": state}, timeout=180.0)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError(f"{role} returned an invalid state")
        return result["state"]
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"Agent service '{role}' is unavailable or returned an invalid response") from exc

import time
import io
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import pandas as pd
from app.services import discovery
from app.services import llm
from app.main import app

client = TestClient(app)

def wait_for_status(job_id, wanted):
    for _ in range(100):
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {wanted, "failed"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"workflow did not reach {wanted}")

def select_columns(job):
    proposal = job["proposal"]
    cols = [c["name"] for c in proposal["candidate_columns"]]
    target = next((c for c in cols if c.lower() in {"outcome", "label", "churn", "sentiment", "final_score"}), cols[-1])
    return client.post(f"/api/jobs/{job['id']}/columns", json={"columns": cols, "target": target})

def test_health():
    assert client.get("/health").json() == {"status": "ok"}

def test_synthetic_review_gate_and_exports():
    created = client.post("/api/jobs", data={"objective": "Predict the outcome for a student based on study patterns."})
    assert created.status_code == 202
    job_id = created.json()["id"]
    job = wait_for_status(job_id, "awaiting_columns")
    assert job["proposal"]["source"] == "synthetic_demo"
    assert select_columns(job).status_code == 202
    job = wait_for_status(job_id, "awaiting_review")
    assert client.get(f"/api/jobs/{job_id}/artifacts/dataset.csv").status_code == 403
    approved = client.post(f"/api/jobs/{job_id}/review", json={"approved": True})
    assert approved.status_code == 200 and approved.json()["status"] == "completed"
    assert client.get(f"/api/jobs/{job_id}/artifacts/dataset.csv").status_code == 200
    assert client.get(f"/api/jobs/{job_id}/artifacts/prepare.py").status_code == 200

def test_csv_upload_and_reject():
    created = client.post("/api/jobs", data={"objective": "Predict whether a client will renew a subscription."}, files={"file": ("input.csv", "age,renewed\n32,yes\n44,no\n", "text/csv")})
    assert created.status_code == 202
    job_id = created.json()["id"]
    job = wait_for_status(job_id, "awaiting_columns")
    assert select_columns(job).status_code == 202
    job = wait_for_status(job_id, "awaiting_review")
    assert job["proposal"]["source"] == "uploaded"
    rejected = client.post(f"/api/jobs/{job_id}/review", json={"approved": False})
    assert rejected.status_code == 200
    assert client.get(f"/api/jobs/{job_id}/artifacts/dataset.csv").status_code == 403

def test_reject_non_csv():
    response = client.post("/api/jobs", data={"objective": "Predict if this record meets the requested outcome."}, files={"file": ("payload.bin", "a,b\n1,2\n", "application/octet-stream")})
    assert response.status_code == 415

def test_json_and_text_upload_formats():
    for filename, payload, target in [
        ("input.json", '[{"feature":1,"outcome":0},{"feature":2,"outcome":1}]', "outcome"),
        ("input.jsonl", '{"feature":1,"outcome":0}\n{"feature":2,"outcome":1}\n', "outcome"),
        ("input.txt", "feature,outcome\n1,0\n2,1\n", "outcome"),
    ]:
        created = client.post("/api/jobs", data={"objective": "Predict the outcome from the supplied feature."}, files={"file": (filename, payload, "application/octet-stream")})
        assert created.status_code == 202
        job = wait_for_status(created.json()["id"], "awaiting_columns")
        assert job["proposal"]["candidate_rows"] == 2
        assert target in [c["name"] for c in job["proposal"]["candidate_columns"]]

def test_xlsx_upload_format():
    stream = io.BytesIO()
    pd.DataFrame({"feature": [1, 2], "outcome": [0, 1]}).to_excel(stream, index=False)
    response = client.post("/api/jobs", data={"objective": "Predict the outcome from the supplied feature."}, files={"file": ("input.xlsx", stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert response.status_code == 202
    job = wait_for_status(response.json()["id"], "awaiting_columns")
    assert job["proposal"]["candidate_rows"] == 2

def test_firecrawl_search_scores_and_filters_results(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"data": {"web": [
                {"title": "Customer churn dataset", "url": "https://huggingface.co/datasets/example", "description": "Customer churn CSV data"},
                {"title": "unsafe", "url": "http://127.0.0.1/private", "description": "customer churn"},
            ]}}
    called = {}
    def fake_post(url, **kwargs):
        called["url"] = url
        called["payload"] = kwargs["json"]
        return Response()
    monkeypatch.setattr(discovery, "FIRECRAWL_API_KEY", "test-key")
    monkeypatch.setattr(discovery.httpx, "post", fake_post)
    results = discovery.search_datasets("customer churn prediction")
    assert called["url"].endswith("/v2/search")
    assert len(results) == 1 and results[0]["match_score"] > 0

def test_llm_structured_primary_and_fallback(monkeypatch):
    providers = [
        ("Primary", "https://primary.example/chat", "secret-a", "primary-model"),
        ("Fallback", "https://fallback.example/chat", "secret-b", "fallback-model"),
    ]
    calls = []
    monkeypatch.setattr(llm, "PROVIDERS", providers)
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": '{"task_type":"classification","target_column":"churn","experience_level":"novice","feature_requirements":["tenure"],"summary":"Predict churn."}'}}]}
    def fake_post(url, **kwargs):
        calls.append(url)
        if "primary" in url: raise RuntimeError("primary unavailable")
        return Response()
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    result, provider = llm.interpret_goal("Predict customer churn")
    assert provider == "Fallback"
    assert result.target_column == "churn"
    assert calls == ["https://primary.example/chat", "https://fallback.example/chat"]

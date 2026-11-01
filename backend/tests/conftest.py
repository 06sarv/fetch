import pytest
from app.agents import workflow

@pytest.fixture(autouse=True)
def avoid_external_provider_calls(monkeypatch):
    # The user's .env is loaded by the app. Tests stay deterministic and never spend API quota.
    monkeypatch.setattr(workflow, "interpret_goal", lambda _objective: (None, None))
    monkeypatch.setattr(workflow, "assess_columns", lambda _objective, columns: (None, None))
    monkeypatch.setattr(workflow, "search_datasets", lambda _objective: [])

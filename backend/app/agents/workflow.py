"""Fetch's bounded, reviewable LangGraph workflow. Never executes model-written code."""
from pathlib import Path
from typing import Any, TypedDict
import json
import numpy as np
import pandas as pd
from langgraph.graph import END, StateGraph
from ..services.discovery import search_datasets
from ..services.llm import interpret_goal, assess_columns
from ..config import FIRECRAWL_API_KEY
from ..services.agent_client import call_agent

class FetchState(TypedDict, total=False):
    objective: str
    source_path: str | None
    job_dir: str
    events: list[dict[str, Any]]
    proposal: dict[str, Any]
    data: Any
    selected_columns: list[str]

def _event(state, agent, message):
    state.setdefault("events", []).append({"agent": agent, "message": message})

def _read(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".csv": return pd.read_csv(path)
    if suffix in {".jsonl", ".ndjson"}: return pd.read_json(path, lines=True)
    if suffix == ".json":
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if isinstance(data, dict): data = data.get("data", data.get("records", data))
        return pd.DataFrame(data)
    if suffix == ".xlsx": return pd.read_excel(path, engine="openpyxl")
    if suffix == ".txt": return pd.read_csv(path, sep=None, engine="python")
    raise ValueError("Unsupported tabular format")

def _synthetic(objective):
    rng = np.random.default_rng(42)
    n = 250
    goal = objective.lower()
    if any(x in goal for x in ["churn", "renew", "retention"]):
        months = rng.integers(1, 60, n); spend = rng.normal(85, 25, n).clip(10, 250).round(2)
        p = 1 / (1 + np.exp(-(-1.5 + .035 * months - .012 * spend)))
        return pd.DataFrame({"tenure_months": months, "monthly_spend": spend, "support_tickets": rng.poisson(1.4, n), "churn": (rng.random(n) < p).astype(int)})
    if any(x in goal for x in ["sentiment", "review", "text"]):
        phrases = ["excellent and easy to use", "slow response and disappointing", "works well for the price", "poor quality and frustrating", "reliable and pleasant"]
        label = rng.integers(0, 2, n)
        return pd.DataFrame({"review_text": [phrases[(int(x) + int(i % 3)) % len(phrases)] for i, x in enumerate(label)], "verified_purchase": rng.integers(0, 2, n), "sentiment": label})
    if any(x in goal for x in ["student", "score", "grade", "academic"]):
        study = rng.integers(1, 12, n); attendance = rng.uniform(.55, 1, n).round(3)
        score = np.clip(28 + study * 4.5 + attendance * 24 + rng.normal(0, 8, n), 0, 100).round(1)
        return pd.DataFrame({"study_hours": study, "attendance_rate": attendance, "previous_score": np.clip(score + rng.normal(0, 12, n), 0, 100).round(1), "final_score": score})
    x1, x2 = rng.normal(size=n), rng.normal(size=n)
    label = (x1 + .6*x2 + rng.normal(0, .6, n) > 0).astype(int)
    return pd.DataFrame({"feature_a": x1.round(4), "feature_b": x2.round(4), "feature_c": rng.normal(0, 1, n).round(4), "outcome": label})

def otto(state):
    words = state["objective"].lower()
    goal_spec, provider = interpret_goal(state["objective"])
    target = None
    for marker in ("predict ", "predicting ", "classify ", "classification of ", "estimate "):
        if marker in words:
            target = words.split(marker, 1)[1].split(" ")[0].strip(".,?!")
            break
    experience = "expert" if any(x in words for x in ["feature", "baseline", "imbalanced", "cross-validation", "regression"]) else "novice"
    task = "classification" if any(k in words for k in ["classify", "classification", "category", "label", "sentiment", "churn"]) else "regression"
    requirements = []
    summary = ""
    if goal_spec:
        task = goal_spec.task_type
        target = goal_spec.target_column or target
        experience = goal_spec.experience_level
        requirements = goal_spec.feature_requirements
        summary = goal_spec.summary
    state["proposal"] = {"task": task, "target_hint": target, "experience_level": experience, "requirements": requirements, "goal_summary": summary}
    suffix = f" using {provider}" if provider else " with local fallback"
    _event(state, "Otto", f"Understood the goal and prepared a {experience}-friendly dataset brief{suffix}.")
    return state

def _rank_columns(objective, columns):
    assessed, _provider = assess_columns(objective, columns)
    if not assessed:
        return columns
    by_name = {str(item.get("name")): item for item in assessed.columns if isinstance(item, dict)}
    for column in columns:
        item = by_name.get(column["name"])
        if item:
            relevance = item.get("relevance")
            if relevance in {"relevant", "optional", "irrelevant"}:
                column["relevance"] = relevance
            column["reason"] = str(item.get("reason", ""))[:240]
            column["selected"] = bool(item.get("selected", column["selected"]))
    return columns

def scout(state):
    if state.get("source_path"):
        df = _read(state["source_path"])
        if df.empty or len(df.columns) > 500: raise ValueError("Dataset is empty or has too many columns")
        columns = []
        for c in df.columns:
            series = df[c]
            name = str(c)
            excluded = (series.nunique(dropna=True) <= 1 or name.lower().startswith("unnamed:") or name.lower() in {"id", "uuid", "timestamp", "created_at"})
            columns.append({"name": name, "dtype": str(series.dtype), "missing": int(series.isna().sum()), "unique": int(series.nunique(dropna=True)), "relevance": "irrelevant" if excluded else "relevant", "selected": not excluded})
        columns = _rank_columns(state["objective"], columns)
        state["data"] = df
        source = "uploaded"
        _event(state, "Scout", f"Profiled {len(df)} rows and marked likely useful columns for review.")
    else:
        columns = []
        source = "synthetic_demo"
        _event(state, "Scout", "No upload was provided; looking for candidate data, then using a labeled synthetic fallback.")
    state["proposal"].update({"profile": {"rows_sampled": len(state.get("data", [])), "columns": columns, "source": source}, "source": source})
    return state

def forger(state):
    if not state.get("source_path"):
        search_failed = False
        try:
            sources = search_datasets(state["objective"])
        except Exception:
            sources = []
            search_failed = True
        state["proposal"]["discovery_results"] = sources
        df = _synthetic(state["objective"])
        state["data"] = df
        candidate_columns = [{"name": str(c), "dtype": str(df[c].dtype), "missing": int(df[c].isna().sum()), "unique": int(df[c].nunique()), "relevance": "relevant", "selected": True} for c in df.columns]
        candidate_columns = _rank_columns(state["objective"], candidate_columns)
        state["proposal"]["profile"] = {"rows_sampled": len(df), "columns": candidate_columns, "source": "synthetic demo"}
        if sources:
            _event(state, "Forger", f"Found {len(sources)} dataset listings. Review a source below and upload a downloaded file, or continue with the synthetic demo.")
        elif search_failed:
            _event(state, "Forger", "Dataset search is temporarily unavailable; using a labeled synthetic demonstration.")
        elif not FIRECRAWL_API_KEY:
            _event(state, "Forger", "Web dataset search is not configured; using a labeled synthetic demonstration.")
        else:
            _event(state, "Forger", "No candidate listings matched; using a labeled synthetic demonstration.")
    df = state["data"]
    state["proposal"].update({"candidate_columns": state["proposal"]["profile"]["columns"], "candidate_rows": int(len(df))})
    _event(state, "Forger", f"Prepared {len(df)} candidate rows from {state['proposal']['source'].replace('_', ' ')}.")
    return state

def engineer(state):
    df = state["data"].copy()
    selected = state.get("selected_columns") or list(df.columns)
    if not selected or any(c not in df.columns for c in selected): raise ValueError("Selected columns do not match the dataset")
    df = df[selected].drop_duplicates().reset_index(drop=True)
    hint = state["proposal"].get("target_hint")
    target = state.get("target_column")
    if target not in df.columns: target = next((c for c in df.columns if hint and (c.lower() == hint or hint in c.lower())), None)
    if target is None: target = next((c for c in df.columns if c.lower() in {"target", "label", "class", "outcome", "y", "final_score", "churn", "sentiment"}), df.columns[-1])
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            median = df[col].median(); df[col] = df[col].fillna(0 if pd.isna(median) else median)
            if col != target:
                q1, q3 = df[col].quantile([.25, .75]); spread = q3-q1
                if spread > 0: df[col] = df[col].clip(q1-1.5*spread, q3+1.5*spread)
        else:
            mode = df[col].mode(dropna=True); filled = df[col].fillna("Unknown" if mode.empty else mode.iloc[0]).astype(str)
            if col == target:
                df[col] = pd.Categorical(filled).codes
            else:
                df[col] = pd.Categorical(filled).codes
    state["data"] = df
    state["proposal"].update({"rows": int(len(df)), "columns_count": int(len(df.columns)), "columns": list(map(str, df.columns)), "target": str(target), "missing_after": int(df.isna().sum().sum()), "selected_columns": selected})
    _event(state, "Engineer", f"Cleaned selected columns, encoded categories, and clipped feature outliers. Target: '{target}'.")
    return state

def assemble(state):
    df = state["data"]
    folder = Path(state["job_dir"]); folder.mkdir(parents=True, exist_ok=True)
    df.to_csv(folder / "dataset.csv", index=False); df.to_json(folder / "dataset.json", orient="records", indent=2)
    target = state["proposal"]["target"]
    script = '''# Reproducible Fetch preparation (input.csv is the normalized uploaded table)\nimport pandas as pd\ndf = pd.read_csv("input.csv")\nselected = ''' + repr(state.get("selected_columns") or list(df.columns)) + '''\ntarget = ''' + repr(target) + '''\ndf = df[selected].drop_duplicates().reset_index(drop=True)\nfor col in df.columns:\n    if pd.api.types.is_numeric_dtype(df[col]):\n        median = df[col].median()\n        df[col] = df[col].fillna(0 if pd.isna(median) else median)\n        if col != target:\n            q1, q3 = df[col].quantile([.25, .75]); spread = q3 - q1\n            if spread > 0: df[col] = df[col].clip(q1 - 1.5 * spread, q3 + 1.5 * spread)\n    else:\n        mode = df[col].mode(dropna=True)\n        df[col] = pd.Categorical(df[col].fillna("Unknown" if mode.empty else mode.iloc[0]).astype(str)).codes\ndf.to_csv("prepared.csv", index=False)\n'''
    (folder / "prepare.py").write_text(script, encoding="utf-8")
    state["proposal"].update({"preview": json.loads(df.head(20).to_json(orient="records", date_format="iso")), "artifacts": ["dataset.csv", "dataset.json", "prepare.py"]})
    _event(state, "Assembler", "Prepared the cleaned preview, CSV, JSON, and reproducible processing script.")
    return state

def _graph(nodes, edges):
    graph = StateGraph(FetchState)
    for name, fn in nodes: graph.add_node(name, fn)
    graph.set_entry_point(nodes[0][0])
    for left, right in edges: graph.add_edge(left, right)
    graph.add_edge(nodes[-1][0], END)
    return graph.compile()

def _remote(role):
    return lambda state: call_agent(role, state)

discovery_graph = _graph([("otto", _remote("otto")), ("scout", _remote("scout")), ("forger", _remote("forger"))], [("otto", "scout"), ("scout", "forger")])
delivery_graph = _graph([("engineer", _remote("engineer")), ("assembler", _remote("assembler"))], [("engineer", "assembler")])

def discover(objective: str, source_path: str | None, job_dir: str, on_progress=None):
    state = {"objective": objective, "source_path": source_path, "job_dir": job_dir, "events": [], "data_dir": str(Path(job_dir).parent)}
    for snapshot in discovery_graph.stream(state, stream_mode="values"):
        state = snapshot
        if on_progress: on_progress(state)
    return state

def deliver(state, selected_columns: list[str], target_column: str, on_progress=None):
    state["selected_columns"] = selected_columns
    state["target_column"] = target_column
    for snapshot in delivery_graph.stream(state, stream_mode="values"):
        state = snapshot
        if on_progress: on_progress(state)
    return state

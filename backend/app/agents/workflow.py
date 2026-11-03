"""Fetch's bounded, reviewable LangGraph workflow. Never executes model-written code."""
from pathlib import Path
from typing import Any, TypedDict
import json
import re
from datetime import datetime
import numpy as np
import pandas as pd
from langgraph.graph import END, StateGraph
from ..services.discovery import search_datasets
from ..services.llm import interpret_goal, assess_columns, ask_clarifying_question
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

def _synthetic(objective, target_hint=None, requirements=None):
    rng = np.random.default_rng(42)
    n = 250
    goal = objective.lower()
    required = [str(name).strip() for name in (requirements or []) if str(name).strip()]
    if any(x in goal for x in ["hiv", "aids", "human immunodeficiency"]):
        if any(x in goal for x in ["prevalence", "epidemiology", "country-year", "country year", "trend", "trends", "population", "by country"]):
            countries = np.array(["India", "South Africa", "Nigeria", "Kenya", "Uganda", "Tanzania", "Ethiopia", "Mozambique", "Zambia", "Zimbabwe", "Brazil", "Thailand", "Ukraine", "United States", "United Kingdom", "France", "China", "Indonesia", "Mexico", "Rwanda", "Botswana", "Malawi", "Ghana", "Cameroon", "Lesotho"])
            requested_years = [int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", goal)]
            start_year = min(requested_years) if requested_years else 2000
            years = np.arange(start_year, datetime.now().year)
            country = np.repeat(countries, len(years))
            year = np.tile(years, len(countries))
            country_baseline = np.repeat(rng.uniform(.15, 18, len(countries)), len(years))
            variation = rng.normal(0, .12, len(country))
            prevalence = np.maximum(.01, country_baseline - (year - 2014) * .025 + variation).round(3)
            population = rng.integers(500_000, 95_000_000, len(country))
            living_with_hiv = np.maximum(0, np.rint(population * prevalence / 100)).astype(int)
            new_infections = np.maximum(0, np.rint(living_with_hiv * rng.uniform(.012, .08, len(country)))).astype(int)
            aids_deaths = np.maximum(0, np.rint(living_with_hiv * rng.uniform(.004, .035, len(country)))).astype(int)
            return pd.DataFrame({"country": country, "year": year, "estimated_hiv_prevalence_pct": prevalence, "population": population, "estimated_people_living_with_hiv": living_with_hiv, "estimated_new_hiv_infections": new_infections, "estimated_aids_related_deaths": aids_deaths})
        cd4 = rng.normal(520, 190, n).clip(40, 1400).round().astype(int)
        viral_load = np.exp(rng.normal(7.2, 1.4, n)).clip(20, 2_000_000).round().astype(int)
        hemoglobin = rng.normal(13.4, 1.8, n).clip(6, 19).round(1)
        white_cells = rng.normal(6.8, 1.8, n).clip(1, 18).round(1)
        platelets = rng.normal(245, 55, n).clip(50, 500).round().astype(int)
        liver_alt = rng.gamma(2.2, 14, n).clip(4, 180).round(1)
        creatinine = rng.normal(.95, .25, n).clip(.3, 3).round(2)
        total_cholesterol = rng.normal(185, 38, n).clip(90, 340).round().astype(int)
        status = ((cd4 < 350) | (viral_load > 100_000)).astype(int)
        return pd.DataFrame({"cd4_count": cd4, "viral_load_copies_ml": viral_load, "hemoglobin_g_dl": hemoglobin, "white_blood_cell_count": white_cells, "platelet_count": platelets, "alt_u_l": liver_alt, "creatinine_mg_dl": creatinine, "total_cholesterol_mg_dl": total_cholesterol, "hiv_status_demo_label": status})
    if any(x in goal for x in ["insurance", "medical cost", "healthcare cost", "health cost", "premium"]):
        age = rng.integers(18, 65, n)
        sex = rng.choice(["female", "male"], n)
        bmi = rng.normal(30, 6, n).clip(15, 53).round(1)
        children = rng.poisson(1.1, n).clip(0, 5)
        smoker = rng.choice(["yes", "no"], n, p=[.2, .8])
        region = rng.choice(["northeast", "northwest", "southeast", "southwest"], n)
        charges = (650 + age * 210 + bmi * 280 + children * 420 + (smoker == "yes") * 14500 + rng.normal(0, 2400, n)).clip(1000).round(2)
        return pd.DataFrame({"age": age, "sex": sex, "bmi": bmi, "children": children, "smoker": smoker, "region": region, "charges": charges})
    if any(x in goal for x in ["house price", "housing", "home value", "property price", "real estate"]):
        area = rng.normal(1850, 620, n).clip(450, 5200).round().astype(int)
        bedrooms = rng.integers(1, 7, n); bathrooms = rng.integers(1, 5, n)
        neighborhood = rng.choice(["urban", "suburban", "rural"], n, p=[.35, .45, .2])
        year_built = rng.integers(1950, 2025, n)
        price = (45000 + area * 145 + bedrooms * 11000 + bathrooms * 9000 + (neighborhood == "urban") * 35000 - (2025-year_built) * 350 + rng.normal(0, 35000, n)).clip(50000).round(-2).astype(int)
        return pd.DataFrame({"area_sqft": area, "bedrooms": bedrooms, "bathrooms": bathrooms, "neighborhood": neighborhood, "year_built": year_built, "sale_price": price})
    if any(x in goal for x in ["loan", "credit risk", "default", "borrower"]):
        age = rng.integers(20, 70, n); income = rng.normal(62000, 24000, n).clip(15000, 180000).round(2)
        credit_score = rng.normal(680, 65, n).clip(300, 850).round().astype(int)
        loan_amount = rng.normal(18000, 11000, n).clip(500, 80000).round(2)
        employment_years = rng.integers(0, 36, n); debt_to_income = rng.beta(2.4, 5, n).round(3)
        chance = 1 / (1 + np.exp(-(-3 + debt_to_income * 5 + loan_amount / 18000 - credit_score / 250)))
        defaulted = (rng.random(n) < chance).astype(int)
        return pd.DataFrame({"age": age, "annual_income": income, "credit_score": credit_score, "loan_amount": loan_amount, "employment_years": employment_years, "debt_to_income_ratio": debt_to_income, "defaulted": defaulted})
    if any(x in goal for x in ["sales", "revenue", "retail", "demand", "product"]) and not any(x in goal for x in ["return", "refund"]):
        category = rng.choice(["electronics", "clothing", "home", "beauty", "grocery"], n)
        unit_price = rng.lognormal(3.2, .8, n).clip(3, 800).round(2)
        units_sold = rng.poisson(18, n) + 1; discount = rng.uniform(0, .35, n).round(2)
        marketing_spend = rng.uniform(0, 500, n).round(2)
        revenue = (unit_price * units_sold * (1-discount) + marketing_spend * .6 + rng.normal(0, 80, n)).clip(0).round(2)
        return pd.DataFrame({"product_category": category, "unit_price": unit_price, "units_sold": units_sold, "discount_rate": discount, "marketing_spend": marketing_spend, "revenue": revenue})
    if any(x in goal for x in ["product return", "product returns", "return rate", "returned item", "refund"]):
        category = rng.choice(["electronics", "clothing", "home", "beauty", "sports"], n)
        purchase_amount = rng.lognormal(3.5, .7, n).clip(5, 1200).round(2)
        discount = rng.uniform(0, .5, n).round(2)
        channel = rng.choice(["online", "store", "marketplace"], n)
        delivery_days = rng.integers(1, 15, n)
        satisfaction = rng.integers(1, 6, n)
        return_probability = 1 / (1 + np.exp(-(-2.4 + discount * 3 + (satisfaction < 3) * 1.2 + (delivery_days > 7) * .7)))
        returned = (rng.random(n) < return_probability).astype(int)
        return pd.DataFrame({"product_category": category, "purchase_amount": purchase_amount, "discount_rate": discount, "sales_channel": channel, "delivery_days": delivery_days, "satisfaction_rating": satisfaction, "returned": returned})
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
    if any(x in goal for x in ["weather", "rain", "temperature", "precipitation"]):
        temperature = rng.normal(22, 9, n).clip(-8, 42).round(1)
        humidity = rng.uniform(25, 100, n).round(1); pressure = rng.normal(1013, 9, n).round(1)
        wind = rng.gamma(2, 7, n).clip(0, 65).round(1); rain_mm = (rng.exponential(2, n) * (humidity > 72)).round(1)
        rain_next_day = ((humidity > 78) & (pressure < 1015) | (rain_mm > 5)).astype(int)
        return pd.DataFrame({"temperature_c": temperature, "humidity_pct": humidity, "pressure_hpa": pressure, "wind_speed_kmh": wind, "precipitation_mm": rain_mm, "rain_next_day": rain_next_day})
    if required:
        columns = {}
        for name in required[:6]:
            key = name.lower().replace(" ", "_")
            if any(term in key for term in ["age", "year"]): columns[name] = rng.integers(18, 66, n)
            elif any(term in key for term in ["income", "spend", "cost", "price", "amount", "salary"]): columns[name] = rng.normal(60000, 18000, n).clip(0).round(2)
            elif any(term in key for term in ["rate", "percent", "ratio", "bmi"]): columns[name] = rng.uniform(.1, 1, n).round(3)
            elif any(term in key for term in ["count", "tickets", "children", "visits"]): columns[name] = rng.poisson(2, n)
            else: columns[name] = rng.normal(50, 15, n).round(2)
        target = str(target_hint or "").strip()
        if not target or target in columns: target = "target_value"
        score = sum(np.asarray(value, dtype=float) for value in list(columns.values())[:3]) if columns else rng.normal(0, 1, n)
        columns[target] = (score / max(len(columns), 1) + rng.normal(0, 5, n)).round(2)
        return pd.DataFrame(columns)
    age = rng.integers(18, 66, n); monthly_spend = rng.normal(75, 22, n).clip(10, 250).round(2)
    purchases = rng.poisson(4, n); satisfaction = rng.integers(1, 6, n)
    returned = ((monthly_spend > 120) & (satisfaction < 3) | (purchases == 0)).astype(int)
    return pd.DataFrame({"customer_age": age, "monthly_spend": monthly_spend, "purchases_last_month": purchases, "satisfaction_rating": satisfaction, "returned_next_month": returned})

def otto(state):
    if state.get("action") == "clarify":
        turn, provider = ask_clarifying_question(state["objective"], state.get("answers", []))
        state["clarification"] = {"question": turn.question, "ready": turn.ready, "provider": provider}
        return state
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
    if target and target.lower().replace(" ", "_") in {"aids", "hiv", "human_immunodeficiency_virus"}:
        target = "hiv_status_demo_label"
        task = "classification"
    elif target is None and any(term in words for term in ["hiv", "aids", "human immunodeficiency"]) and any(term in words for term in ["prevalence", "epidemiology", "country-year", "country year", "trend", "trends", "population", "by country"]):
        target = "estimated_hiv_prevalence_pct"
        task = "regression"
    elif any(term in words for term in ["hiv", "aids", "human immunodeficiency"]) and task == "regression":
        task = "classification"
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
    if state.get("source_path") or isinstance(state.get("data"), pd.DataFrame):
        df = _read(state["source_path"]) if state.get("source_path") else state["data"]
        if df.empty or len(df.columns) > 500: raise ValueError("Dataset is empty or has too many columns")
        columns = []
        for c in df.columns:
            series = df[c]
            name = str(c)
            excluded = (series.nunique(dropna=True) <= 1 or name.lower().startswith("unnamed:") or name.lower() in {"id", "uuid", "timestamp", "created_at"})
            columns.append({"name": name, "dtype": str(series.dtype), "missing": int(series.isna().sum()), "unique": int(series.nunique(dropna=True)), "relevance": "irrelevant" if excluded else "relevant", "selected": not excluded})
        columns = _rank_columns(state["objective"], columns)
        state["data"] = df
        source = state.get("source_type", "uploaded")
        _event(state, "Scout", f"Profiled {len(df)} rows and marked likely useful columns for review.")
    else:
        columns = []
        source = "synthetic_demo"
        _event(state, "Scout", "No file was uploaded. Forger will generate a domain-matched synthetic dataset and search for related public sources.")
    state["proposal"].update({"profile": {"rows_sampled": len(state.get("data", [])), "columns": columns, "source": source}, "source": source})
    return state

def forger(state):
    search_failed = False
    try:
        proposal = state.get("proposal", {})
        search_context = " ".join([state["objective"], str(proposal.get("goal_summary") or ""), *proposal.get("requirements", []), str(proposal.get("target_hint") or "")])
        sources = search_datasets(search_context)
    except Exception:
        sources = []
        search_failed = True
    proposal = state.get("proposal", {})
    proposal["discovery_results"] = sources
    if not state.get("source_path"):
        df = _synthetic(state["objective"], proposal.get("target_hint"), proposal.get("requirements"))
        state["data"] = df
        candidate_columns = [{"name": str(c), "dtype": str(df[c].dtype), "missing": int(df[c].isna().sum()), "unique": int(df[c].nunique()), "relevance": "relevant", "selected": True} for c in df.columns]
        # Every field in a purpose-built synthetic schema is included deliberately.
        # A model's generic relevance guess must not erase the selected goal's schema.
        state["proposal"]["profile"] = {"rows_sampled": len(df), "columns": candidate_columns, "source": "synthetic demo"}
        state["proposal"]["source"] = "synthetic_demo"
    if sources:
        suffix = " The uploaded file remains active until you choose a source." if state.get("source_path") else " Continue with the labeled synthetic demo if no public file fits."
        _event(state, "Forger", f"Found {len(sources)} dataset listings. Choose a source to try loading its public data.{suffix}")
    elif search_failed:
        _event(state, "Forger", "Dataset search is temporarily unavailable; the current upload or labeled synthetic demo remains available.")
    elif not FIRECRAWL_API_KEY:
        _event(state, "Forger", "Web dataset search is not configured; the current upload or labeled synthetic demo remains available.")
    else:
        _event(state, "Forger", "No candidate listings matched; the current upload or labeled synthetic demo remains available.")
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

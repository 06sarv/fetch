"""Optional Firecrawl dataset search. Search results are suggestions, never trusted code or data."""
import re
import httpx
from ..config import FIRECRAWL_API_KEY

DATA_DOMAINS = [
    "kaggle.com", "huggingface.co", "github.com", "data.gov", "data.europa.eu",
    "archive.ics.uci.edu", "openml.org", "zenodo.org", "dataverse.org", "data.world",
]
STOP_WORDS = {"the", "and", "for", "from", "with", "that", "this", "into", "using", "use", "dataset", "data", "predict", "prediction", "build", "create", "find", "make", "based", "about", "model", "ml", "machine", "learning"}

def search_datasets(objective: str) -> list[dict]:
    if not FIRECRAWL_API_KEY:
        return []
    query = f"dataset for {objective[:300]} CSV"
    response = httpx.post(
        "https://api.firecrawl.dev/v2/search",
        headers={"Authorization": f"Bearer {FIRECRAWL_API_KEY}"},
        json={"query": query, "limit": 5, "sources": [{"type": "web"}], "includeDomains": DATA_DOMAINS, "safe": True, "timeout": 20000, "highlights": False},
        timeout=25.0,
    )
    response.raise_for_status()
    body = response.json()
    hits = body.get("data", {}).get("web", [])
    terms = set(re.findall(r"[a-z0-9]{3,}", objective.lower())) - STOP_WORDS
    results = []
    for hit in hits[:5]:
        url, title = str(hit.get("url", "")), str(hit.get("title", ""))
        if not url.startswith("https://"):
            continue
        summary = str(hit.get("description", ""))[:500]
        words = set(re.findall(r"[a-z0-9]{3,}", (title + " " + summary).lower()))
        matched = terms & words
        score = round(100 * len(matched) / max(len(terms), 1))
        results.append({"title": title[:240], "url": url[:1000], "description": summary, "match_score": score})
    return sorted(results, key=lambda r: r["match_score"], reverse=True)

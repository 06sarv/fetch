"""Optional Firecrawl dataset search. Search results are suggestions, never trusted code or data."""
import ipaddress
import io
import re
import socket
from urllib.parse import urljoin, urlparse
import httpx
import pandas as pd
from ..config import FIRECRAWL_API_KEY

DATA_DOMAINS = [
    "kaggle.com", "huggingface.co", "github.com", "data.gov", "data.europa.eu",
    "raw.githubusercontent.com", "cdn-lfs.huggingface.co", "hf.co", "archive.ics.uci.edu", "openml.org", "zenodo.org", "dataverse.org", "data.world",
    "worldbank.org", "who.int", "unaids.org", "unicef.org", "data.un.org", "ourworldindata.org", "humdata.org",
]
TABULAR_EXTENSIONS = {".csv", ".json", ".jsonl", ".ndjson", ".xlsx", ".txt"}
STOP_WORDS = {"the", "and", "for", "from", "with", "that", "this", "into", "using", "use", "dataset", "datasets", "data", "predict", "prediction", "build", "create", "find", "make", "based", "about", "model", "ml", "machine", "learning", "a", "an", "to", "of", "in", "on", "at", "by", "is", "are", "be", "as", "it", "or", "if", "no", "can", "will", "would", "should", "could", "from", "prefer", "public", "real", "source", "sources", "overall", "only", "clearly", "labeled", "labelled", "synthetic", "demo", "suitable", "retrieved", "available", "related", "please", "want", "need", "useful", "include", "including", "starting", "start", "onward", "after", "before", "where", "possible", "possible", "than", "rather", "something", "else", "such", "which", "what", "should", "fetch", "user", "attached", "file", "upload", "uploaded", "csv", "row", "rows", "column", "columns", "analysis", "analyze", "analyse", "prepare", "prepared", "create", "make", "from"}

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
        if not matched:
            continue
        score = round(100 * len(matched) / max(len(terms), 1))
        results.append({"title": title[:240], "url": url[:1000], "description": summary, "match_score": score})
    return sorted(results, key=lambda r: r["match_score"], reverse=True)

def _safe_source_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        return False
    host = parsed.hostname.lower().rstrip(".")
    if not any(host == domain or host.endswith("." + domain) for domain in DATA_DOMAINS):
        return False
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        return bool(addresses) and all(ipaddress.ip_address(address).is_global for address in addresses)
    except (OSError, ValueError):
        return False

def _scraped_links(page_url: str) -> list[str]:
    if not FIRECRAWL_API_KEY:
        return []
    response = httpx.post(
        "https://api.firecrawl.dev/v2/scrape",
        headers={"Authorization": f"Bearer {FIRECRAWL_API_KEY}"},
        json={"url": page_url, "formats": ["links", "markdown"], "onlyMainContent": True},
        timeout=45.0,
    )
    response.raise_for_status()
    body = response.json().get("data", {})
    links = body.get("links", [])
    collected = [urljoin(page_url, str(link)) for link in links if isinstance(link, str)] if isinstance(links, list) else []
    markdown = body.get("markdown", "")
    if isinstance(markdown, str):
        collected.extend(re.findall(r"\]\((https?://[^)\s]+)\)", markdown))
    return list(dict.fromkeys(collected))

def _raw_download_url(url: str) -> str:
    parsed = urlparse(url)
    parts = parsed.path.split("/")
    if parsed.hostname == "github.com" and len(parts) > 5 and parts[3] == "blob":
        return f"https://raw.githubusercontent.com/{parts[1]}/{parts[2]}/{'/'.join(parts[4:])}"
    return url

def acquire_dataset(source_url: str, objective: str = "", max_bytes: int = 25 * 1024 * 1024) -> tuple[pd.DataFrame, str]:
    """Scrape a selected public dataset page, then fetch only an allowlisted tabular file."""
    if not _safe_source_url(source_url):
        raise ValueError("This source host is not on Fetch's public dataset allowlist.")
    candidates = [source_url] if urlparse(source_url).path.lower().endswith(tuple(TABULAR_EXTENSIONS)) else []
    if not candidates:
        links = _scraped_links(source_url)
        parsed_source = urlparse(source_url)
        if parsed_source.hostname == "github.com":
            folders = [link for link in links if urlparse(link).hostname == "github.com" and "/tree/" in urlparse(link).path]
            for folder in folders[:3]:
                links.extend(_scraped_links(folder))
        if parsed_source.hostname and parsed_source.hostname.endswith("ourworldindata.org"):
            terms = set(re.findall(r"[a-z0-9]{3,}", objective.lower())) - STOP_WORDS
            charts = [link for link in links if "/grapher/" in urlparse(link).path and not urlparse(link).path.lower().endswith(".png")]
            ranked_charts = []
            for chart in charts:
                slug_terms = set(re.findall(r"[a-z0-9]{3,}", urlparse(chart).path.lower()))
                overlap = terms & slug_terms
                if len(overlap) >= min(2, len(terms)):
                    objective_focus = overlap & {"prevalence", "incidence", "infection", "infections", "death", "deaths", "mortality", "cases", "churn", "revenue", "price", "cost"}
                    strata = {"gender", "men", "women", "male", "males", "female", "females", "age", "ages", "children", "young"}
                    unrequested_strata = (slug_terms & strata) - terms
                    ranked_charts.append((len(overlap) + 5 * len(objective_focus) - 6 * len(unrequested_strata), chart.rstrip("/") + ".csv"))
            links.extend(url for _score, url in sorted(ranked_charts, reverse=True))
        candidates = [_raw_download_url(link) for link in links if urlparse(link).path.lower().endswith(tuple(TABULAR_EXTENSIONS))]
        candidates = [link for link in candidates if _safe_source_url(link)]
    if not candidates:
        raise ValueError("This listing did not expose a directly downloadable CSV, JSON, XLSX, or TXT file. Download it from the source site and upload it to Fetch instead.")
    last_error = None
    for candidate in candidates[:5]:
        url = candidate
        try:
            with httpx.Client(timeout=35.0, follow_redirects=False) as client:
                for _ in range(6):
                    if not _safe_source_url(url):
                        raise ValueError("The file link redirected outside Fetch's public dataset allowlist.")
                    with client.stream("GET", url, headers={"User-Agent": "FetchDatasetDemo/1.0"}) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise ValueError("The dataset host returned an invalid redirect.")
                            url = urljoin(url, location)
                            continue
                        response.raise_for_status()
                        length = int(response.headers.get("content-length", "0") or 0)
                        if length > max_bytes:
                            raise ValueError("The selected dataset exceeds Fetch's 25 MB import limit.")
                        output = bytearray()
                        for chunk in response.iter_bytes():
                            output.extend(chunk)
                            if len(output) > max_bytes:
                                raise ValueError("The selected dataset exceeds Fetch's 25 MB import limit.")
                        extension = next((ext for ext in TABULAR_EXTENSIONS if urlparse(url).path.lower().endswith(ext)), ".csv")
                        stream = io.BytesIO(output)
                        if extension == ".csv": df = pd.read_csv(stream)
                        elif extension in {".jsonl", ".ndjson"}: df = pd.read_json(stream, lines=True)
                        elif extension == ".json": df = pd.read_json(stream)
                        elif extension == ".xlsx": df = pd.read_excel(stream, engine="openpyxl")
                        else: df = pd.read_csv(stream, sep=None, engine="python")
                        if df.empty or len(df.columns) == 0 or len(df.columns) > 500:
                            raise ValueError("The downloaded file is not a usable tabular dataset.")
                        return df, url
                raise ValueError("The source used too many redirects to reach the dataset file.")
        except (httpx.HTTPError, ValueError, OSError) as exc:
            last_error = exc
    if isinstance(last_error, ValueError):
        raise last_error
    raise ValueError("Fetch could not retrieve a public tabular file from this listing. Download it manually and upload it instead.") from last_error

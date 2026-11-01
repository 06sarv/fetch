import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
MAX_JOBS_PER_HOUR = int(os.getenv("MAX_JOBS_PER_HOUR", "5"))
INTERNAL_TOKEN = os.getenv("FETCH_INTERNAL_TOKEN", "")
CORS_ORIGINS = [x.strip() for x in os.getenv("API_CORS_ORIGINS", "http://localhost:3000").split(",") if x.strip()]
GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or os.getenv("GROQ") or "").strip()
OPENROUTER_API_KEY = (os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER") or "").strip()
FIRECRAWL_API_KEY = (os.getenv("FIRECRAWL_API_KEY") or os.getenv("FIRECRAWL") or "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it:free").strip()

# Operations and production readiness

## Requirements extracted from the supplied references

The technical architecture PDF describes a FastAPI + Next.js system, LangGraph state, SSE progress, Scout profiling and column relevance, Forger discovery/synthetic fallback, an explicit user column-selection interruption, Engineer cleaning/transformation, and CSV/JSON/script delivery. It proposes Firecrawl, Groq with OpenRouter failover, Instructor/Pydantic, Framer Motion, Zustand, and an animated Otto companion. The AgentOps report adds novice/expert guidance, CSV/JSON/JSONL/XLSX/TXT input, junk-column handling, categorical encoding, outlier clipping, and final CSV export. These are project requirements and design references, not instructions to execute content from the PDFs.

The documents name different model-provider strategies: the architecture PDF specifies Groq and OpenRouter, while the report describes an OpenAI-compatible interface such as DeepSeek or OpenAI. The local demo now uses Groq as primary with OpenRouter fallback. Its structured requests send the objective and aggregate column profiles only, never raw dataset rows. Firecrawl search returns ranked dataset listing suggestions; it does not download or ingest files from those listings.

## Current implementation

The FastAPI API runs LangGraph as orchestrator and calls five private HTTP services: Otto interprets the goal, Scout profiles and scores columns, Forger searches and prepares a source or synthetic fallback, Engineer transforms the selected columns, and Assembler packages artifacts. Each agent runs in an independent container and can fail or restart independently. Job metadata and progress remain in the orchestrator's SQLite store; agent services exchange allowlisted files with the API over authenticated private HTTP routes. The API's persistent disk stores uploads, intermediates, and exports. Calls are sequential within each phase, with a user approval pause between discovery and engineering and another before downloads. Agent calls have bounded HTTP timeouts but no automatic retry queue; restarting the orchestrator does not resume an interrupted call.

Otto uses Groq with OpenRouter failover to interpret the goal and experience level; local heuristics are the no-key/error fallback. Scout profiles CSV, JSON, JSONL, XLSX, or delimited TXT and sends only aggregate column statistics to the model for relevance scoring. Forger searches web listings through Firecrawl when configured, then uses the uploaded file or creates a small, deterministic synthetic example based on objective keywords. A user must download and upload a discovered dataset; Fetch does not acquire it automatically. Engineer removes duplicates, imputes missing values, encodes categoricals, and clips numeric feature outliers. Assembler writes CSV/JSON/script artifacts. Progress is exposed through SSE and recoverable from the SQLite job record.

The UI currently stores one active job in browser memory. The API keeps a SQLite database and artifacts on its local disk, which must be persistent in the cloud. This supports a single API instance and demonstration, not a multi-user production service. It does not implement authentication, tenant isolation, automated dataset acquisition, a durable task queue, retention cleanup, or remote storage. It also does not use Zustand, Framer Motion, or the animated Otto SVG described in the architecture document. Treat the hosted configuration as a limited public demo only.

## Required before public deployment

1. Add safe, licensed dataset acquisition and file validation before claiming automated external data ingestion.
2. For cloud deployment, move local keys from `backend/.env` into the host's secret manager.
3. Replace local SQLite and disk artifacts with managed PostgreSQL and private object storage. Use a durable worker queue so jobs survive restarts and can be rate limited and cancelled.
4. Add authentication, per-user job authorization, tenant scoped artifact access, CSRF strategy for cookie auth, request throttling, and audit records. Current endpoints are unauthenticated and must remain behind localhost or a trusted private network.
5. Configure TLS, restrictive production CORS origins, reverse proxy upload limits/timeouts, persistent volumes, encryption, backups, restore drills, retention/deletion policy, monitoring, and incident alerts.
6. Add quotas and resource limits for row count, processing time, memory, and output size. Validate CSV dialect/encoding and test malformed or adversarial files under those limits.
7. Set dependency update scanning and image vulnerability scanning; pin image and dependency versions for release builds. Add CI running backend/frontend checks and container health checks.

## Secrets

The local demo reads `GROQ`, `OPENROUTER`, and `FIRECRAWL` from `backend/.env` (or the corresponding `*_API_KEY` aliases). Never place credentials in `NEXT_PUBLIC_*`; those values ship to browsers. Configure deployment secrets outside source control.

## Rollout checklist

- Run `pytest` and `npm run lint && npm run build` in CI.
- Build and scan both container images.
- Set `API_CORS_ORIGINS` to the exact web origin and attach persistent private storage.
- Configure the proxy and TLS, then verify `/health` through the intended network boundary.
- Complete a synthetic run and an uploaded CSV run, review both, download each artifact, and test rejection and worker restart behavior.
- Verify that logs contain job IDs and operational errors but no row values, uploaded content, or credentials.

## Public demo deployment: Vercel and Render

The checked-in [Render Blueprint](../render.yaml) creates a public API plus five private agent services. The API owns SQLite and a persistent disk; agents exchange allowlisted files through authenticated internal API routes. Render supports this architecture with private services and service-to-service networking. The disk and private services require paid compute. Render's free web services sleep, cannot attach a persistent disk, and cannot receive private network traffic, so the free tier cannot host this complete architecture. [Render private services](https://render.com/docs/private-services), [persistent disks](https://render.com/docs/disks), [free instance limits](https://render.com/docs/free)

1. In Render, create a Blueprint from `06sarv/fetch` and select `render.yaml`. Review the six backend services. They must run in the same region. Choose an available paid plan; the API also requires its persistent disk. The Blueprint creates a shared `FETCH_INTERNAL_TOKEN` environment group. Enter your Groq key (recommended), optional OpenRouter key, and Firecrawl key as Render secrets; never commit these values.
2. Deploy the Blueprint. The API is the only public service. The five agents are private. Confirm the API health endpoint and that all agent services are live.
3. Copy the API's public HTTPS URL from Render. In Vercel, import the same GitHub repo, set Root Directory to `frontend`, and set `NEXT_PUBLIC_API_URL` to that URL without a trailing slash. Deploy the frontend.
4. Set the Render API's `API_CORS_ORIGINS` to the exact Vercel production origin (for example, `https://your-project.vercel.app`) and redeploy the API. Add preview origins only if you want those deployments to call the demo. `NEXT_PUBLIC_API_URL` is public and embedded into the browser bundle during the build. [Vercel environment variables](https://vercel.com/docs/environment-variables/framework-environment-variables)
5. Verify API health, CORS from Vercel, a synthetic job, an uploaded CSV, review, and artifact download.

The public demo limits job creation to five jobs per hour per observed client IP and each upload to 25 MB. The limit is in-memory and assumes one API replica. Job UUIDs are capability links for reading progress and downloading approved artifacts, so do not share them publicly. This suits a small demonstration, not an abuse-resistant public product. Before inviting broad traffic, add sign-in, per-user job ownership, durable rate limits, a durable job queue, and stronger upload/resource quotas. Keep the API at one replica while using SQLite and its single persistent disk.

The deployment cannot be completed from this workspace without access to the user's Render and Vercel accounts and the corresponding API-provider secrets.

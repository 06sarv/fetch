# Fetch

Fetch prepares a dataset from an ML objective and an optional uploaded file. It profiles columns, suggests relevant features, cleans and transforms the selected data, and provides previewable exports after human review.

## Run locally

Install Docker Desktop, then run:

```sh
cp .env.example backend/.env
```

Add your provider keys to `backend/.env`, then start Fetch:

```sh
docker compose up --build
```

Open the app at http://localhost:3002. The API health check is http://localhost:8001/health. Stop the app with `docker compose down`.

Upload `examples/crappy_student_data.csv` to try preprocessing. It contains missing values, duplicate rows, inconsistent category labels, outliers, invalid ranges, and a missing target.

## Workflow

Enter an ML objective, optionally add details in the guided conversation, and upload a dataset or use the synthetic demo fallback. Otto interprets the goal. Scout profiles the data. Forger searches public dataset listings through Firecrawl. Search results are suggestions; download a result and upload it to process it. Engineer cleans selected columns. Assembler prepares CSV, JSON, and a reproducible Python script. Review the preview and approve before downloading.

The API is the LangGraph orchestrator. Otto, Scout, Forger, Engineer, and Assembler each run in their own FastAPI container and are called over the private Docker network. The agents exchange structured job state over HTTP and pass tabular data through the shared private job volume. The workflow remains sequential around the user's column selection and review. LLM calls receive the objective and aggregate column summaries; raw uploaded rows stay in the shared local job volume.

## Configuration

Set `GROQ`, `OPENROUTER`, and `FIRECRAWL` in `backend/.env`. Keys remain on the backend. `.env.example` lists the supported settings. Do not commit `.env` files.

The API accepts CSV, JSON, JSONL, XLSX, and delimited TXT files up to 25 MB. It processes uploaded tables in memory using Pandas. A 1,000-row table is within the intended demo size; actual capacity depends on row width, file size, available memory, and column count.

## Deploy frontend and backend

The current Docker Compose setup is for local use. A public deployment needs a frontend host and a separately reachable backend host.

1. Push the source to a private or public Git repository. Keep `backend/.env`, local job data, and uploaded files out of Git.
2. Deploy the FastAPI service from `backend/Dockerfile`. Configure `GROQ`, `OPENROUTER`, and `FIRECRAWL` as backend environment secrets, set a persistent `DATA_DIR`, and set `API_CORS_ORIGINS` to the exact frontend origin.
3. Deploy the Next.js service from `frontend/Dockerfile`. Set the build argument and runtime variable `NEXT_PUBLIC_API_URL` to the public HTTPS API URL. This value is compiled into the browser bundle at build time.
4. Configure HTTPS and verify `/health`, job creation, SSE progress, dataset upload, review, and artifact downloads.

The current SQLite and local filesystem storage are for a single instance. Before using the app with public users or multiple instances, add authentication and per-user authorization, managed database and object storage, a durable worker queue, request limits, backups, and monitoring. Never expose the current unauthenticated API publicly as-is.

## Development checks

Backend tests:

```sh
cd backend
pytest
```

Frontend lint and build:

```sh
cd frontend
npm run lint
npm run build
```

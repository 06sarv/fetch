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

The API is the LangGraph orchestrator. Otto, Scout, Forger, Engineer, and Assembler each run in their own FastAPI container and are called over a private network. Agents exchange structured state over HTTP and retrieve intermediate files through authenticated internal API routes. The workflow remains sequential around column selection and review. LLM calls receive the objective and aggregate column summaries; uploaded rows are processed by the services and are not sent to LLM providers.

## Configuration

Set `GROQ`, `OPENROUTER`, and `FIRECRAWL` in `backend/.env`. Keys remain on the backend. `.env.example` lists the supported settings. Do not commit `.env` files.

The API accepts CSV, JSON, JSONL, XLSX, and delimited TXT files up to 25 MB. It processes uploaded tables in memory using Pandas. A 1,000-row table is within the intended demo size; actual capacity depends on row width, file size, available memory, and column count.

## Deploy frontend and backend

The no-cost demo runs the full Docker Compose stack on your Mac and shares it with invited testers through a temporary Cloudflare Quick Tunnel. Your Mac and Docker must stay running, and the temporary URL changes when the tunnel restarts. See [docs/operations.md](docs/operations.md) for setup.

The separate Render deployment in [render.yaml](render.yaml) requires paid services and a persistent disk. Keep `backend/.env`, local job data, and uploaded files out of Git. Never put provider credentials in `NEXT_PUBLIC_*` values.

The public demo has a per-IP job limit and treats unguessable job IDs as capability links. It does not have user accounts or per-user ownership. Keep the audience small and do not put sensitive datasets in it. Add sign-in and per-user authorization before using it as a public product.

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

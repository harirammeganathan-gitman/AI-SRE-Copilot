# AI SRE Copilot

AI SRE Copilot is an incident investigation and root-cause analysis platform.
The platform includes document ingestion/indexing, RAG retrieval, cited RCA
generation, and a Streamlit investigation UI. PostgreSQL stores the source
documents, chunks, and RCA reports; ChromaDB performs similarity search over
Ollama `nomic-embed-text` embeddings. RCA generation uses Ollama `llama3.1`.

## Development setup

Requirements: Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

The liveness endpoint is available at `/health/live`. Readiness reports
PostgreSQL, ChromaDB, and both configured Ollama models.

## Windows quick start

From the repository root, double-click `start-ai-sre-copilot.bat` or run it
from Command Prompt. It starts the existing PostgreSQL container if needed,
starts Ollama and ChromaDB only when their health endpoints are unavailable,
then starts FastAPI on port 8000 and Streamlit on port 8502. Each launched
service gets its own window for logs. The script does not apply database
migrations; apply migrations 001–004 once before the first API startup.

Run `restart-ai-sre-copilot.bat` to restart only this project's FastAPI,
ChromaDB, and Streamlit processes. It leaves the PostgreSQL container and
Ollama running, and does not delete database or vector data. The restart script
only stops listeners whose process command line belongs to this repository.
Both scripts require Docker Desktop, the repository `.venv`, and `curl.exe`.

## Log ingestion boundary

Create/list incidents with `POST /api/v1/incidents` and
`GET /api/v1/incidents`. The human-facing `incident_number` (for example
`INC-1001`) is unique; APIs use the returned UUID as `incident_id`.
The versioned `POST /api/v1/log-batches` endpoint requires that UUID as a
multipart field along with a `file` (`.log`, `.txt`, or `.md`) and `source_type`.
Files must be UTF-8 and are bounded by `AI_SRE_MAX_UPLOAD_SIZE_BYTES`. Content
is newline-normalized and Unicode NFC-normalized; the SHA-256 is calculated
from the original bytes.
Chunks contain at most 1,000 Unicode code points with 150 code points of
overlap. Chunk metadata line numbers are zero-based and inclusive.

PostgreSQL is the source of truth. Apply these migrations in order before
starting the API:

1. `database/postgres/migrations/001_document_ingestion.sql`
2. `database/postgres/migrations/002_rca_reports.sql`
3. `database/postgres/migrations/003_incidents.sql`
4. `database/postgres/migrations/004_rca_quality.sql`

The incident migration creates the incident registry and foreign keys from
documents and RCA reports. Chunks are associated through their parent document;
Chroma metadata carries a derived incident UUID for efficient filtering.
Document deduplication is scoped to an incident, so identical content can be
ingested once per incident without inheriting another incident's association.
Chunks are committed before embedding/indexing; failed indexing can be retried
from persisted chunks. Deterministic chunk IDs and ChromaDB upserts make retries
idempotent.

Set `AI_SRE_DATABASE_URL`, `AI_SRE_OLLAMA_BASE_URL`, and `AI_SRE_CHROMA_URL` to
enable ingestion and indexing. Ollama must have the configured embedding model
available (default: `nomic-embed-text`). ChromaDB uses the
`incident_knowledge_base` collection. If any required dependency is not
configured, the ingestion endpoint reports that the service is unavailable
instead of storing data ephemerally.

## RAG retrieval

`POST /api/v1/rag/search` embeds the query, retrieves up to
`AI_SRE_TOP_K_RETRIEVE` candidates (default 15), reranks them using lexical
overlap combined with cosine similarity, and returns at most
`AI_SRE_TOP_K_RERANK` cited chunks (default 5) with a source-labeled context.
The endpoint preserves source filename and zero-based inclusive line ranges.
Document type, incident, service, environment, and observed-time filters are
applied by ChromaDB when supplied. Incident-filtered vector hits are also
verified against the PostgreSQL document-to-incident relationship before their
text is used as context. The reranker is intentionally lightweight;
evaluate it against incident-specific relevance judgments before relying on it
for operational decisions.

## RCA generation

`POST /api/v1/incidents/{incident_id}/rca` accepts `{"question":"..."}`.
The service retrieves evidence for the incident, prompts Ollama using the
configured chat model (`AI_SRE_OLLAMA_CHAT_MODEL`, default `llama3.1`), validates
the response against a strict JSON schema, and maps model-provided source IDs
back to the retrieved citations. Unknown or fabricated source IDs are rejected
and retried; if retrieval returns no sources, the service returns a
low-confidence report that explicitly states the evidence is insufficient
without calling the model.

The RCA prompt distinguishes symptoms, underlying causes, impact, and
consequences, and favors the deepest cause supported by evidence. When the
evidence does not establish a cause, it instructs the model to set confidence
at or below 0.30 and identify the missing evidence in `limitations`. Above 0.70,
the prompt requires an affirmative root-cause statement while still allowing
limitations about other unsupported details.

The RCA validator runs before report persistence. Evidence claims must quote a
cited retrieved source; the cited chunk must exist in the retrieval result.
Claims are categorized as root cause, symptom, impact, consequence, or severity.
Contradictory confidence/limitation statements, unsupported summaries, and
misclassified database timeouts, service unavailability, or request failures
are rejected and retried.

Severity is assigned by a deterministic classifier, not accepted from the LLM:
production-wide outages and multiple unavailable services map to P1; service
unavailability, request failures, database timeouts, connection-pool exhaustion,
and CrashLoopBackOff map to P2; high/network latency, partial degradation, and
memory leaks map to P3; warnings without failure indicators map to P4. A
determined root cause must have a known severity, actionable recommendations,
and preventive actions. These actions are generated from the supported
root-cause pattern and are suggestions, not claims that remediation has already
occurred.

After the configured three failed generation/validation attempts, the service
returns and persists an explicitly marked degraded report when it can extract
cited evidence deterministically. Its limitations identify the fallback and
remaining unknowns. Each report stores a 0–100 consistency score based on root
cause validity, citation support, recommendation quality, preventive-action
quality, and severity assignment (25, 25, 15, 15, and 20 points respectively).
Legacy rows receive a score of zero until regenerated.

Example:

| | Before (invalid when the source shows pool exhaustion) | After (source-supported) |
| --- | --- | --- |
| Root cause | Database timeout | Connection pool exhaustion |
| Symptom | — | Database timeout |
| Impact | — | Service unavailable |
| Consequence | Request processing failed, reported as the root cause | Request processing failed |

If the evidence only shows a database timeout and does not establish pool
exhaustion, the RCA must instead say the cause is undetermined, use confidence
at or below 0.30, and state what evidence is missing.

`AI_SRE_RCA_TIMEOUT_SECONDS`, `AI_SRE_RCA_MAX_ATTEMPTS`, and
`AI_SRE_RCA_RETRY_BACKOFF_SECONDS` configure model timeout and bounded retries.
The Streamlit RCA request timeout is derived from these settings to cover the
retrieval embedding call, ChromaDB query, incident/chunk/report database
operations, all model attempts, retry backoff, and processing overhead;
connection establishment remains capped at five seconds.
Ollama must have both configured models available for readiness and operation.
Successful RCA reports are persisted with an incident-scoped version number.
The report history API provides cursor pagination through
`GET /api/v1/rca-reports`; use `GET /api/v1/rca-reports/{report_id}` for report
details. The dashboard aggregates document, chunk, and report counts at
`GET /api/v1/dashboard/summary`.

## Streamlit user interface

Start the UI from the repository root in a second terminal:

```powershell
streamlit run frontend/app.py --server.port 8502
```

Configure `AI_SRE_API_BASE_URL` (default `http://localhost:8000`) to point the
UI at the API. The Dashboard shows PostgreSQL-backed counts and dependency
readiness. Upload Logs accepts `.log`, `.txt`, and `.md` files; its upload
history is scoped to the current Streamlit browser session. Incident
Investigation exposes RAG search and cited RCA generation. RCA Reports browses
the persisted report history, assessment and consistency score, organized
findings, deduplicated citations, actions, limitations, and JSON downloads.
The live dependency monitor is available at
`http://localhost:8502/health` (or the configured Streamlit port); it refreshes FastAPI, PostgreSQL, ChromaDB, and
Ollama model readiness every 10 seconds.

## Validation

```powershell
pytest
ruff check .
mypy backend
```

## Live end-to-end validation

Start dependencies in this order: PostgreSQL, ChromaDB, and Ollama; apply
migrations 001–004; then start FastAPI and Streamlit. Ollama must have both
`nomic-embed-text` and `llama3.1` available. For the current local setup, use
the health URLs below:

| Service | Check |
| --- | --- |
| PostgreSQL | `docker exec ai-sre-copilot-postgres pg_isready -U ai_sre_copilot -d ai_sre_copilot` |
| ChromaDB | `http://localhost:8001/api/v2/heartbeat` |
| Ollama | `http://localhost:11434/api/tags` |
| FastAPI liveness | `http://localhost:8000/health/live` |
| FastAPI readiness | `http://localhost:8000/health/ready` |
| Streamlit monitor | `http://localhost:8502/health` |

Expected FastAPI readiness is HTTP 200 with `status: "ready"` and true checks
for PostgreSQL, ChromaDB, both Ollama models, ingestion, RAG, and RCA.

The automated live integration test creates two isolated test incidents,
uploads identical log bytes, validates PostgreSQL and Chroma ownership, checks
incident-filtered retrieval, generates/persists an RCA, and removes its test
records afterward. Run it only when the local services are available:

```powershell
$env:AI_SRE_RUN_LIVE_E2E = "1"
.\.venv\Scripts\python.exe -m pytest backend\tests\integration\test_live_end_to_end.py -q
```

The API workflow can also be exercised directly in PowerShell:

```powershell
$incident = Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/incidents `
  -ContentType "application/json" `
  -Body (@{ incident_number = "INC-1001"; title = "Database timeout"; severity = "P2" } | ConvertTo-Json)
$incidentId = $incident.incident_id

curl.exe -F "incident_id=$incidentId" -F "source_type=application" `
  -F "file=@database.log" http://localhost:8000/api/v1/log-batches

Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/rag/search `
  -ContentType "application/json" `
  -Body (@{ query = "Why did production fail?"; incident_id = $incidentId } | ConvertTo-Json)

Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/api/v1/incidents/$incidentId/rca" `
  -ContentType "application/json" `
  -Body (@{ question = "Why did production fail?" } | ConvertTo-Json)
```

Use an unused incident number when repeating this sample. The upload receipt
should return the same `incident_id`; RAG sources and the generated report
should identify only that incident.

1. Open Streamlit's **Upload Logs** page and create `INC-1001` (or another
   unique incident number).
2. Select that incident and upload a UTF-8 log containing a recognizable
   database failure, such as `database timeout; connection pool exhausted`.
   The receipt should include the incident UUID, document UUID, and chunk
   count. Re-uploading the same bytes to the same incident should say
   **Duplicate**; uploading those bytes to a different incident should create
   a separate incident-scoped document.
3. Run **Incident Investigation** for the selected incident. Search results
   should cite only that incident's documents. Generate an RCA and confirm the
   report shows the same incident and source chunk.
4. Open **RCA Reports**, filter by the incident, verify the report version and
   citations, then download its JSON.
5. Verify the PostgreSQL relationships and report versions:

```sql
SELECT i.incident_number, d.document_id, d.incident_id, COUNT(c.chunk_id) AS chunks
FROM incidents AS i
JOIN documents AS d ON d.incident_id = i.incident_id
LEFT JOIN document_chunks AS c ON c.document_id = d.document_id
WHERE i.incident_number = 'INC-1001'
GROUP BY i.incident_number, d.document_id, d.incident_id;

SELECT i.incident_number, r.report_id, r.version, r.incident_id
FROM incidents AS i
JOIN rca_reports AS r ON r.incident_id = i.incident_id
WHERE i.incident_number = 'INC-1001'
ORDER BY r.version;
```

Each document row should have the selected incident UUID, each chunk should
join through its parent document, and report versions should increase per
incident. Embeddings are stored in the `incident_knowledge_base` Chroma
collection; PostgreSQL remains authoritative for document ownership and chunk
text.

Troubleshooting:

- If readiness reports PostgreSQL unhealthy, confirm migrations 001–004 were
  applied and the API's database URL targets the expected database.
- If upload returns 404, select an incident from the API/UI list; API calls use
  the UUID, not the display key.
- If RAG returns no sources, confirm the Chroma collection has vectors and the
  upload receipt's incident matches the investigation selection. For vectors
  created before incident-aware ingestion, re-ingest/re-index after verifying
  their PostgreSQL incident association.
- If RCA reports no evidence, first validate RAG search for the same incident;
  RCA intentionally does not use evidence from other incidents.

Do not commit `.env` or credentials. Production secrets must be injected by the
deployment platform's secret manager.

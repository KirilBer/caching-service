# Caching Service

A FastAPI service that transforms two equally sized string lists, interleaves
their results, and persists both the transformation cache and generated payloads.
The sample transformer is Python's Unicode-aware `str.upper`.

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev --frozen
uv run uvicorn caching_service.api:app --host 127.0.0.1 --port 8000
```

Interactive API documentation: <http://127.0.0.1:8000/docs>.
By default, data lives under `./data`. Set `CACHE_DATA_DIR` to change it.
Creating the app is side-effect free; database initialization runs during startup.

## API

```bash
curl -sS http://127.0.0.1:8000/payload \
  -H 'Content-Type: application/json' \
  -d @examples/input.json
# {"id":"<64-character SHA-256 digest>"}

curl -sS http://127.0.0.1:8000/payload/<id>
# {"output":"FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"}
```

`POST /payload` returns HTTP 200 for both new and reused payloads.
`GET /payload/{id}` returns HTTP 404 for an unknown well-formed ID and HTTP 422
for an invalid ID. Invalid request bodies return HTTP 422 before transformations.
Lists must have the same length; only strings are accepted; extra fields are
rejected. Empty lists are valid and produce an empty output. Limits are 1,000
items per list and 10,000 characters per string. Strings are preserved exactly
before transformation: no stripping, lowercasing or implicit numeric coercion.

## CLI

The command uses Pydantic Settings' native CLI parser and model validation
([documentation](https://pydantic.dev/docs/validation/2.12/concepts/pydantic_settings/)).

```bash
uv run cache-cli --host http://127.0.0.1:8000 --repeat 3 --input examples/input.json
uv run cache-cli -r 2 -j '{"list_1":["a"],"list_2":["b"]}' -o results.jsonl
cat examples/input.json | uv run cache-cli -i - -o -
uv run cache-cli --help
```

- `--host`: HTTP/HTTPS server URL, default `http://127.0.0.1:8000`.
- `-r`, `--repeat`: number of POST → GET iterations, 1–10,000, default 1.
- `-i`, `--input`: UTF-8 JSON file, or `-` for stdin.
- `-j`, `--json`: inline JSON; mutually exclusive with `--input`.
- `-o`, `--output`: UTF-8 JSONL file, or `-` for stdout (default).
- `-h`, `--help`: help. The task assigns `-h` to both host and help;
  this implementation reserves it for help and uses `--host` for the URL.

Without an input option, the CLI reads stdin. Each successful iteration emits
`{"id":"...","output":"..."}` as one JSON line. It also checks that repeated
requests return the same ID. Exit codes: 0 success, 2 invalid input/arguments,
1 network, HTTP or filesystem failure. Partial output is retained if a later
iteration fails. HTTP calls have a 30-second timeout. Host URLs containing
credentials, query parameters or fragments are rejected.

## Persistence and concurrency

`storage.py` contains the SQLAlchemy models and transaction boundary:

1. Acquire SQLite's write reservation using `BEGIN IMMEDIATE` before checking
   the cache. This coordinates independent processes as well as threads.
2. Deduplicate input strings and load cached `(transformer version, source)`
   results in batches. Call the transformer only for misses, then persist them.
3. Interleave in the original order and join with `", "` as specified.
4. Hash the canonical JSON `[transformer_version, output]` with SHA-256.
   Identical output reuses the same payload ID, even if the input differs.
   Save the output in SQLite and commit the transaction.
5. Materialize `data/payloads/<id>.json` using a temporary file and atomic replace.
   Existing correct files are reused. GET reads the generated JSON file.

SQLite is the authoritative store; payload files are recoverable projections.
If the process stops after committing but before writing the file, retrying POST
or performing GET reconstructs it without transforming again. Modified or
missing files are also repaired. The database and files must share the same
persistent data directory.

The transformation namespace is `uppercase-v1`; bump it when transformer semantics
change. Original strings are cache keys, so `a` and `A` are transformed separately
once, while their identical resulting payloads share an ID. Since the public
output is a joined string, inputs with different comma boundaries can produce
the same output and therefore the same ID. SHA-256 collision risk is accepted.

The transformer must be deterministic and side-effect free. There is no TTL:
successful results remain reusable until their version changes. Failed transactions
roll back cache entries and payloads. A crash or failed transaction can repeat an
external call that completed before the commit; exactly-once external execution
would require upstream idempotency support.

SQLite WAL allows reads while a write transaction is active, but creation requests
are serialized. This is a deliberate shortcut for a small single-host service:
slow external transformations reduce write throughput. A busy database waits up
to 30 seconds, then returns 503 with `Retry-After: 1`. Other unexpected failures
return 500. For higher throughput, use PostgreSQL with per-key coordination and
an actual external-service timeout/retry policy. There is no authentication,
eviction or database migration framework in this assessment implementation.

## Docker

```bash
docker compose up --build -d
uv run cache-cli -i examples/input.json -r 3
docker compose down
```

The container runs as a non-root user. A named volume retains SQLite and JSON
files across restarts. Compose exposes the API on localhost. The image installs
only production dependencies from `uv.lock`. `docker compose down -v` deletes
the persistent data. Do not share this SQLite volume across multiple hosts.

## Validation

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests cover the sample output, input validation, empty/Unicode inputs, cache reuse
across payloads and application restarts, independent concurrent database engines,
rollback, interrupted file writes, output identity, CLI files/stdin/stdout, short
flags, help and exit codes. API integration tests use a temporary real SQLite
database. CLI HTTP unit tests use HTTPX MockTransport without an external server.

Local verification on Python 3.14.6: 26 tests passed, Ruff checks passed, and a
live Uvicorn smoke test completed three CLI POST → GET iterations with the same
ID and expected output. `docker compose config` validated the configuration.
The Docker image was not built or run locally because the Docker daemon was
unavailable. The installed Starlette emits a deprecation warning for HTTPX-based
TestClient; it does not affect the passing tests.

See [WALKTHROUGH.md](WALKTHROUGH.md) for an English recording outline and
[SUBMISSION.md](SUBMISSION.md) for the submission email template.

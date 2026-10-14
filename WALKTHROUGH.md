# English walkthrough outline (10–13 minutes)

Record your screen and camera in Loom or upload an unlisted YouTube video.
Use this as an outline, and explain the code in your own words. No video has
been recorded as part of this repository.

## 0:00–1:00 — Purpose and layout

“This service accepts two equal-length lists of strings. It transforms each string,
interleaves the results, and returns a persistent payload identifier. SQLite caches
the transformations, so future requests avoid repeated work. The code separates
input schemas, HTTP endpoints, persistence, and the CLI.”

Show `src/caching_service/`, `tests/`, the Dockerfile and the commit history.

## 1:00–2:30 — Startup and validation

Open `api.py` and `schemas.py`. Explain the application factory, lifespan startup
and engine disposal, `CACHE_DATA_DIR`, strict strings, equal-length validation,
the limits and why empty inputs are valid. Show the two response schemas.

## 2:30–6:00 — Follow one POST request through storage

Follow `create_payload` into `Store.create` in `storage.py`.
Use `{"list_1":["a","a"],"list_2":["b","a"]}` as the example.

Explain:

- Two distinct source strings require two calls, rather than four.
- `BEGIN IMMEDIATE` prevents separate workers from racing on the same cache miss.
- The composite transformation key includes a version to avoid stale semantics.
- Batched queries respect SQLite parameter limits.
- Interleaving preserves order and duplicates: `A, B, A, A`.
- The payload ID hashes the final output plus the transformer version.
- The transaction commits transformations and payload together.
- Failed transactions roll back; external calls themselves cannot be rolled back.

Point to the tables and their primary keys. Explain that a different request
with some shared strings transforms only newly encountered strings.

## 6:00–7:00 — Files and GET

Follow `read_payload` into `Store.read` and `materialize`.
Show the actual JSON file location. Explain atomic replacement and why SQLite
remains authoritative: a crash between commit and file creation is repaired
on retry or read. Unknown IDs return 404; malformed IDs return 422.

## 7:00–9:00 — CLI

Open `cli.py`. Explain Pydantic Settings parsing, aliases, URL validation,
mutually exclusive input sources, UTF-8 files, stdin and JSONL output.
Explain the deliberate `-h`/`--host` resolution and exit codes.

Optional live demonstration:

```bash
uv run uvicorn caching_service.api:app
# In another terminal:
uv run cache-cli -i examples/input.json -r 3
uv run cache-cli -j '{"list_1":["a"],"list_2":["b"]}' -o results.jsonl
uv run cache-cli --repeat 0
```

## 9:00–11:00 — Tests

Open the test files before running them. Explain the transformer spy and its
call counts, persisted cache after app restart, four independent SQLAlchemy
engines competing for identical misses, rollback and file-recovery tests.
Explain that CLI unit tests use MockTransport and the real API tests use SQLite.

```bash
uv run pytest -q
uv run ruff check .
```

## 11:00–13:00 — Deployment and tradeoffs

Walk through Dockerfile, `uv.lock` and `compose.yaml`: frozen dependencies,
non-root execution, persistent named volume and localhost binding.
Be clear that SQLite creation requests serialize and that a remote transformer
would need explicit timeouts and upstream idempotency for stronger retry guarantees.
Discuss cache growth, lack of TTL/authentication and why PostgreSQL would be the
next step for a service deployed across multiple hosts.

Before submitting, make sure you can change the transformer, explain content-based
IDs, and demonstrate why concurrent requests do not double-transform a cache miss.

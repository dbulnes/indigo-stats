# Backend & Code Health Optimization Plan

This document details optimization and cleanup tasks for the backend, database layer, background job scheduling, and test suite.

---

## 1. Background Jobs & Database Maintenance

### A. Remove Dead Writes to the `summaries` Table
- **Location**: [`backend/jobs.py`](file:///Users/davidbulnes/git/indigo-stats/backend/jobs.py#L100-L105)
- **Problem**: In `jobs.maintenance()`, the server executes:
  ```python
  for span in (3600, 86400):
      con.execute('''INSERT OR REPLACE INTO summaries
          SELECT ts/?*?, ?, COUNT(*),AVG(temperature),AVG(humidity),AVG(pm25),
          MIN(temperature),MAX(temperature),MIN(pm25),MAX(pm25)
          FROM readings WHERE ts>=? GROUP BY ts/?''', (span, span, span, now - 3 * 86400, span))
  ```
  The `summaries` table was created in migration `0001_initial.sql`, but is never queried anywhere in `app.py`, `jobs.py`, or the frontend. All history endpoints dynamically calculate averages on `readings` based on the selected `environment_mode`.
- **Fix**: Remove the hourly calculation and insertion to eliminate unnecessary SQLite disk I/O and CPU usage on homelabs.

### B. Clean Coroutine Inspection in Background Loop
- **Location**: [`backend/jobs.py`](file:///Users/davidbulnes/git/indigo-stats/backend/jobs.py#L125-L130)
- **Problem**: `jobs.loop` contains hardcoded string branching:
  ```python
  if name == 'maintenance': await asyncio.to_thread(task)
  else: await task()
  ```
- **Fix**: Inspect whether `task` is a coroutine function (`asyncio.iscoroutinefunction(task)`). If it is a coroutine, `await task()`; otherwise, run it in a worker thread via `await asyncio.to_thread(task)`.

### C. Optimize Repetitive Allocations in `jobs.weather()`
- **Location**: [`backend/jobs.py`](file:///Users/davidbulnes/git/indigo-stats/backend/jobs.py#L60-L85)
- **Problem**: For each timestamp `t` across 240 hourly and 10 daily iterations, `val = lambda ...` and `dval = lambda ...` are defined inline and allocate a new `[None] * len(...)` list whenever a key is looked up.
- **Fix**: Define column lookup helpers outside the loop and avoid repeated fallback list allocations.

---

## 2. Test Suite & Python 3.13/3.14 Hygiene

### A. Fix `ResourceWarning: unclosed database` in Unit Tests
- **Location**: [`backend/tests/test_core.py`](file:///Users/davidbulnes/git/indigo-stats/backend/tests/test_core.py#L110-L116) and [`test_jobs.py`](file:///Users/davidbulnes/git/indigo-stats/backend/tests/test_jobs.py)
- **Problem**: In Python `sqlite3`, using `with sqlite3.connect(...) as con:` only enters a transaction context manager; it does not close the underlying connection. Python 3.13 and 3.14 emit `ResourceWarning: unclosed database in <sqlite3.Connection object>`.
- **Fix**: Use `contextlib.closing(sqlite3.connect(...))` or explicitly call `con.close()` upon completion.

---

## 3. Architecture & CLI Decoupling

### A. Decouple `APP_VERSION` from `backend/app.py`
- **Location**: [`backend/backups.py`](file:///Users/davidbulnes/git/indigo-stats/backend/backups.py#L184) and [`backend/app.py`](file:///Users/davidbulnes/git/indigo-stats/backend/app.py#L38)
- **Problem**: In `backups.manifest()`, `from .app import APP_VERSION` is imported inside the function to avoid circular imports. This causes CLI management tools (`backend/manage.py`) or backup verification utilities to import `FastAPI`, `Starlette`, and the entire web stack.
- **Fix**: Store `APP_VERSION` in `backend/__init__.py` or `backend/version.py`, export it from `backend.app` for backwards compatibility, and update `scripts/bump-version.mjs`.

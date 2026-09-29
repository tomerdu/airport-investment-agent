# Phase 8.4 — Reproducibility and Session Testing

**Status:** complete, uncommitted, awaiting review
**Tests:** 564 passed, 0 failed, 0 skipped
**Clean-environment verification:** install → import → `uvicorn` → `/health` 200
**Scope honoured:** no analytical formula, scoring weight, model selection,
prompt architecture, API response contract or frontend behaviour changed.

Driven by `docs/agent-architecture-interview-guide.md` §I and §K.

---

## 1. `requirements.txt` — the fix and how it was verified

### The defect

`fastapi`, `uvicorn`, `anthropic` and `python-dotenv` were **commented out**
under the note *"commented until used"*. They were in fact imported by
`app/main.py`, `app/agent/orchestrator.py` and `app/agent/config.py`. The
versions in the comments were also stale — `anthropic` was written as `0.42.0`
against **1.8.0** installed, a major version apart.

**Reproduced before fixing.** Installing only the three previously-uncommented
lines into an empty virtual environment, then running the command at
`README.md:137`:

```
Successfully installed certifi charset-normalizer colorama et-xmlfile idna
iniconfig openpyxl packaging pluggy pytest requests urllib3

>>> from app.main import app
  File "...\backend\app\main.py", line 17, in <module>
    from fastapi import FastAPI, HTTPException
ModuleNotFoundError: No module named 'fastapi'
```

A fresh clone could not start the backend.

### How the version set was chosen

Not by copying `pip freeze`. The method was:

1. **Enumerate real imports.** Every `import`/`from` across `backend/`, filtered
   to third-party: `anthropic`, `dotenv`, `fastapi`, `openpyxl`, `pydantic`,
   `pytest`, `requests`. Plus `uvicorn`, which is invoked as a module rather than
   imported.
2. **Declare direct dependencies only.** `starlette`, `anyio`, `httpx`,
   `pydantic-core`, `certifi` and the rest are pip's to resolve. This is a
   requirements file, not a lock file, and the distinction is now stated in it.
3. **Add `pydantic` explicitly.** `app/main.py` imports `BaseModel` and `Field`
   directly, so relying on FastAPI's transitive pin would be wrong.
4. **Keep the `[standard]` extra on uvicorn.** `watchfiles`, `httptools`,
   `websockets`, `PyYAML` and `colorama` are all installed, and `--reload` in the
   module docstring needs `watchfiles`.
5. **Drop `httpx`.** No test imports `TestClient`; only my scratchpad
   verification scripts did. The clean install confirms the suite does not need
   it — pip resolved `httpx2`/`httpcore2` transitively for `anthropic` and never
   pulled `httpx` at all, whereas the dev venv has both from earlier
   experimentation.
6. **Verify the set together**, by clean install plus the full suite (below)
   rather than by assertion.

### The file now

```
fastapi==0.141.1            # app/main.py — routing and request validation
uvicorn[standard]==0.54.0   # ASGI server; `python -m uvicorn app.main:app`
pydantic==2.13.5            # imported directly by app/main.py for ChatRequest
anthropic==1.8.0            # Messages API client, used directly (no wrapper)
python-dotenv==1.2.3        # loads the gitignored .env for ANTHROPIC_API_KEY
requests==2.32.3            # ETL — HTTP with sane retry/session handling
openpyxl==3.1.5             # ETL — FAA enplanement workbooks (.xlsx)
pytest==8.3.4               # offline suite; conftest blocks live calls
```

### Clean-environment verification

Empty venv, Python 3.12.10, `pip 25.0.1`, nothing pre-installed:

| step | command | result |
|---|---|---|
| 1 | `python -m venv <empty>` | only `pip` present |
| 2 | `pip install -r requirements.txt` | **exit 0** — 36 packages resolved |
| 3 | `python -m pytest` | **549 passed in 14.82s** |
| 4 | `python -c "from app.main import app"` | imports; title reads back |
| 5 | `python -m uvicorn app.main:app --host 127.0.0.1 --port 8099` | *"Application startup complete"* |
| 6 | `GET /health` | **200** — `window=2025-05..2026-04 airports=401 cohort=399 aci_eligible=237 model=claude-sonnet-5` |

Step 3 ran the suite **against the clean venv**, not the development one, so the
549 figure is independent of anything left over in `backend/.venv`.

One environment note: the first attempt failed with
`ERROR: Could not install packages ... content_param.py` and a hint about
**Windows long-path support**, because I had put the test venv under a very long
scratchpad path and `anthropic`'s nested `types/beta/...` exceeded `MAX_PATH`.
Re-running under a short path succeeded. That is a property of the path I chose,
not of the requirements file — but it is worth knowing that a deep checkout
directory can break installation on Windows without long paths enabled.

---

## 2. Session tests — and the bug they found

`backend/tests/test_session_continuity.py`, **15 tests**, offline with a scripted
fake model. The four properties from the audit, plus the regressions below.

| group | tests |
|---|---|
| **Ordinal follow-up** | state block numbers the ranking (`1. HVN`, `2. BOS`); a `rank_airports` call harvests into `last_ranking` and `focus_airports`; end-to-end "why the second one?" — asserts the *second request's* system blocks contain `2. BOS`, then that the model's next tool call was `get_airport_profile(iata="BOS")` |
| **Comparison persistence** | compare LAX/SNA, two intervening turns on ANC and SFO, then assert focus has moved to `["SFO"]` while `last_comparison` is still `["LAX", "SNA"]` and reaches the model; focus and comparison are separate fields |
| **Session isolation** | two ids do not share message objects, prose, focus or `known_numbers`; a user-supplied figure (`1,500 miles`) stays in its own session's audit pool; `reset` replaces the object; an unknown id is *adopted*, not rejected |
| **Trim-boundary integrity** | over `MAX_HISTORY_TURNS + 8` tool-using turns, every retained `tool_use` id has a matching `tool_result` id after every turn; the turn budget is respected while message count stays well above it; the first retained message always starts a turn; a `tool_result` message is not counted as a turn start |

The isolation tests are the ones that matter most, because session id is the
**only** isolation boundary — there is no authentication.

### The bug

Three of the four groups failed on first run for one reason: `store.get(id)`
returned `None` although the session had clearly been created.

`SessionStore` defines `__len__` (`session.py:167`), so **an empty store is
falsy**. `Orchestrator.__init__` did:

```python
self.store = store or SessionStore()        # discards an empty store
```

A store is always empty at construction, so **the store a caller passed was
always thrown away**. `main.py:50-51` builds a store and hands it to the
orchestrator, so the running app held *two*:

| surface | before | after |
|---|---|---|
| `/health.active_sessions` | always **0** | reflects the real store |
| `GET /sessions/{id}` | always **404** | 200 with turns, focus, ranking |
| `DELETE /sessions/{id}` | reset a store nothing used | resets the live session |

Verified through `TestClient` with no LLM call: `active_sessions` 0 → 1 after a
session is created via `orchestrator.store`, `GET /sessions/demo` → 200 returning
`turns=3 focus=['BOS','LAX'] ranking=['HVN','BOS','BGR']`, and `DELETE` → 200
with `turns` back to 0.

**Fix** (`orchestrator.py:145-152`): `store if store is not None else ...`, same
for `engine`, with a comment recording why. `AnalyticsEngine` defines no `__len__`
so it was never affected, but the explicit form removes the trap.

Two regression tests pin it: an injected empty store is actually used, and
`main.orchestrator.store is main.store`.

Worth recording honestly: **my own test helper had the identical bug.**
`make_orch` wrote `store or SessionStore()` and swallowed the store before the
orchestrator ever saw it — which is why the first fix appeared not to work. The
pattern is genuinely easy to hit, which is the argument for the explicit form.

This was outside the four requested tasks. I fixed it because the session tests
cannot be written honestly against broken wiring — a test that worked around it
would have documented the bug as correct behaviour. It changes no formula, score,
prompt or response *shape*; it makes three existing fields report the truth.

---

## 3. Documentation corrections

| # | Location | Correction |
|---|---|---|
| 1 | `backend/requirements.txt` | The *"commented until used"* note is gone along with the defect it described (§1) |
| 2 | `docs/design-document.md` §8 | *"None computes anything"* → **"None computes an analytical value"**, with a paragraph distinguishing analysis from presentation: `compact_for_model` does round scores and assemble the UDEI `evidence`/`limits` blocks, and full precision goes to the frontend unchanged |
| 3 | `docs/phase-8.2b-aci-temporal-integration.md` §5 | **Supersession note added; original text preserved unedited.** It states that the `_auditable()` implementation described there no longer exists (Phase 8.3 replaced field-stripping with the `compact_for_model` projection), that the *finding* stands unchanged, and where current behaviour is documented |
| 4 | `docs/agent-architecture-interview-guide.md` | §E marked IMPLEMENTED; §I.1 struck through as fixed; §I.1b added for the falsy-store bug; §I.2 marked TESTED; §K rows 1–2 marked DONE |

Historical phase reports were not rewritten. The 8.2b note is additive and says
plainly which part is superseded and which part still holds.

---

## 4. Test results

```
564 passed in 15.20s
```

| | count |
|---|---:|
| Before this phase | 549 |
| `test_session_continuity.py` | +15 |
| **Total** | **564** |

Also run: **549 passed in the clean venv** before the session tests existed,
which is the figure that verifies the dependency fix.

No live API calls — `conftest.py` replaces the Anthropic client unless a test is
marked `live`, and `pytest.ini` adds `-m "not live"`.

### Regression checks

Unchanged and re-asserted by the existing suite: Σ TDPI 16146.9211, Σ ACI
10931.2699, UDEI bands 328 Weak / 70 Moderate / 1 Strong, divergence
classifications, the four exam scenarios, and the absence of any magnitude field.

---

## 5. Files changed

| file | change |
|---|---|
| `backend/requirements.txt` | **rewritten** — all runtime deps declared and pinned; direct-only policy stated; install commands documented |
| `backend/app/agent/orchestrator.py` | **one-line defect fix** — `store`/`engine` injected with `is not None` instead of `or`, with a comment explaining the falsy-store trap |
| `backend/tests/test_session_continuity.py` | **new** — 15 deterministic session tests |
| `docs/design-document.md` | §8 wording corrected (analysis vs presentation) |
| `docs/phase-8.2b-aci-temporal-integration.md` | supersession note added to §5; original text preserved |
| `docs/agent-architecture-interview-guide.md` | findings updated to reflect what is now fixed and tested |
| `docs/phase-8.4-reproducibility.md` | **new** — this document |

Untouched, as required: every file under `app/analytics/`, `app/agent/prompts.py`,
`app/agent/tools.py`, `app/agent/session.py`, `app/agent/config.py`, and the
entire frontend.

---

## 6. Remaining limitations

1. **`anthropic==1.8.0` is verified for import and for the offline suite, not for
   live calls.** The suite mocks the client, and this phase made no live API
   call. Module-level symbol use (`anthropic.Anthropic`, `NotFoundError`,
   `APIStatusError`, `messages.create`, `models.retrieve`, `models.list`) is
   exercised at import; actual wire compatibility with the 1.x API was last
   demonstrated by the Phase 5 live smoke test, before this pin was written down.
   Re-running `python smoke_test.py --confirm` would close that gap.
2. **No lock file.** Transitive versions can drift between installs. A
   `pip-compile`-style lock would pin them, at the cost of a build step.
3. **Not tested on macOS or Linux.** The install and startup verification ran on
   Windows only.
4. **Windows long paths.** A deep checkout can break `pip install` on systems
   without long-path support, because of `anthropic`'s nested `types/beta/...`
   tree. Not something this repository can fix; worth a README note.
5. **Sessions remain in-process and unauthenticated.** Now *tested* and pinned,
   not fixed. A restart still loses every session, and an unknown id is still
   adopted rather than rejected.
6. **No token-based history budget.** Trimming still counts turns, so twelve
   tool-heavy turns carry far more context than twelve conversational ones.
7. **The session tests use a scripted fake model.** They verify the *mechanism* —
   what state reaches the model and what tool call follows — not that a real model
   interprets "the second one" correctly. That would need a live call.
8. **`/health.active_sessions` never decreases.** `SessionStore.reset` replaces a
   session rather than removing it, so the count reflects sessions ever created
   in this process. Pre-existing behaviour, now visible because the field finally
   reports the real store.

---

## 7. Review checklist

- [x] `requirements.txt` declares every runtime dependency the backend imports
- [x] Versions chosen from actual imports, verified together — not copied from `pip freeze`
- [x] Documented install and startup procedure tested in an **empty** virtual environment
- [x] Old file's failure reproduced, proving the fix was necessary
- [x] Four session test groups implemented (ordinal, comparison, isolation, trim)
- [x] Three documentation inconsistencies corrected; history preserved with a labelled supersession note
- [x] Full offline suite run — **564 passed**
- [x] No analytical formula, weight, model selection, prompt architecture, response contract or frontend change
- [x] **Not committed, not pushed**

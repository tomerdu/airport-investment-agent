# API Cost Control

Credits are limited, so the repository is arranged around one rule: **nothing
calls the Anthropic API unless you explicitly ask it to.**

---

## Which commands are guaranteed offline

These never construct an Anthropic client. Safe to run any number of times.

| Command | What it does |
|---|---|
| `pytest` | The full test suite — 328 tests |
| `pytest tests/test_cost_control.py` | The cost-control tests themselves |
| `python smoke_test.py --list` | Prints the manual checklist |
| `python smoke_test.py --dry-run` | Shows what *would* be sent |
| `python smoke_test.py` (no flags) | Refuses, and says so |
| `python report_checkpoint2.py` | Analytics deliverables from the warehouse |
| `python -m etl.build_warehouse` | Rebuilds the warehouse |
| `python -m app.agent.preflight` | Credential + `.gitignore` checks only (model lookup is behind `--live`) |
| `uvicorn app.main:app` | Starts the server. `/analytics/*`, `/health`, `/sources`, `/usage` make no API calls |
| `npm run build` / `npm run dev` | Frontend |

The offline guarantee is **enforced, not assumed**. `tests/conftest.py` replaces
`anthropic.Anthropic` and `anthropic.AsyncAnthropic` with a function that
raises `LiveApiCallBlocked`, so a test that forgets to inject a fake client
fails loudly instead of quietly billing. `pytest.ini` additionally deselects
`-m "not live"`.

## Which commands call the live API

| Command | Requests | Notes |
|---|---|---|
| `python -m app.agent.preflight --live` | 2 | A Models API lookup plus a generation capped at `max_tokens=16` (~20 tokens) |
| `python smoke_test.py --confirm` | 4–8 | One per question, plus at most one regeneration each |
| `python smoke_test.py --confirm --only N` | 1–2 | A single question |
| `python measure_tokens.py` | 8 `count_tokens` | Counting only — no generation, so no output-token charge |
| `python demo_phase3.py` | 8–16 | The full 8-turn demo |
| `POST /chat` (the browser UI) | 1–2 per message | Each chat message |
| `pytest -m live` | varies | No live tests exist today |

## Running the four mandatory questions once each

```bash
cd backend

python smoke_test.py --list       # review the checklist first (offline)
python smoke_test.py --dry-run    # confirm what will be sent (offline)
python smoke_test.py --confirm    # LIVE — 4 questions, one session
```

One question at a time:

```bash
python smoke_test.py --confirm --only 1   # New England
python smoke_test.py --confirm --only 2   # LAX vs SNA
python smoke_test.py --confirm --only 3   # Anchorage long-haul
python smoke_test.py --confirm --only 4   # SFO unmet demand
```

`--fresh` gives each question its own session (no conversational carry-over);
the default shares one session, which is what the follow-up behaviour needs.

Every run prints a per-question token count and a cumulative estimate at the
end.

---

## Retry policy

The SDK's own retry loop is **disabled** (`max_retries=0`); retries are decided
by `app/agent/retry.py`, which classifies each failure.

**Never retried** — a second attempt cannot succeed and costs another request:

| Failure | Status |
|---|---|
| Authentication / permission | 401, 403 |
| Billing — payment required | 402 |
| Billing — credit or quota exhausted | any 429 whose body mentions credit, quota, balance or spend limit |
| Malformed request, unknown model | 400, 404, 405, 422 |

**Retried, bounded** by `AGENT_MAX_RETRIES` (default **1**, so 2 attempts max):
408, 409, 5xx, and connection errors.

A credit-exhaustion 429 is the important case: it looks like an ordinary rate
limit, and retrying it burns attempts against an account that has nothing left.

## Usage logging

Every response's usage is recorded by `app/agent/usage.py`:

```
api-usage purpose=chat model=claude-sonnet-5 in=9928 out=812
          cache_read=8410 cache_write=0 est_cost=$0.0280
```

Recorded: token counts, model id, purpose, timestamp.
**Never recorded:** prompts, completions, message content, API keys, headers.

Read the cumulative total at any time:

```bash
curl http://127.0.0.1:8000/usage
curl -X POST http://127.0.0.1:8000/usage/reset
```

Token categories are reported separately — **input**, **output**, **cache read**
and **cache write** — because they bill at different rates (cache reads ~0.1×
input, cache writes ~1.25×).

### Costs are estimates

Computed from a local price table (Sonnet 5: $2.00 in / $10.00 out per MTok).
They exclude tier and batch discounts and drift when published prices change.
Override with `AGENT_PRICE_IN` / `AGENT_PRICE_OUT`. **The Anthropic console is
authoritative.**

## Cost per activity (estimates, Sonnet 5)

| Activity | Approx. cost |
|---|---|
| `preflight --live` | < $0.001 |
| One chat message | $0.02 – $0.05 |
| `smoke_test.py --confirm` (4 questions) | $0.10 – $0.20 |
| `demo_phase3.py` (8 turns) | $0.35 – $0.45 |
| `measure_tokens.py` | negligible (counting only) |

The Phase 4 payload optimisation cut input tokens ~83%, so these are already
far below where they started (the pre-optimisation 8-turn demo cost roughly
5× the current one).

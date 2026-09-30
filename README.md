# Airport Investment Intelligence Agent

A screening tool for US airport modernisation investment. Every number comes
from a deterministic Python analytics engine; Claude handles the conversation
and the explanation, and is structurally prevented from producing figures of its
own.

I built this for the Deloitte Digital Forward Deployed Engineer exercise. The
design in one line: **the LLM decides what to do and how to explain it;
deterministic code decides the numbers.**

**Design / Architecture: [DESIGN.md](DESIGN.md)** — the assignment's short
design document: scoring methodology, key tradeoffs, and where/how AI is used.
**Guided walkthrough: [DEMO.md](DEMO.md)** — the four assignment questions, and
the deterministic fallback when the LLM is unavailable.

---

## The business problem

An investment firm backs US airport modernisation and wants to find airports
where renovation would be most valuable. The honest answer is that
*profitability* cannot be derived from public data — it needs construction cost,
financing terms, concession revenue and use-and-lease agreements, none of which
are published.

So I scoped this as a **screening tool** rather than pretending otherwise. It
shortlists airports on measurable demand pressure and congestion, shows exactly
how each score was built, and is explicit about what it does not know. The
investment judgement stays with the analyst.

> **What this does not do.** It does not measure terminal capacity, does not
> identify the cause of congestion, and does not model profitability. TDPI and
> ACI are composite **proxy** indices scored against a peer cohort. Divergence
> classes are screening classifications, not recommendations.

## Capabilities

- **Rank** any airport cohort by demand pressure or congestion, with the full
  arithmetic exposed
- **Compare** airports, separating traffic *volume* from per-flight *intensity*
- **Long-haul analysis** with threshold sensitivity and an aircraft-configuration
  split (passenger / freighter / combi / amphibious)
- **Unmet-demand evidence** as an indicator table — never a fabricated number
- **Conversational follow-ups** — ordinal references, threshold changes, adding
  an airport to an existing comparison
- **Numeric provenance audit** — every figure in an answer is checked against
  what the engine actually returned
- **Voice (bonus)** — browser-based English speech-to-text input, with optional
  text-to-speech playback of an answer

## Architecture

```
React + TypeScript            FastAPI                  Claude (Sonnet 5)
┌────────────────┐      ┌──────────────────┐      ┌────────────────────┐
│ chat  │ panels │ ───► │  /chat           │ ───► │ 6 deterministic    │
│       │        │      │  /analytics/*    │      │ tools, compact view│
└────────────────┘      └────────┬─────────┘      └────────────────────┘
      ▲                          │                          │
      │  structured JSON         ▼                          ▼
      └──────────────  deterministic analytics  ◄──  numeric audit
                                 │
                        SQLite warehouse (committed)
                                 ▲
                        offline ETL  ◄──  BTS · FAA · OurAirports
```

I kept the arithmetic out of the model because an LLM producing an aviation
statistic is producing a *plausible-looking* number — it may be right, but
nothing makes it checkably right, and an unauditable figure carries unearned
authority in an investment screen. Three layers enforce that, weakest first:

1. Numbers exist only as tool return values.
2. Tool schemas make the failure impossible rather than discouraged —
   `long_haul_breakdown` returns a sensitivity table with no scalar field;
   `unmet_demand_evidence` has no magnitude field, so the model cannot report a
   quantity the schema does not contain.
3. A **numeric provenance audit** checks every numeral in the draft answer
   against what the engine actually returned. A mismatch triggers one
   regeneration, then a templated fallback rendered from tool data.

The frontend renders tables and scores from the structured response, never from
the model's prose, so a chart cannot disagree with the engine.

Scoring formulas, the tradeoffs behind them and the full AI boundary are in
**[DESIGN.md](DESIGN.md)** — start with its
executive summary.

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Analytics | Pure Python + SQLite (no numpy/pandas at runtime) |
| LLM | Claude Sonnet 5 via `anthropic` SDK, manual tool-calling loop |
| Frontend | React 19, TypeScript, Vite |
| ETL | `requests`, `openpyxl`, stdlib `csv` / `zipfile` / `sqlite3` |
| Tests | pytest — 593 backend tests passing, all offline |

## Data sources

All U.S. federal public domain, or an explicit public-domain dedication.

| Dataset | Source | Role |
|---|---|---|
| Traffic | BTS T-100 Segment Summary by Origin Airport (Socrata `r495-tyji`) | Passengers, seats, departures, load factor |
| Delay | BTS On-Time Performance | Taxi-out, NAS delay, delay rate, cancellations |
| Segments | BTS T-100 Segment (All Carriers) | Per-route distance → long-haul analysis |
| Enplanements | FAA Passenger Boarding | Official passenger counts, hub class |
| Reference | OurAirports | Runways, identifier crosswalk |

**Analysis window: 2025-05 … 2026-04 (12 months)**, pinned across every source.
I pinned it because the sources publish on different schedules — On-Time
Performance runs further ahead than the rest — and truncating everything to one
window is what stops an answer silently mixing vintages.

The warehouse is **built offline and committed** (`backend/app/data/warehouse.db`,
~60 MB), so there is nothing to download and the app runs with every upstream
source unavailable. That snapshot is also what makes scores reproducible: the
same question returns the same numbers on any machine, on any day.

---

## Installation

### Prerequisites

- **Python 3.12+**
- **Node 20+**
- An **Anthropic API key** — needed for chat only; `/analytics/*` works without it

### 1. Configure

```powershell
copy .env.example .env
```

Edit `.env` and set your key. `.env` is gitignored; `.env.example` holds
placeholders only.

```ini
ANTHROPIC_API_KEY=sk-ant-...        # your key
ANTHROPIC_MODEL=claude-sonnet-5     # optional
AGENT_MAX_RETRIES=1                 # optional, bounded retries
SOCRATA_APP_TOKEN=                  # optional, ETL only
```

### 2. Backend (Windows / PowerShell)

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verify:

```powershell
curl http://127.0.0.1:8000/health
```

**If port 8000 is taken**, run on 8001:

```powershell
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

and point the frontend at it by editing the proxy target in
`frontend/vite.config.ts`:

```ts
proxy: { '/api': { target: 'http://127.0.0.1:8001', changeOrigin: true, ... } }
```

Alternatively bypass the proxy with an env var — create `frontend/.env.local`:

```ini
VITE_API_BASE=http://127.0.0.1:8001
```

### 3. Frontend

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open **http://127.0.0.1:5173**.

---

## Example questions

The four assignment questions are one click away in the UI:

1. *Which airports in New England are strong candidates for terminal expansion?*
2. *Compare LA and Santa Ana airport congestion levels.*
3. *What is the percentage of long haul flights out of Anchorage airport?*
4. *What is the unmet flight demand in SFO airport and why?*

Then try a follow-up — *"Why is the second one ranked there?"*, *"What if we
used 1,500 miles as the long-haul threshold instead?"*, *"Now add Burbank to
that comparison."*

See **[DEMO.md](DEMO.md)** for a guided walkthrough.

## Voice interaction (bonus)

The assignment's optional bonus, on the browser's Web Speech APIs —
`SpeechRecognition` (webkit-prefixed in Chrome and Edge) for **speech-to-text
question input**, and `speechSynthesis` for **text-to-speech playback of an
answer**. English (`en-US`) only, set explicitly.

It is dictation and read-aloud around the existing chat — **not** a realtime or
full-duplex voice agent, and not speech-to-speech. No continuous listening, no
wake word.

- **Ask by voice** — click the microphone, speak, press Stop (Esc discards). The
  transcript lands in the composer to read and edit before you press Send, then
  takes the *same* `POST /chat` path as typed text. A transcript is ordinary
  untrusted input with no special authority — same validation, same narrow tools,
  same numeric audit, no voice-specific code path.
- **Read answer** — a button on each answer speaks the visible reply. One at a
  time, never automatically.

The backend is untouched: no endpoint, no dependency, no speech key, and it
**never receives or stores audio**. Recognition happens in the browser, which is
not the same as on the device — Chrome and Edge may use an OS or vendor
recognition service. Where either API is missing the control is disabled with a
short explanation and typing is unaffected.

## Testing

**Offline — guaranteed, costs nothing.** `tests/conftest.py` replaces the
Anthropic client constructors during tests, so a forgotten mock fails loudly
instead of billing.

```powershell
cd backend
.venv\Scripts\python -m pytest                # 593 tests
.venv\Scripts\python -m app.agent.preflight   # credential checks, no API call
.venv\Scripts\python smoke_test.py --list     # manual checklist
.venv\Scripts\python smoke_test.py --dry-run  # what a live run would send
```

```powershell
cd frontend
npm run build          # tsc typecheck + production build
npm run lint           # oxlint
npm run test:voice     # voice logic, Node's built-in runner, no dependencies
```

`test:voice` covers capability detection, final-only transcripts, error wording,
markdown-to-speech extraction and voice selection. It needs Node 24 (or Node 22
with `--experimental-strip-types`) since it imports TypeScript directly. The two
React hooks own only browser lifecycle and need a DOM to exercise; they are
covered by the voice checklist in **[DEMO.md](DEMO.md)**.

**Live — spends API credits.** Never run as part of the suite.

```powershell
.venv\Scripts\python smoke_test.py --confirm          # 4 questions, ~$0.10–0.20 est.
.venv\Scripts\python smoke_test.py --confirm --only 3 # one question
.venv\Scripts\python -m app.agent.preflight --live    # ~20 tokens
```

Token usage and estimated cost are logged per request and exposed at
`GET /usage`. Authentication, billing and malformed-request failures are never
retried. Detail: **[docs/api-cost-control.md](docs/api-cost-control.md)**.

## Known limitations

- **Terminal capacity is never measured.** No public dataset in use publishes
  gates, holdroom area, checkpoint lanes or baggage throughput. TDPI is demand
  *pressure*.
- **ACI identifies no cause.** It reflects observed delay outcomes, not runway
  or airspace capacity, and does not establish that a constraint is binding.
- **Profitability is not modelled.** No cost, financing or revenue data.
- **Unmet demand cannot be quantified** from the datasets used here.
- **On-Time Performance is domestic, reporting-carrier only** and excludes
  all-cargo carriers, so ACI under-observes international and freight operations.
- **All growth is trailing, never forecast** — the FAA Terminal Area Forecast
  bulk download was out of service when the data was assembled.
- **Scores are cohort-relative** percentile positions, not absolute statements.
- **Weights are reasoned judgement**, reported with every score, not derived
  from observed investment outcomes.
- **US airports only**; 401 FAA primary commercial service airports.
- **Voice input is English-only and browser-dependent.** Speech recognition
  requires Chrome or Edge on desktop; elsewhere the control is disabled and
  typing is the only input. Recognition accuracy is the browser's, not ours, and
  the browser may use an external recognition service.

## Repository structure

```
airport-investment-agent/
├── README.md               this file
├── DESIGN.md               the design/architecture deliverable
├── DEMO.md                 guided demo walkthrough
├── .env.example            configuration template (no secrets)
├── backend/
│   ├── app/
│   │   ├── analytics/      deterministic engine — no LLM
│   │   ├── agent/          orchestrator, tools, prompts, audit, usage, retry
│   │   ├── data/           warehouse.db (committed)
│   │   └── main.py         FastAPI
│   ├── etl/                reproducible pipeline + warehouse build
│   ├── tests/              593 offline tests
│   ├── smoke_test.py       the four questions, once each (live, gated)
│   ├── measure_tokens.py   deterministic token measurement
│   ├── report_checkpoint2.py  analytics deliverables (offline)
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/     chat, panels, primitives
│   │   ├── analytics types, api client, glossary, markdown renderer
│   │   ├── voice.ts        speech capability detection + text handling
│   │   ├── useVoice.ts     the two speech hooks
│   │   └── styles.css
│   └── tests/voice.test.ts voice logic (node --test)
└── docs/
    ├── api-cost-control.md     which commands cost money
    ├── scoring-proposal.md     TDPI / ACI / UDEI formula derivation
    ├── data-source-research.md source verification and licensing
    └── feasibility-matrix.md   what is measurable vs proxied
```

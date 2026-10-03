# IoU prototype

IoU is a Python-first neighbourhood mutual-credit demo. It uses FastAPI, server-rendered Jinja2 templates, SQLite, and a small amount of vanilla CSS/HTML.

## Run locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --reload
```

Open <http://127.0.0.1:8000>. The seeded demo accounts all use `demo123`:

- `alex@example.com`
- `sam@example.com`
- `moderator@example.com`

The prototype includes the request board, a dedicated skill-matched recommendations page, optional direct-exchange and three-person chain suggestions, requester-side offers with buffers, provider matching, two-sided value and buffer negotiation, overlapping-range settlement, visible recommendation guardrails with explanations and explicit confirmation for out-of-range offers, provider leave/reopen, side-to-move deadlines, pre-acceptance messaging, required provider proof upload, deterministic proof file screening with audit metadata, requester completion confirmation, a three-day post-completion payment hold, automatic background release processing and deadline reminders, configurable dispute eligibility and windows, multi-file dispute evidence, refund/redo/alternative-compensation resolution requests, user-to-user refund negotiation, deterministic mediation recommendations with accept/reject controls, moderator policy configuration and no/partial/full/frozen decisions, automatic refund ledger adjustments, and richer escrow/payment/Kudos/refund history. The recommendation engine infers effort and complexity from the written task details on the backend, then compares them with recent history. It is deterministic and local so a live demo does not require an external AI key.

When an agreed task is started, its credits move from the requester's available balance into held escrow. The provider must upload proof that passes the deterministic file checks and mark the task complete before the requester can confirm delivery. Credits remain held for three days after that confirmation, then release automatically unless an open dispute freezes them. The requester must remain at or above the single `-20` credit minimum when escrow is created.

Proof screening checks file type, size, MIME consistency, and duplicate content. It is an automated integrity check, not a claim that the system can reliably detect every downloaded or AI-generated image. Disputes support up to five evidence files, requested credit refunds, redo/fix requests, alternative compensation descriptions, provider counter-offers, deterministic mediation using task/complaint/chat/history signals, and human moderator escalation.

Completion requires at least one evidence file. After the requester confirms completion, the agreed credits remain held for three days. Due holds are released by the background worker (and also opportunistically on authenticated requests) unless an open dispute keeps the exchange frozen. After-completion disputes are accepted only while the configured dispute window is open and the task is eligible.

Users must have an available balance of at least `-20` credits to post a new request or start an agreed task if the escrow deduction would take them below `-20`. The favicon is served at `/favicon.ico`.

The exchange matching page (`/exchange-matches`) is advisory: it identifies two-person direct swaps and small closed chains using residents' declared skills and open requests. Participants still accept and complete each task independently through the normal safeguards. Credit differences are shown as a balancing amount rather than bypassing escrow.

## Seed recommendation history

To add realistic completed exchanges for the estimator:

```powershell
python seed_demo_data.py
```

The seeder is idempotent and creates 12 local demo transactions across errands, childcare, tech help, and home tasks. The recommendation endpoint uses the recent 90-day category median as its historical benchmark, then adjusts it for effort, complexity, and quality:

```text
recommended credits =
historical median
× effort factor
× complexity factor
× quality factor
```

Example:

```text
/estimate/tech%20help?effort_minutes=90&complexity=4&quality_score=4
```

The response includes the recommended value, allowed range, benchmark, adjustment factors, and sample size.

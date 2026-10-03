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

The prototype includes the request board, a dedicated skill-matched recommendations page, opt-in value-based task-chain invitations, requester-side offers with buffers, provider matching, two-sided value and buffer negotiation, overlapping-range agreement, visible recommendation guardrails with explanations and explicit confirmation for out-of-range offers, provider leave/reopen, side-to-move deadlines, pre-acceptance messaging, required provider proof upload, deterministic proof file screening with audit metadata, requester completion confirmation, a three-day protected-payment hold, automatic background release processing and deadline reminders, configurable problem-reporting eligibility and windows, multi-file supporting evidence, refund/redo/alternative-compensation resolution requests, user-to-user refund negotiation, deterministic fair-solution suggestions with accept/reject controls, moderator policy configuration and no/partial/full/frozen decisions, automatic balance adjustments, and richer payment/thank-you/refund history. The recommendation engine infers effort and complexity from the written task details on the backend, then compares them with recent history. It is deterministic and local so a live demo does not require an external AI key.

IoU also groups at least three open tasks in a similar credit range and invites their task owners to join a voluntary task chain. No skill declaration is required: each participant chooses whether to complete one other listed task and have their own task completed by someone else. A chain activates once at least three participants accept; declines do not cancel it. The invitation page includes the task list, value explanation, shared discussion, participant responses, and the active give/receive assignments. Each task then continues through the ordinary chat, agreement, protected payment, proof, completion, and problem-reporting safeguards.

When an agreed task is started, its credits move from the requester's available balance into a protected held balance. The provider must upload proof that passes the deterministic file checks and mark the task complete before the requester can confirm delivery. Credits remain held for three days after that confirmation, then release automatically unless an open problem report keeps them held. The requester must remain at or above the `-5` credit floor when the payment is set aside, and cannot start a new exchange while their balance is negative.

**Credit rules.** New members start with `+10` credits. A member can only post a request while their balance is positive, and a balance can never fall below `-5`.

Proof screening checks file type, size, MIME consistency, and duplicate content. It is an automated integrity check, not a claim that the system can reliably detect every downloaded or AI-generated image. Problem reports support up to five supporting files, requested credit refunds, redo/fix requests, alternative compensation descriptions, provider counter-offers, and escalation to a human moderator.

Completion requires at least one evidence file. After the requester confirms completion, the agreed credits remain held for three days. Due holds are released by the background worker unless an open dispute keeps the exchange frozen. After-completion disputes are accepted only while the configured dispute window is open and the task is eligible.

## Dispute flow

1. **Raise a dispute (`disputed`).** The requester submits a reason, evidence and a requested refund. The credits stay frozen in escrow.
2. **User-to-user resolution (3 days).** Both participants negotiate in the chat — refund, redo, or alternative compensation. If they agree, the ledger is updated automatically and the dispute closes.
3. **Fair Resolution (1 day).** If the three-day window closes without agreement, IoU proposes a *Fair Resolution* — a refund amount plus a transparent explanation that weighs the task description, agreed value, evidence, chat history and comparable past disputes. Both participants must accept it.
4. **Human moderation.** If either participant rejects the proposal, or nobody responds within a day, the case escalates to a community moderator who makes the final decision (no refund / partial / full / keep frozen). The ledger is updated automatically.

The stage timers are real (3 days + 1 day). For live demos, compress them:

```powershell
$env:IOWEU_FAST_DISPUTES = "6"   # 6-minute negotiation window, 2-minute Fair Resolution window
uvicorn app:app --reload
```

Escalations notify every moderator account, and the moderator queue lives on the dashboard. Set `IOWEU_SESSION_SECRET` before deploying; otherwise a random per-process secret is generated.


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

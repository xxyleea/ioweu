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

## Reliability score

Separate from credits. Every resident starts at **60/100**. It is not a financial credit score — it measures dependability on IoU.

| Band | Label | Effect |
| --- | --- | --- |
| 80–100 | Excellent | full access |
| 60–79 | Strong | full access |
| 30–59 | Limited | full access |
| 1–29 | Restricted | max task value 5 credits, warning shown, may appeal |
| 0 | Suspended | cannot post or accept; history, disputes, export and appeal remain |

Events are configurable in `RELIABILITY_EVENTS`: completed task `+1`, thumbs-up `+2`, five-task streak `+2`, mutually agreed cancellation `0`, late cancellation `−5`, no-show `−10`, serious failure `−10`, fraudulent evidence `−20`, broken Circle commitment `−10`. Every change is written to `reliability_events` with a reason, and moderators can restrict or suspend an account (an action that is always recorded). Restricted and suspended residents can submit an appeal from the home banner or Settings, and moderators resolve appeals on `/moderator/reports`. Reliability appears in the sidebar, on the home dashboard, on task cards and detail pages, and on the public profile.

## The Circle

A Circle is a reciprocal help loop: **no credits move**. When three or more open needs within roughly ±2 credits are detected, residents are invited to join a Circle. Each member picks which of the other tasks they would be comfortable helping with (never their own), all members must accept, and only then is the loop assigned. Circle tasks are tagged `circle_id`, skip escrow entirely, and produce no ledger transaction — when the last task completes, the Circle closes with a "3 needs fulfilled · 3 neighbours helped · 0 credits exchanged" screen. Withdrawing after neighbours have been helped is recorded as a broken commitment, unfinished needs return to the board, and completed work stays honoured. Blocked residents are never matched into the same Circle. Circle suggestions can be turned off in Settings.

## Safety, consent and data

Registration requires four consent checkboxes (Terms, Privacy Notice, Community Guidelines, competence note); optional analytics consent defaults to **off** and lives under Settings → Privacy & Data. Identity verification is simulated — no HKID is collected — and is labelled as a planned feature that would stop people farming welcome credits. Task creation and chat run a keyword blocklist for clearly prohibited activity. Residents can report a user or task and block a resident (blocking stops direct messaging, task acceptance and shared Circle matching). Moderators get `/moderator/reports` with dismiss / warning / false-evidence / restrict / suspend / restore actions, each of which writes a reliability event. `Download My Data` exports a JSON file (account, ledger, tasks, reliability events, Circle participation, privacy settings, consents) and `Delete My Account` deactivates the profile behind a `DELETE` confirmation while keeping the ledger intact.

Legal pages live at `/legal/tos`, `/legal/privacy`, `/legal/guidelines`, `/legal/disputes`, `/legal/circle` and `/legal/ai`.

## Evidence screening

Screening checks file type, size, MIME consistency, duplicate content and basic image signals. It **flags rather than blocks**: a suspicious file is accepted and marked `requires_review`, and the participants see "Evidence requires review" — never "fraud detected". Flags become supporting information during a dispute. Categories that don't suit photographs (education, companionship) accept the requester's confirmation instead of a file.

## Cancellation

Cancellation is requested, not unilateral, once both sides are involved. The other participant can agree, discuss first, or ask for compensation for work already committed; the requester can accept, decline, or escalate to IoU's normal resolution ladder. Agreed cancellations carry no reliability penalty and held credits are returned.

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

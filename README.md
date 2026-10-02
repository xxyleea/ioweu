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

The prototype includes the request board, requester-side offers with buffers, provider matching, two-sided value and buffer negotiation, overlapping-range settlement, a three-day negotiation deadline that releases the provider when no agreement is reached, a recent-history estimator endpoint, balance ledger, mutual completion confirmation, transaction history, and local evidence upload plumbing for disputes. The recommendation engine infers effort and complexity from the written task details on the backend, then compares them with recent history. It is deterministic and local so a live demo does not require an external AI key.

When an agreed task is started, its credits move from the requester's available balance into held escrow. They are released to the provider only after both parties confirm completion. The requester must remain above the configurable `-50` credit floor when escrow is created.

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

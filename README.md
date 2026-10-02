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

The prototype includes the request board, requester-side offers with buffers, provider matching, two-sided value and buffer negotiation, overlapping-range settlement, a three-day negotiation deadline that releases the provider when no agreement is reached, a recent-history estimator endpoint, balance ledger, mutual completion confirmation, transaction history, and local evidence upload plumbing for disputes. The deterministic recommendation engine is intentionally local so a live demo does not require an external AI key.

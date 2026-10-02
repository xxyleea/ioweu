from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "ioweu.db"
UPLOAD_DIR = BASE_DIR / "uploads"
MIN_BALANCE = -20
TASK_CATEGORIES = (
    "errands",
    "childcare",
    "tech help",
    "home",
    "pet care",
    "tutoring",
)
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="IoU")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("IOWEU_SESSION_SECRET", "demo-only-change-me"),
    max_age=60 * 60 * 24 * 14,
)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    salt_hex, digest_hex = encoded.split("$", 1)
    candidate = hash_password(password, bytes.fromhex(salt_hex)).split("$", 1)[1]
    return hmac.compare_digest(candidate, digest_hex)


def init_db() -> None:
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL, balance INTEGER NOT NULL DEFAULT 0,
                held_balance INTEGER NOT NULL DEFAULT 0,
                is_moderator INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                category TEXT NOT NULL, requester_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', provider_id INTEGER,
                requester_value INTEGER, requester_buffer INTEGER,
                provider_value INTEGER, provider_buffer INTEGER,
                agreed_value INTEGER, negotiation_deadline TEXT,
                effort_minutes INTEGER, complexity INTEGER, quality_score INTEGER,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(requester_id) REFERENCES users(id),
                FOREIGN KEY(provider_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY, request_id INTEGER UNIQUE NOT NULL,
                requester_id INTEGER NOT NULL, provider_id INTEGER NOT NULL,
                value INTEGER NOT NULL, effort_minutes INTEGER,
                complexity INTEGER, quality_score INTEGER,
                kudos_given INTEGER NOT NULL DEFAULT 0,
                kudos_bonus INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(request_id) REFERENCES requests(id),
                FOREIGN KEY(requester_id) REFERENCES users(id),
                FOREIGN KEY(provider_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS disputes (
                id INTEGER PRIMARY KEY, request_id INTEGER UNIQUE NOT NULL,
                opened_by INTEGER NOT NULL, description TEXT NOT NULL,
                evidence_path TEXT, status TEXT NOT NULL DEFAULT 'open',
                recommendation TEXT, moderator_decision TEXT,
                requested_refund_amount INTEGER NOT NULL DEFAULT 0,
                requester_offer INTEGER, provider_offer INTEGER,
                negotiation_status TEXT NOT NULL DEFAULT 'open',
                resolved_at TEXT,
                FOREIGN KEY(request_id) REFERENCES requests(id)
            );
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, request_id INTEGER,
                kind TEXT NOT NULL, amount INTEGER NOT NULL, description TEXT NOT NULL,
                created_at TEXT NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(request_id) REFERENCES requests(id)
            );
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(requests)")}
        user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "held_balance" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN held_balance INTEGER NOT NULL DEFAULT 0")
        if "negotiation_deadline" not in columns:
            connection.execute("ALTER TABLE requests ADD COLUMN negotiation_deadline TEXT")
        for column in ("effort_minutes", "complexity", "quality_score"):
            if column not in columns:
                connection.execute(f"ALTER TABLE requests ADD COLUMN {column} INTEGER")
        if "completed_at" not in columns:
            connection.execute("ALTER TABLE requests ADD COLUMN completed_at TEXT")
        transaction_columns = {row["name"] for row in connection.execute("PRAGMA table_info(transactions)")}
        for column in ("effort_minutes", "complexity", "quality_score"):
            if column not in transaction_columns:
                connection.execute(f"ALTER TABLE transactions ADD COLUMN {column} INTEGER")
        if "kudos_given" not in transaction_columns:
            connection.execute("ALTER TABLE transactions ADD COLUMN kudos_given INTEGER NOT NULL DEFAULT 0")
        if "kudos_bonus" not in transaction_columns:
            connection.execute("ALTER TABLE transactions ADD COLUMN kudos_bonus INTEGER NOT NULL DEFAULT 0")
        dispute_columns = {row["name"] for row in connection.execute("PRAGMA table_info(disputes)")}
        for column, definition in (
            ("requested_refund_amount", "INTEGER NOT NULL DEFAULT 0"),
            ("requester_offer", "INTEGER"),
            ("provider_offer", "INTEGER"),
            ("negotiation_status", "TEXT NOT NULL DEFAULT 'open'"),
            ("resolved_at", "TEXT"),
        ):
            if column not in dispute_columns:
                connection.execute(f"ALTER TABLE disputes ADD COLUMN {column} {definition}")
        legacy_negotiations = connection.execute(
            """SELECT id, created_at FROM requests
               WHERE status='negotiating' AND negotiation_deadline IS NULL"""
        ).fetchall()
        for item in legacy_negotiations:
            created_at = datetime.fromisoformat(item["created_at"])
            deadline = (created_at + timedelta(days=3)).isoformat(timespec="seconds")
            connection.execute(
                "UPDATE requests SET negotiation_deadline=? WHERE id=?",
                (deadline, item["id"]),
            )
        if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            users = [
                ("Alex Resident", "alex@example.com", "demo123", 0, 0),
                ("Sam Resident", "sam@example.com", "demo123", 0, 0),
                ("Morgan Moderator", "moderator@example.com", "demo123", 0, 1),
            ]
            connection.executemany(
                "INSERT INTO users(name,email,password,balance,is_moderator) VALUES (?,?,?,?,?)",
                [(name, email, hash_password(password), balance, moderator) for name, email, password, balance, moderator in users],
            )


def current_user(request: Request) -> sqlite3.Row | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with db() as connection:
        return connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def comparable_stats(connection: sqlite3.Connection, category: str) -> tuple[int, int]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    values = [
        row["value"]
        for row in connection.execute(
            """SELECT t.value FROM transactions t JOIN requests r ON r.id=t.request_id
               WHERE r.category=? AND t.created_at>=? ORDER BY t.value""",
            (category, cutoff),
        )
    ]
    if not values:
        return 3, 2
    midpoint = values[len(values) // 2]
    spread = max(2, round(midpoint * 0.35)) if len(values) < 5 else max(1, round(midpoint * 0.2))
    return midpoint, spread


def recommendation(
    connection: sqlite3.Connection,
    category: str,
    effort_minutes: int = 30,
    complexity: int = 3,
    quality_score: int = 3,
) -> dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    rows = connection.execute(
        """SELECT t.value, t.effort_minutes, t.complexity, t.quality_score
           FROM transactions t JOIN requests r ON r.id=t.request_id
           WHERE r.category=? AND t.created_at>=? ORDER BY t.value""",
        (category.lower(), cutoff),
    ).fetchall()
    if not rows:
        midpoint, spread = comparable_stats(connection, category.lower())
        return {
            "recommended": midpoint,
            "range": [max(1, midpoint - spread), midpoint + spread],
            "benchmark": midpoint,
            "adjustments": {"effort": 1.0, "complexity": 1.0, "quality": 1.0},
            "sample_size": 0,
        }
    benchmark = rows[len(rows) // 2]["value"]
    measured_effort = [row["effort_minutes"] for row in rows if row["effort_minutes"]]
    typical_effort = sum(measured_effort) / len(measured_effort) if measured_effort else 30
    effort_factor = max(0.5, min(1.5, effort_minutes / typical_effort))
    complexity_factor = max(0.7, min(1.3, 1 + 0.1 * (complexity - 3)))
    quality_factor = max(0.8, min(1.2, 1 + 0.05 * (quality_score - 3)))
    suggested = max(1, round(benchmark * effort_factor * complexity_factor * quality_factor))
    spread = max(1, round(suggested * (0.2 if len(rows) >= 5 else 0.35)))
    return {
        "recommended": suggested,
        "range": [max(1, suggested - spread), suggested + spread],
        "benchmark": benchmark,
        "adjustments": {
            "effort": round(effort_factor, 2),
            "complexity": round(complexity_factor, 2),
            "quality": round(quality_factor, 2),
        },
        "sample_size": len(rows),
    }


def infer_task_attributes(title: str, description: str, category: str) -> tuple[int, int]:
    """Infer task effort and complexity from the written request for the demo."""
    text = f"{title} {description} {category}".lower()
    effort = 30
    complexity = 3
    effort_keywords = {
        "quick": -10, "pickup": 10, "collect": 10, "grocery": 25,
        "shopping": 30, "clean": 45, "cleaning": 45, "assemble": 45,
        "repair": 60, "install": 60, "move": 75, "childcare": 60,
        "supervision": 60, "lesson": 60, "urgent": 15,
    }
    complexity_keywords = {
        "quick": -1, "pickup": -1, "collect": -1, "grocery": 0,
        "shopping": 0, "clean": 0, "cleaning": 0, "assemble": 1,
        "repair": 2, "install": 2, "move": 2, "childcare": 1,
        "supervision": 1, "lesson": 1, "urgent": 1,
    }
    for keyword, adjustment in effort_keywords.items():
        if keyword in text:
            effort += adjustment
    for keyword, adjustment in complexity_keywords.items():
        if keyword in text:
            complexity += adjustment
    return max(15, effort), max(1, min(5, complexity))


def render(request: Request, template: str, **context: Any) -> HTMLResponse:
    context["user"] = current_user(request)
    return templates.TemplateResponse(request=request, name=template, context=context)


def expire_negotiations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """UPDATE requests
           SET status='open', provider_id=NULL, provider_value=NULL,
               provider_buffer=NULL, negotiation_deadline=NULL
           WHERE status='negotiating' AND negotiation_deadline IS NOT NULL
             AND negotiation_deadline <= ?""",
        (now(),),
    )


def ledger_entry(connection: sqlite3.Connection, user_id: int, request_id: int | None,
                 kind: str, amount: int, description: str) -> None:
    connection.execute(
        "INSERT INTO ledger(user_id,request_id,kind,amount,description,created_at) VALUES (?,?,?,?,?,?)",
        (user_id, request_id, kind, amount, description, now()),
    )


def outside_range(connection: sqlite3.Connection, item: sqlite3.Row, value: int) -> bool:
    band = recommendation(connection, item["category"], item["effort_minutes"] or 30,
                          item["complexity"] or 3, item["quality_score"] or 3)["range"]
    return value < band[0] or value > band[1]


@app.on_event("startup")
def startup() -> None:
    init_db()


def load_board(connection: sqlite3.Connection):
    expire_negotiations(connection)
    requests = connection.execute(
        """SELECT r.*, u.name requester_name, p.name provider_name
           FROM requests r JOIN users u ON u.id=r.requester_id
           LEFT JOIN users p ON p.id=r.provider_id ORDER BY r.created_at DESC"""
    ).fetchall()
    recommendations = {
        item["id"]: recommendation(
            connection,
            item["category"],
            item["effort_minutes"] or 30,
            item["complexity"] or 3,
            item["quality_score"] or 3,
        )
        for item in requests
    }
    return requests, recommendations


def load_history(connection: sqlite3.Connection, user_id: int):
    return connection.execute(
        """SELECT t.*, r.title, u.name provider_name FROM transactions t
           JOIN requests r ON r.id=t.request_id JOIN users u ON u.id=t.provider_id
           WHERE t.requester_id=? OR t.provider_id=? ORDER BY t.created_at DESC""",
        (user_id, user_id),
    ).fetchall()


def load_disputes(connection: sqlite3.Connection):
    return connection.execute(
        """SELECT d.*, r.title, r.agreed_value, u.name opened_by_name
           FROM disputes d JOIN requests r ON r.id=d.request_id
           JOIN users u ON u.id=d.opened_by ORDER BY d.id DESC"""
    ).fetchall()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        requests, recommendations = load_board(connection)
        active_items = [
            item for item in requests
            if (item["requester_id"] == user["id"] or item["provider_id"] == user["id"])
            and item["status"] != "completed"
        ]
        history = load_history(connection, user["id"])
        disputes = load_disputes(connection)
        ledger = connection.execute(
            """SELECT l.*, r.title FROM ledger l LEFT JOIN requests r ON r.id=l.request_id
               WHERE l.user_id=? ORDER BY l.created_at DESC""", (user["id"],)
        ).fetchall()
        stats = {
            "completed": connection.execute(
                "SELECT COUNT(*) c FROM transactions WHERE requester_id=? OR provider_id=?",
                (user["id"], user["id"])).fetchone()["c"],
            "helped": connection.execute(
                "SELECT COUNT(*) c FROM transactions WHERE provider_id=?",
                (user["id"],)).fetchone()["c"],
            "kudos": connection.execute(
                "SELECT COALESCE(SUM(kudos_bonus),0) s FROM transactions WHERE provider_id=?",
                (user["id"],)).fetchone()["s"],
        }
    return render(
        request,
        "dashboard.html",
        page="home",
        active_items=active_items,
        history=history,
        disputes=disputes,
        ledger=ledger,
        recommendations=recommendations,
        stats=stats,
        task_categories=TASK_CATEGORIES,
    )


@app.get("/my-posts", response_class=HTMLResponse)
def my_posts(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        requests, recommendations = load_board(connection)
        mine = [item for item in requests if item["requester_id"] == user["id"]]
        my_open = [item for item in mine if item["status"] == "open"]
        my_active = [item for item in mine if item["status"] != "completed" and item["status"] != "open"]
        my_completed = [item for item in mine if item["status"] == "completed"]
        joined = [
            item for item in requests
            if item["provider_id"] == user["id"] and item["requester_id"] != user["id"]
        ]
        history = load_history(connection, user["id"])
        disputes = load_disputes(connection)
    return render(
        request,
        "my_posts.html",
        page="posts",
        my_open=my_open,
        my_active=my_active,
        my_completed=my_completed,
        joined_requests=joined,
        history=history,
        disputes=disputes,
        recommendations=recommendations,
        task_categories=TASK_CATEGORIES,
    )


@app.get("/browse", response_class=HTMLResponse)
def browse(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        requests, recommendations = load_board(connection)
        community_requests = [
            item for item in requests
            if item["requester_id"] != user["id"] and item["provider_id"] is None
        ]
        history = load_history(connection, user["id"])
    return render(
        request,
        "browse.html",
        page="browse",
        community_requests=community_requests,
        history=history,
        disputes=[],
        recommendations=recommendations,
        task_categories=TASK_CATEGORIES,
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(BASE_DIR / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html", error=None)


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    with db() as connection:
        user = connection.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    if not user or not verify_password(password, user["password"]):
        return render(request, "login.html", error="Invalid email or password.")
    request.session["user_id"] = user["id"]
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.post("/requests/{request_id}/kudos")
def give_kudos(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = connection.execute(
            """SELECT t.* FROM transactions t
               JOIN requests r ON r.id=t.request_id
               WHERE t.request_id=? AND t.requester_id=? AND r.status='completed'""",
            (request_id, user["id"]),
        ).fetchone()
        if item and not item["kudos_given"]:
            bonus = max(1, round(item["value"] * 0.10))
            connection.execute(
                "UPDATE users SET balance=balance+? WHERE id=?",
                (bonus, item["provider_id"]),
            )
            connection.execute(
                "UPDATE transactions SET kudos_given=1, kudos_bonus=? WHERE id=?",
                (bonus, item["id"]),
            )
            ledger_entry(connection, item["provider_id"], request_id, "kudos_bonus", bonus,
                         f"Kudos bonus for {item['request_id']}")
    return RedirectResponse("/", status_code=303)


@app.post("/requests")
def create_request(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    category: str = Form(...),
    offered_value: int = Form(...),
    offered_buffer: int = Form(...),
    confirm_outside_range: str | None = Form(None),
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user["balance"] < MIN_BALANCE:
        return render(
            request,
            "error.html",
            message=f"Your available balance is {user['balance']} credits. You must be at or above {MIN_BALANCE} credits to post a new request.",
        )
    category = category.strip().lower()
    if category not in TASK_CATEGORIES:
        return render(request, "error.html", message="Please choose a valid task category.")
    offered_value, offered_buffer = max(1, offered_value), max(0, offered_buffer)
    effort_minutes, complexity = infer_task_attributes(title, description, category)
    with db() as connection:
        suggested = recommendation(connection, category, effort_minutes, complexity)
        if (offered_value < suggested["range"][0] or offered_value > suggested["range"][1]) and confirm_outside_range != "1":
            return render(request, "error.html", message=(
                f"Warning: {offered_value} credits is outside the recommended "
                f"{suggested['range'][0]}–{suggested['range'][1]} range. "
                "Return to the form, adjust the offer, or tick the confirmation checkbox to proceed."
            ))
        connection.execute(
            """INSERT INTO requests(
                title,description,category,requester_id,requester_value,
                requester_buffer,effort_minutes,complexity,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                title.strip(), description.strip(), category.strip().lower(),
                user["id"], offered_value, offered_buffer, effort_minutes,
                complexity, now(),
            ),
        )
    return RedirectResponse("/", status_code=303)


@app.post("/requests/{request_id}/accept")
def accept_request(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        expire_negotiations(connection)
        item = connection.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if item and item["status"] == "open" and item["requester_id"] != user["id"]:
            connection.execute(
                """UPDATE requests SET provider_id=?, status='negotiating',
                   negotiation_deadline=? WHERE id=?""",
                (user["id"], (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec="seconds"), request_id),
            )
    return RedirectResponse("/", status_code=303)


@app.post("/requests/{request_id}/decline")
def decline_request(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = connection.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if item and item["provider_id"] == user["id"] and item["status"] == "negotiating":
            connection.execute(
                """UPDATE requests SET status='open', provider_id=NULL, provider_value=NULL,
                   provider_buffer=NULL, negotiation_deadline=NULL WHERE id=?""", (request_id,))
    return RedirectResponse("/", status_code=303)


@app.post("/requests/{request_id}/value")
def submit_value(
    request: Request,
    request_id: int,
    value: int = Form(...),
    buffer: int = Form(...),
    confirm_outside_range: str | None = Form(None),
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    value, buffer = max(1, value), max(0, buffer)
    with db() as connection:
        expire_negotiations(connection)
        item = connection.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if not item:
            return RedirectResponse("/", status_code=303)
        if user["id"] not in (item["requester_id"], item["provider_id"]) or item["status"] != "negotiating":
            return RedirectResponse("/", status_code=303)
        if outside_range(connection, item, value) and confirm_outside_range != "1":
            return render(request, "error.html", message=(
                "Warning: this value is outside IoU's recommended range. "
                "Return to the negotiation form and confirm that you want to continue."
            ))
        field = "requester_value" if item["requester_id"] == user["id"] else "provider_value"
        buffer_field = "requester_buffer" if field == "requester_value" else "provider_buffer"
        connection.execute(
            f"UPDATE requests SET {field}=?, {buffer_field}=? WHERE id=?",
            (value, buffer, request_id),
        )
        item = connection.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if item["requester_value"] is not None and item["provider_value"] is not None:
            requester_range = range(item["requester_value"] - item["requester_buffer"], item["requester_value"] + item["requester_buffer"] + 1)
            provider_range = range(item["provider_value"] - item["provider_buffer"], item["provider_value"] + item["provider_buffer"] + 1)
            overlap = sorted(set(requester_range).intersection(provider_range))
            if overlap:
                connection.execute(
                    "UPDATE requests SET agreed_value=?, status='agreed', negotiation_deadline=NULL WHERE id=?",
                    (overlap[len(overlap) // 2], request_id),
                )
    return RedirectResponse("/", status_code=303)


@app.post("/requests/{request_id}/start")
def start_request(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = connection.execute(
            "SELECT * FROM requests WHERE id=? AND requester_id=?",
            (request_id, user["id"]),
        ).fetchone()
        if not item or item["status"] != "agreed":
            return RedirectResponse("/", status_code=303)
        if user["balance"] - item["agreed_value"] < MIN_BALANCE:
            return render(
                request,
                "error.html",
                message=f"You need at least {item['agreed_value']} available credits to hold this task and stay above the {MIN_BALANCE} credit limit.",
            )
        connection.execute(
            """UPDATE users
               SET balance=balance-?, held_balance=held_balance+?
               WHERE id=?""",
            (item["agreed_value"], item["agreed_value"], user["id"]),
        )
        ledger_entry(connection, user["id"], request_id, "escrow_hold", -item["agreed_value"],
                     f"Escrow hold for {item['title']}")
        connection.execute(
            "UPDATE requests SET status='in_progress' WHERE id=?",
            (request_id,),
        )
    return RedirectResponse("/", status_code=303)


@app.post("/requests/{request_id}/complete")
def complete_request(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = connection.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if item and user["id"] in (item["requester_id"], item["provider_id"]):
            status = "provider_confirmed" if user["id"] == item["provider_id"] else "requester_confirmed"
            if item["status"] in ("provider_confirmed", "requester_confirmed"):
                status = "completed"
                connection.execute(
                    """INSERT OR IGNORE INTO transactions(
                       request_id,requester_id,provider_id,value,effort_minutes,
                       complexity,quality_score,created_at
                    ) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        request_id, item["requester_id"], item["provider_id"],
                        item["agreed_value"], item["effort_minutes"],
                        item["complexity"], item["quality_score"], now(),
                    ),
                )
                connection.execute(
                    """UPDATE users
                       SET held_balance=held_balance-?
                       WHERE id=? AND held_balance>=?""",
                    (item["agreed_value"], item["requester_id"], item["agreed_value"]),
                )
                connection.execute(
                    "UPDATE users SET balance=balance+? WHERE id=?",
                    (item["agreed_value"], item["provider_id"]),
                )
                ledger_entry(connection, item["provider_id"], request_id, "task_payment",
                             item["agreed_value"], f"Payment for {item['title']}")
                completed_at = now()
            else:
                completed_at = item["completed_at"]
            connection.execute("UPDATE requests SET status=?, completed_at=? WHERE id=?",
                               (status, completed_at, request_id))
    return RedirectResponse("/", status_code=303)


@app.post("/requests/{request_id}/dispute")
async def dispute(
    request: Request,
    request_id: int,
    description: str = Form(...),
    requested_refund_amount: int = Form(...),
    evidence: UploadFile | None = File(None),
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    path = None
    if evidence and evidence.filename:
        suffix = Path(evidence.filename).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".pdf", ".txt"}:
            return render(request, "error.html", message="Unsupported evidence type.")
        data = await evidence.read()
        if len(data) > 5 * 1024 * 1024:
            return render(request, "error.html", message="Evidence must be smaller than 5 MB.")
        filename = f"{secrets.token_hex(12)}{suffix}"
        (UPLOAD_DIR / filename).write_bytes(data)
        path = filename
    with db() as connection:
        item = connection.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if not item or user["id"] != item["requester_id"] or item["status"] != "completed":
            return RedirectResponse("/", status_code=303)
        if not item["completed_at"] or datetime.fromisoformat(item["completed_at"]) + timedelta(days=3) < datetime.now(timezone.utc):
            return render(request, "error.html", message="The three-day post-completion dispute window has closed.")
        requested_refund_amount = max(0, min(requested_refund_amount, item["agreed_value"]))
        midpoint, spread = comparable_stats(connection, "general")
        recommendation = f"Review value {item['agreed_value']} credits against a recent-history band of {midpoint - spread}-{midpoint + spread}."
        connection.execute(
            """INSERT OR REPLACE INTO disputes(
               request_id,opened_by,description,evidence_path,recommendation,
               requested_refund_amount,requester_offer,negotiation_status)
               VALUES (?,?,?,?,?,?,?,'open')""",
            (request_id, user["id"], description.strip(), path, recommendation,
             requested_refund_amount, requested_refund_amount if user["id"] == item["requester_id"] else None),
        )
    return RedirectResponse("/", status_code=303)


@app.post("/disputes/{dispute_id}/decide")
def decide_dispute(request: Request, dispute_id: int, decision: str = Form(...)):
    user = current_user(request)
    if not user or not user["is_moderator"]:
        return RedirectResponse("/", status_code=303)
    with db() as connection:
        dispute = connection.execute(
            "SELECT d.*, r.agreed_value, r.requester_id, r.provider_id FROM disputes d JOIN requests r ON r.id=d.request_id WHERE d.id=?",
            (dispute_id,)).fetchone()
        if dispute and dispute["status"] == "open":
            refund = {"deny": 0, "partial": dispute["requested_refund_amount"] // 2,
                      "full": dispute["requested_refund_amount"]}.get(decision, 0)
            connection.execute("UPDATE users SET balance=balance+? WHERE id=?",
                               (refund, dispute["requester_id"]))
            if refund:
                connection.execute("UPDATE users SET balance=balance-? WHERE id=?",
                                   (refund, dispute["provider_id"]))
            if refund:
                ledger_entry(connection, dispute["requester_id"], dispute["request_id"], "refund",
                             refund, f"Refund ({decision}) for dispute #{dispute_id}")
            connection.execute(
                "UPDATE disputes SET status='resolved', moderator_decision=?, negotiation_status='resolved', resolved_at=? WHERE id=?",
                (decision, now(), dispute_id))
    return RedirectResponse("/", status_code=303)


@app.post("/disputes/{dispute_id}/offer")
def dispute_offer(request: Request, dispute_id: int, amount: int = Form(...)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        d = connection.execute(
            "SELECT d.*, r.requester_id, r.provider_id FROM disputes d JOIN requests r ON r.id=d.request_id WHERE d.id=?",
            (dispute_id,)).fetchone()
        if not d or d["status"] != "open" or user["id"] not in (d["requester_id"], d["provider_id"]):
            return RedirectResponse("/", status_code=303)
        amount = max(0, min(amount, d["requested_refund_amount"]))
        if user["id"] == d["requester_id"]:
            connection.execute("UPDATE disputes SET requester_offer=? WHERE id=?", (amount, dispute_id))
        else:
            connection.execute("UPDATE disputes SET provider_offer=? WHERE id=?", (amount, dispute_id))
    return RedirectResponse("/", status_code=303)


@app.post("/disputes/{dispute_id}/accept")
def accept_dispute_offer(request: Request, dispute_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        d = connection.execute(
            "SELECT d.*, r.requester_id FROM disputes d JOIN requests r ON r.id=d.request_id WHERE d.id=?",
            (dispute_id,)).fetchone()
        if not d or user["id"] != d["requester_id"] or d["provider_offer"] is None:
            return RedirectResponse("/", status_code=303)
        refund = d["provider_offer"]
        connection.execute("UPDATE users SET balance=balance+? WHERE id=?", (refund, user["id"]))
        if refund:
            connection.execute("UPDATE users SET balance=balance-? WHERE id=?", (refund, d["provider_id"]))
        if refund:
            ledger_entry(connection, user["id"], d["request_id"], "refund", refund,
                         f"Agreed refund for dispute #{dispute_id}")
        connection.execute(
            "UPDATE disputes SET status='resolved', negotiation_status='resolved', moderator_decision='agreed', resolved_at=? WHERE id=?",
            (now(), dispute_id))
    return RedirectResponse("/", status_code=303)


@app.get("/estimate/{category}")
def estimate(
    request: Request,
    category: str,
    title: str = "",
    description: str = "",
    effort_minutes: int | None = None,
    complexity: int | None = None,
    quality_score: int = 3,
):
    if effort_minutes is None or complexity is None:
        effort_minutes, complexity = infer_task_attributes(title, description, category)
    with db() as connection:
        result = recommendation(
            connection,
            category,
            max(1, effort_minutes),
            max(1, min(5, complexity)),
            max(1, min(5, quality_score)),
        )
    return {"category": category, **result}

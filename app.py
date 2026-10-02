from __future__ import annotations

import contextlib
import hashlib
import json
import hmac
import os
import secrets
import sqlite3
import threading
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
    "daily life",
    "education",
    "technology",
    "creative",
    "repair & diy",
    "transport",
    "companionship",
    "family & kids",
    "pets & animals",
    "community",
)
GENRE_EMOJI = {
    "daily life": "🏠", "education": "📚", "technology": "💻", "creative": "🎨",
    "repair & diy": "🔧", "transport": "🚗", "companionship": "🧑‍🤝‍🧑",
    "family & kids": "👶", "pets & animals": "🐶", "community": "🌱",
}
HK_REGIONS = {
    "Hong Kong Island": ["Kennedy Town", "Sai Ying Pun", "Sheung Wan", "Central", "Wan Chai", "Causeway Bay", "North Point", "Quarry Bay", "Tai Koo", "Pok Fu Lam"],
    "Kowloon": ["Tsim Sha Tsui", "Mong Kok", "Yau Ma Tei", "Jordan", "Sham Shui Po", "Kowloon City", "Kwun Tong"],
    "New Territories": ["Sha Tin", "Tai Po", "Tsuen Wan", "Tuen Mun", "Sai Kung", "Yuen Long"],
}
HK_DISTRICTS = [district for districts in HK_REGIONS.values() for district in districts]
SKILL_TREE = {
    "daily life": ["Grocery Shopping", "Cooking", "Meal Preparation", "Cleaning", "Organising", "Moving / Carrying", "Furniture Assembly", "Running Errands", "Plant Care"],
    "education": ["Math", "Science", "English", "Chinese", "Other Languages", "Homework Help", "Exam Preparation", "Study Planning", "Public Speaking", "Music Theory"],
    "technology": ["Computer Setup", "Phone Setup", "Troubleshooting", "Microsoft Office", "Coding", "Web Development", "AI Tools", "Data Analysis", "Excel", "Digital Literacy"],
    "creative": ["Graphic Design", "UI/UX Design", "Figma", "Illustration", "Photography", "Video Editing", "Animation", "Presentation Design", "Writing", "Music", "Drawing", "Crafts"],
    "repair & diy": ["Basic Repairs", "Electronics", "Sewing", "Knitting", "Furniture Assembly", "Painting", "Bicycle Repair", "Gardening", "Basic Plumbing", "Installation"],
    "transport": ["Driving", "Cycling", "Picking Up Items", "Delivery", "Moving Assistance", "Accompanying Someone"],
    "companionship": ["Conversation", "Language Exchange", "Walking Buddy", "Game Partner", "Event Buddy", "Elderly Companionship", "New Neighbour Support", "Listening"],
    "family & kids": ["Babysitting", "Homework Supervision", "Reading with Children", "School Pickup", "Child Activities", "Arts & Crafts for Kids", "Sports for Kids"],
    "pets & animals": ["Dog Walking", "Pet Sitting", "Feeding", "Grooming", "Playing with Pets", "Vet Visit Companion"],
    "community": ["Event Planning", "Event Setup", "Volunteering", "Community Organising", "Translation", "MC / Hosting", "Fundraising", "Decoration", "First Aid"],
}
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="IoU")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("IOWEU_SESSION_SECRET", "demo-only-change-me"),
    max_age=60 * 60 * 24 * 14,
)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["slug"] = lambda value: value.replace("&", "and").replace(" ", "-")
templates.env.filters["from_json"] = lambda value: json.loads(value) if value else []


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


DB_LOCK = threading.RLock()


@contextlib.contextmanager
def db() -> sqlite3.Connection:
    DB_LOCK.acquire()
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 15000")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        DB_LOCK.release()


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
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL,
                sender_id INTEGER, kind TEXT NOT NULL DEFAULT 'chat',
                body TEXT NOT NULL, meta TEXT, status TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(request_id) REFERENCES requests(id),
                FOREIGN KEY(sender_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                body TEXT NOT NULL, link TEXT NOT NULL DEFAULT '/',
                is_read INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL DEFAULT 'info',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS thread_reads (
                user_id INTEGER NOT NULL, request_id INTEGER NOT NULL,
                last_read_id INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, request_id)
            );
            CREATE TABLE IF NOT EXISTS pins (
                user_id INTEGER NOT NULL, request_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id, request_id)
            );
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(requests)")}
        user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "held_balance" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN held_balance INTEGER NOT NULL DEFAULT 0")
        for column, definition in (
            ("language", "TEXT NOT NULL DEFAULT 'en'"),
            ("address", "TEXT"),
            ("district", "TEXT"),
            ("hkid", "TEXT"),
            ("address_id", "TEXT"),
            ("age", "INTEGER"),
            ("birthday", "TEXT"),
            ("skills", "TEXT NOT NULL DEFAULT ''"),
            ("community_helper", "INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "TEXT"),
        ):
            if column not in user_columns:
                connection.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
        if "location" not in columns:
            connection.execute("ALTER TABLE requests ADD COLUMN location TEXT")
        if "needed_by" not in columns:
            connection.execute("ALTER TABLE requests ADD COLUMN needed_by TEXT")
        if "urgency" not in columns:
            connection.execute("ALTER TABLE requests ADD COLUMN urgency TEXT")
        for column, definition in (
            ("meetup_date", "TEXT"), ("meetup_time", "TEXT"), ("meetup_location", "TEXT"),
            ("preferred_time", "TEXT"),
            ("completion_note", "TEXT"), ("completion_evidence", "TEXT"),
            ("confirm_requester", "INTEGER NOT NULL DEFAULT 0"),
            ("confirm_provider", "INTEGER NOT NULL DEFAULT 0"),
            ("started_at", "TEXT"),
        ):
            if column not in columns:
                connection.execute(f"ALTER TABLE requests ADD COLUMN {column} {definition}")
        notif_columns = {row["name"] for row in connection.execute("PRAGMA table_info(notifications)")}
        if "kind" not in notif_columns:
            connection.execute("ALTER TABLE notifications ADD COLUMN kind TEXT NOT NULL DEFAULT 'info'")
        message_columns = {row["name"] for row in connection.execute("PRAGMA table_info(messages)")}
        for column, definition in (("meta", "TEXT"), ("status", "TEXT")):
            if column not in message_columns:
                connection.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")
        dispute_cols = {row["name"] for row in connection.execute("PRAGMA table_info(disputes)")}
        for column, definition in (
            ("reason", "TEXT"), ("desired_outcome", "TEXT"),
            ("requester_accepted", "INTEGER NOT NULL DEFAULT 0"),
            ("provider_accepted", "INTEGER NOT NULL DEFAULT 0"),
            ("review_since", "TEXT"),
        ):
            if column not in dispute_cols:
                connection.execute(f"ALTER TABLE disputes ADD COLUMN {column} {definition}")
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
    user = current_user(request)
    context["user"] = user
    context.setdefault("status_labels", STATUS_LABELS)
    context.setdefault("stage_labels", STAGE_LABELS)
    context.setdefault("stage_map", STAGE_MAP)
    if user and "unread_msgs" not in context:
        with db() as connection:
            auto_resolve_reviews(connection)
            context["unread_msgs"] = unread_message_count(connection, user["id"])
            context["unread_notifs"] = connection.execute(
                "SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0",
                (user["id"],)).fetchone()["c"]
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


def add_message(connection: sqlite3.Connection, request_id: int, sender_id: int | None,
                kind: str, body: str, meta: str | None = None, status: str | None = None) -> int:
    cursor = connection.execute(
        "INSERT INTO messages(request_id,sender_id,kind,body,meta,status,created_at) VALUES (?,?,?,?,?,?,?)",
        (request_id, sender_id, kind, body, meta, status, now()),
    )
    return cursor.lastrowid


def notify(connection: sqlite3.Connection, user_id: int | None, body: str, link: str,
           kind: str = "info") -> None:
    if not user_id:
        return
    connection.execute(
        "INSERT INTO notifications(user_id,body,link,kind,created_at) VALUES (?,?,?,?,?)",
        (user_id, body, link, kind, now()),
    )


STATUS_LABELS = {
    "open": "Waiting for Helper",
    "negotiating": "Negotiating",
    "agreement_pending": "Agreement Pending",
    "confirmed": "Confirmed",
    "scheduled": "Scheduled",
    "in_progress": "In Progress",
    "completion_submitted": "Completion Submitted",
    "completed": "Completed",
    "disputed": "Disputed",
    "human_review": "Human Review",
    "resolved": "Resolved",
}

TIMELINE_STEPS = [
    "Request created", "Offer received", "Negotiation", "Exchange confirmed",
    "Credits escrowed", "Meeting confirmed", "Task started",
    "Completion submitted", "Completion confirmed", "Credits released",
]

STAGE_LABELS = ["Agree", "Plan", "Do", "Complete", "Resolve"]
STAGE_MAP = {
    "open": 0, "negotiating": 0, "agreement_pending": 0,
    "confirmed": 1, "scheduled": 1,
    "in_progress": 2,
    "completion_submitted": 3, "completed": 3, "resolved": 3,
    "disputed": 4, "human_review": 4,
}

STATUS_STEP = {
    "open": 0, "negotiating": 1, "agreement_pending": 2, "confirmed": 4,
    "scheduled": 5, "in_progress": 6, "completion_submitted": 7,
    "completed": 10, "disputed": 7, "human_review": 7, "resolved": 10,
}


def pending_message(connection: sqlite3.Connection, request_id: int, kind: str):
    return connection.execute(
        """SELECT * FROM messages WHERE request_id=? AND kind=? AND status='pending'
           ORDER BY id DESC LIMIT 1""",
        (request_id, kind),
    ).fetchone()


def other_party(item: sqlite3.Row, user_id: int) -> int | None:
    if item["requester_id"] == user_id:
        return item["provider_id"]
    return item["requester_id"]


def mark_thread_read(connection: sqlite3.Connection, request_id: int, user_id: int) -> None:
    connection.execute(
        """INSERT INTO thread_reads(user_id, request_id, last_read_id)
           VALUES (?, ?, (SELECT COALESCE(MAX(id),0) FROM messages WHERE request_id=?))
           ON CONFLICT(user_id, request_id) DO UPDATE SET
           last_read_id=(SELECT COALESCE(MAX(id),0) FROM messages WHERE request_id=?)""",
        (user_id, request_id, request_id, request_id),
    )


def unread_message_count(connection: sqlite3.Connection, user_id: int) -> int:
    return connection.execute(
        """SELECT COUNT(*) c FROM messages m
           JOIN requests r ON r.id=m.request_id
           LEFT JOIN thread_reads tr ON tr.request_id=m.request_id AND tr.user_id=?
           WHERE (r.requester_id=? OR r.provider_id=?)
             AND m.sender_id IS NOT NULL AND m.sender_id != ?
             AND m.id > COALESCE(tr.last_read_id, 0)""",
        (user_id, user_id, user_id, user_id),
    ).fetchone()["c"]


def auto_resolve_reviews(connection: sqlite3.Connection) -> None:
    """Demo: human moderator review resolves itself ~3 seconds after escalation."""
    import random
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat(timespec="seconds")
    rows = connection.execute(
        """SELECT d.*, r.id rid FROM disputes d JOIN requests r ON r.id=d.request_id
           WHERE r.status='human_review' AND d.status='open'
             AND d.review_since IS NOT NULL AND d.review_since <= ?""",
        (cutoff,),
    ).fetchall()
    for dispute in rows:
        item = connection.execute(
            "SELECT * FROM requests WHERE id=?", (dispute["rid"],)).fetchone()
        approved = random.random() < 0.5
        refund = dispute["requested_refund_amount"] if approved else 0
        decision = "approve" if approved else "deny"
        already_settled = connection.execute(
            "SELECT 1 FROM transactions WHERE request_id=?", (dispute["rid"],)).fetchone()
        if not already_settled:
            settle_exchange(connection, item, refund, f"Moderator decision ({decision}) for dispute #{dispute['id']}")
        connection.execute(
            "UPDATE disputes SET status='resolved', moderator_decision=?, negotiation_status='resolved', resolved_at=? WHERE id=?",
            (decision, now(), dispute["id"]))
        connection.execute("UPDATE requests SET status='resolved', completed_at=? WHERE id=?",
                           (now(), dispute["rid"]))
        outcome = f"approved a {refund} credit refund" if approved else "rejected the refund request"
        add_message(connection, dispute["rid"], None, "system",
                    f"A community moderator reviewed this dispute and {outcome}.")
        notify(connection, item["requester_id"], f"Your dispute was reviewed — {outcome}.", f"/requests/{dispute['rid']}", kind="success")
        notify(connection, item["provider_id"], f"Your dispute was reviewed — {outcome}.", f"/requests/{dispute['rid']}", kind="success")


def redirect_toast(path: str, message: str) -> RedirectResponse:
    from urllib.parse import quote
    sep = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{sep}toast={quote(message)}", status_code=303)


def redirect_back(next: str, fallback: str = "/") -> RedirectResponse:
    target = next if next.startswith("/") and not next.startswith("//") else fallback
    return RedirectResponse(target, status_code=303)


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
        pinned_ids = {row["request_id"] for row in connection.execute(
            "SELECT request_id FROM pins WHERE user_id=?", (user["id"],)).fetchall()}
        active_items = [
            item for item in requests
            if (item["requester_id"] == user["id"] or item["provider_id"] == user["id"])
            and item["status"] not in ("completed", "resolved")
        ]
        active_items.sort(key=lambda i: i["id"] not in pinned_ids)
        exchanges = []
        for i in active_items:
            needs, cta, label, href = exchange_flag(connection, i, user["id"])
            exchanges.append({"item": i, "needs": needs, "cta": cta, "label": label, "href": href})
        exchanges.sort(key=lambda e: not e["needs"])
        action_notifs = connection.execute(
            "SELECT * FROM notifications WHERE user_id=? AND is_read=0 AND kind='action' ORDER BY created_at DESC LIMIT 5",
            (user["id"],)).fetchall()
        action_count = sum(1 for e in exchanges if e["needs"])
        history = load_history(connection, user["id"])
        disputes = load_disputes(connection)
        ledger = connection.execute(
            """SELECT l.*, r.title FROM ledger l LEFT JOIN requests r ON r.id=l.request_id
               WHERE l.user_id=? ORDER BY l.created_at DESC""", (user["id"],)
        ).fetchall()
        notifications = connection.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
            (user["id"],),
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
            "incoming": connection.execute(
                """SELECT COALESCE(SUM(agreed_value),0) s FROM requests
                   WHERE provider_id=? AND status IN ('confirmed','scheduled','in_progress','completion_submitted')""",
                (user["id"],)).fetchone()["s"],
        }
    return render(
        request,
        "dashboard.html",
        page="home",
        active_items=active_items,
        exchanges=exchanges,
        action_notifs=action_notifs,
        action_count=action_count,
        pinned_ids=pinned_ids,
        history=history,
        disputes=disputes,
        ledger=ledger,
        notifications=notifications,
        recommendations=recommendations,
        stats=stats,
        task_categories=TASK_CATEGORIES,
    )


@app.get("/history", response_class=HTMLResponse)
def credit_history(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        ledger = connection.execute(
            """SELECT l.*, r.title FROM ledger l LEFT JOIN requests r ON r.id=l.request_id
               WHERE l.user_id=? ORDER BY l.created_at DESC""", (user["id"],)
        ).fetchall()
    return render(request, "history.html", page="account", ledger=ledger)


@app.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, tab: str = "system"):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        notifs = connection.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
            (user["id"],)).fetchall()
        threads = connection.execute(
            """SELECT r.id, r.title, r.status, r.requester_id, r.provider_id,
                      ru.name requester_name, pu.name provider_name,
                      (SELECT body FROM messages WHERE request_id=r.id ORDER BY id DESC LIMIT 1) last_body,
                      (SELECT created_at FROM messages WHERE request_id=r.id ORDER BY id DESC LIMIT 1) last_at
               FROM requests r
               JOIN users ru ON ru.id=r.requester_id
               LEFT JOIN users pu ON pu.id=r.provider_id
               WHERE (r.requester_id=? OR r.provider_id=?)
                 AND EXISTS (SELECT 1 FROM messages WHERE request_id=r.id)
               ORDER BY last_at DESC""",
            (user["id"], user["id"])).fetchall()
    return render(request, "notifications.html", page="notifications",
                  notifs=notifs, threads=threads, tab=tab)


@app.get("/my-posts", response_class=HTMLResponse)
def my_posts(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        requests, recommendations = load_board(connection)
        pinned_ids = {row["request_id"] for row in connection.execute(
            "SELECT request_id FROM pins WHERE user_id=?", (user["id"],)).fetchall()}
        mine = [item for item in requests if item["requester_id"] == user["id"]]
        mine.sort(key=lambda i: i["id"] not in pinned_ids)
        my_open = [item for item in mine if item["status"] == "open"]
        my_active = [item for item in mine if item["status"] not in ("completed", "resolved", "open")]
        my_completed = [item for item in mine if item["status"] in ("completed", "resolved")]
        history = load_history(connection, user["id"])
        disputes = load_disputes(connection)
    return render(
        request,
        "my_posts.html",
        page="posts",
        my_open=my_open,
        my_active=my_active,
        my_completed=my_completed,
        history=history,
        disputes=disputes,
        recommendations=recommendations,
        pinned_ids=pinned_ids,
        task_categories=TASK_CATEGORIES,
    )


@app.get("/helping", response_class=HTMLResponse)
def helping(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        requests, recommendations = load_board(connection)
        pinned_ids = {row["request_id"] for row in connection.execute(
            "SELECT request_id FROM pins WHERE user_id=?", (user["id"],)).fetchall()}
        joined = [
            item for item in requests
            if item["provider_id"] == user["id"] and item["requester_id"] != user["id"]
        ]
        joined.sort(key=lambda i: i["id"] not in pinned_ids)
        helping_active = [item for item in joined if item["status"] not in ("completed", "resolved")]
        helping_done = [item for item in joined if item["status"] in ("completed", "resolved")]
        history = load_history(connection, user["id"])
        disputes = load_disputes(connection)
    return render(
        request,
        "helping.html",
        page="helping",
        helping_active=helping_active,
        helping_done=helping_done,
        history=history,
        disputes=disputes,
        recommendations=recommendations,
        pinned_ids=pinned_ids,
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
    user_skills = [s for s in (user["skills"] or "").split(",") if s]
    matched_genres = {g for g, subs in SKILL_TREE.items() if any(s in subs for s in user_skills)}
    matched_ids = {item["id"] for item in community_requests if item["category"] in matched_genres}
    return render(
        request,
        "browse.html",
        page="browse",
        community_requests=community_requests,
        history=history,
        disputes=[],
        recommendations=recommendations,
        task_categories=TASK_CATEGORIES,
        genre_emoji=GENRE_EMOJI,
        matched_ids=matched_ids,
        matched_genres=matched_genres,
        user_area=user["district"] or user["address_id"] or "",
        user_district=(user["district"] or "").strip().lower(),
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(BASE_DIR / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return render(request, "register.html", error=None, skill_tree=SKILL_TREE,
                  genre_emoji=GENRE_EMOJI, hk_regions=HK_REGIONS)


@app.post("/register")
def register(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    address: str = Form(""),
    district: str = Form(""),
    hkid: str = Form(""),
    birthday: str = Form(""),
    language: str = Form("en"),
    skills: list[str] = Form([]),
):
    email = email.strip().lower()
    district = district.strip()
    if district and district not in HK_DISTRICTS:
        district = ""
    age = None
    if birthday:
        try:
            born = datetime.fromisoformat(birthday)
            today = datetime.now(timezone.utc)
            age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        except ValueError:
            age = None
    all_skills = {skill for skills_list in SKILL_TREE.values() for skill in skills_list}
    skills_clean = [s for s in skills if s in all_skills]
    with db() as connection:
        try:
            cursor = connection.execute(
                """INSERT INTO users(name,email,password,balance,is_moderator,language,
                   address,district,hkid,address_id,age,birthday,skills,created_at)
                   VALUES (?,?,?,10,0,?,?,?,?,?,?,?,?,?)""",
                (name.strip(), email, hash_password(password), language,
                 address.strip(), district, hkid.strip(), district, age, birthday or None,
                 ",".join(skills_clean), now()),
            )
            user_id = cursor.lastrowid
            ledger_entry(connection, user_id, None, "welcome_credit", 10,
                         "Welcome credits for joining the community")
        except sqlite3.IntegrityError:
            user_id = connection.execute(
                "SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
    request.session["user_id"] = user_id
    return RedirectResponse("/welcome", status_code=303)


@app.get("/welcome", response_class=HTMLResponse)
def welcome_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, "welcome.html")


@app.get("/onboarding/skills", response_class=HTMLResponse)
def onboarding_skills_page(request: Request, next: str = "/community-test"):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    selected = [s for s in (user["skills"] or "").split(",") if s]
    return render(request, "onboarding_skills.html", skill_tree=SKILL_TREE,
                  genre_emoji=GENRE_EMOJI, selected_skills=selected, next=next)


@app.post("/onboarding/skills")
def onboarding_skills_save(
    request: Request,
    skills: list[str] = Form([]),
    next: str = Form("/community-test"),
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    all_skills = {skill for skills_list in SKILL_TREE.values() for skill in skills_list}
    skills_clean = [s for s in skills if s in all_skills]
    with db() as connection:
        connection.execute("UPDATE users SET skills=? WHERE id=?",
                           (",".join(skills_clean), user["id"]))
    target = next if next.startswith("/") else "/browse"
    return RedirectResponse(target, status_code=303)


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, saved: str = ""):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, "account.html", page="account", genre_emoji=GENRE_EMOJI,
                  skill_tree=SKILL_TREE, hk_districts=HK_DISTRICTS, saved=saved == "1")


@app.post("/account")
def account_save(
    request: Request,
    name: str = Form(...),
    address: str = Form(""),
    district: str = Form(""),
    hkid: str = Form(""),
    age: int | None = Form(None),
    birthday: str = Form(""),
    language: str = Form("en"),
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    district = district.strip()
    if district and district not in HK_DISTRICTS:
        district = ""
    with db() as connection:
        connection.execute(
            "UPDATE users SET name=?, address=?, district=?, hkid=?, address_id=?, age=?, birthday=?, language=? WHERE id=?",
            (name.strip(), address.strip(), district, hkid.strip(), district, age,
             birthday.strip() or None, language, user["id"]),
        )
    return RedirectResponse("/account?saved=1", status_code=303)


@app.get("/community-test", response_class=HTMLResponse)
def community_test_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, "community_test.html")


@app.post("/community-test")
def community_test_submit(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        connection.execute("UPDATE users SET community_helper=1, balance=balance+5 WHERE id=?", (user["id"],))
        ledger_entry(connection, user["id"], None, "community_test", 5,
                     "Bonus for completing the community fairness test")
    return RedirectResponse("/browse", status_code=303)


@app.get("/new-request", response_class=HTMLResponse)
def new_request_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, "new_request.html", page="posts",
                  task_categories=TASK_CATEGORIES, genre_emoji=GENRE_EMOJI)


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


@app.get("/messages", response_class=HTMLResponse)
def messages_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        threads = connection.execute(
            """SELECT r.id, r.title, r.status, r.category,
                      u.name requester_name, p.name provider_name,
                      (SELECT body FROM messages m WHERE m.request_id=r.id ORDER BY m.created_at DESC, m.id DESC LIMIT 1) last_body,
                      (SELECT created_at FROM messages m WHERE m.request_id=r.id ORDER BY m.created_at DESC, m.id DESC LIMIT 1) last_at,
                      (SELECT COUNT(*) FROM messages m WHERE m.request_id=r.id) msg_count,
                      (SELECT COUNT(*) FROM messages m
                        LEFT JOIN thread_reads tr ON tr.request_id=m.request_id AND tr.user_id=?
                        WHERE m.request_id=r.id AND m.sender_id IS NOT NULL AND m.sender_id != ?
                          AND m.id > COALESCE(tr.last_read_id, 0)) unread_count
               FROM requests r JOIN users u ON u.id=r.requester_id
               LEFT JOIN users p ON p.id=r.provider_id
               WHERE (r.requester_id=? OR r.provider_id=?)
                 AND EXISTS (SELECT 1 FROM messages m WHERE m.request_id=r.id)
               ORDER BY last_at DESC""",
            (user["id"], user["id"], user["id"], user["id"]),
        ).fetchall()
        exchanges = []
        for t in threads:
            item = load_request(connection, t["id"])
            needs, cta, label, href = exchange_flag(connection, item, user["id"])
            exchanges.append({"t": t, "needs": needs, "cta": cta, "label": label, "href": href})
        exchanges.sort(key=lambda e: not e["needs"])
    return render(request, "messages.html", page="messages", exchanges=exchanges)


@app.get("/chat/{request_id}", response_class=HTMLResponse)
def chat_page(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        expire_negotiations(connection)
        item = load_request(connection, request_id)
        if not item or user["id"] not in (item["requester_id"], item["provider_id"] or 0):
            return render(request, "error.html", message="You are not part of this exchange.")
        messages = connection.execute(
            """SELECT m.*, u.name sender_name FROM messages m
               LEFT JOIN users u ON u.id=m.sender_id
               WHERE m.request_id=? ORDER BY m.created_at, m.id""",
            (request_id,),
        ).fetchall()
        pending_offer = pending_message(connection, request_id, "offer")
        pending_meetup = pending_message(connection, request_id, "meetup")
        pending_settlement = pending_message(connection, request_id, "settlement")
        dispute = connection.execute(
            "SELECT * FROM disputes WHERE request_id=?", (request_id,)).fetchone()
        rec = recommendation(connection, item["category"], item["effort_minutes"] or 30,
                             item["complexity"] or 3, item["quality_score"] or 3)
        my_confirm = (item["requester_id"] == user["id"] and item["confirm_requester"]) or \
                     (item["provider_id"] == user["id"] and item["confirm_provider"])
        hero = hero_for(item, user["id"], pending_offer, pending_meetup, dispute, my_confirm)
        mark_thread_read(connection, request_id, user["id"])
        threads = connection.execute(
            """SELECT r.id, r.title, r.status,
                      u.name requester_name, p.name provider_name,
                      (SELECT COUNT(*) FROM messages m
                        LEFT JOIN thread_reads tr ON tr.request_id=m.request_id AND tr.user_id=?
                        WHERE m.request_id=r.id AND m.sender_id IS NOT NULL AND m.sender_id != ?
                          AND m.id > COALESCE(tr.last_read_id, 0)) unread_count
               FROM requests r JOIN users u ON u.id=r.requester_id
               LEFT JOIN users p ON p.id=r.provider_id
               WHERE (r.requester_id=? OR r.provider_id=?)
                 AND EXISTS (SELECT 1 FROM messages m WHERE m.request_id=r.id)
               ORDER BY (SELECT created_at FROM messages m WHERE m.request_id=r.id
                         ORDER BY m.created_at DESC, m.id DESC LIMIT 1) DESC""",
            (user["id"], user["id"], user["id"], user["id"]),
        ).fetchall()
    return render(request, "chat.html", page="messages", item=item, messages=messages,
                  pending_offer=pending_offer, pending_meetup=pending_meetup,
                  pending_settlement=pending_settlement, dispute=dispute, rec=rec,
                  my_confirm=my_confirm, hero=hero, genre_emoji=GENRE_EMOJI,
                  threads=threads, step_index=STATUS_STEP.get(item["status"], 0),
                  timeline_steps=TIMELINE_STEPS,
                  status_label=STATUS_LABELS.get(item["status"], item["status"]))


@app.get("/chat/{request_id}/feed", response_class=HTMLResponse)
def chat_feed(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return HTMLResponse("", status_code=401)
    with db() as connection:
        auto_resolve_reviews(connection)
        item = load_request(connection, request_id)
        if not item or user["id"] not in (item["requester_id"], item["provider_id"] or 0):
            return HTMLResponse("", status_code=403)
        messages = connection.execute(
            """SELECT m.*, u.name sender_name FROM messages m
               LEFT JOIN users u ON u.id=m.sender_id
               WHERE m.request_id=? ORDER BY m.created_at, m.id""",
            (request_id,),
        ).fetchall()
        mark_thread_read(connection, request_id, user["id"])
        rec = recommendation(connection, item["category"], item["effort_minutes"] or 30,
                             item["complexity"] or 3, item["quality_score"] or 3)
    content = templates.get_template("_chat_messages.html").render(
        messages=messages, user=user, rec=rec)
    return HTMLResponse(content, headers={"X-Status": item["status"]})


@app.post("/chat/{request_id}")
def chat_post(request: Request, request_id: int, body: str = Form(...)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = connection.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if item and user["id"] in (item["requester_id"], item["provider_id"] or 0) and body.strip():
            add_message(connection, request_id, user["id"], "chat", body.strip())
    return RedirectResponse(f"/chat/{request_id}", status_code=303)


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, "help.html", page="help")


@app.post("/requests")
def create_request(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    category: str = Form(...),
    location: str = Form(""),
    needed_by: str = Form(""),
    urgency: str = Form(""),
    preferred_time: str = Form(""),
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
                title,description,category,location,needed_by,urgency,preferred_time,requester_id,
                requester_value,requester_buffer,effort_minutes,complexity,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                title.strip(), description.strip(), category.strip().lower(),
                location.strip(), needed_by.strip() or None, urgency.strip() or None,
                preferred_time.strip() or None,
                user["id"], offered_value, offered_buffer, effort_minutes,
                complexity, now(),
            ),
        )
    return RedirectResponse("/my-posts?posted=1", status_code=303)


@app.post("/requests/{request_id}/kudos")
def give_kudos(request: Request, request_id: int, next: str = Form(""), tags: list[str] = Form([])):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = connection.execute(
            """SELECT t.* FROM transactions t
               JOIN requests r ON r.id=t.request_id
               WHERE t.request_id=? AND t.requester_id=? AND r.status IN ('completed','resolved')""",
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
            tag_text = f" ({', '.join(tags)})" if tags else ""
            add_message(connection, request_id, None, "system",
                        f"Kudos{tag_text}! The provider received a {bonus} credit bonus.")
            notify(connection, item["provider_id"],
                   f"You received Kudos (+{bonus} credits){tag_text}.", f"/requests/{request_id}", kind="success")
    return redirect_back(next, f"/requests/{request_id}")


def load_request(connection: sqlite3.Connection, request_id: int):
    return connection.execute(
        """SELECT r.*, u.name requester_name, p.name provider_name
           FROM requests r JOIN users u ON u.id=r.requester_id
           LEFT JOIN users p ON p.id=r.provider_id WHERE r.id=?""",
        (request_id,),
    ).fetchone()


def settle_exchange(connection: sqlite3.Connection, item: sqlite3.Row, refund: int, note: str) -> None:
    """Release escrow: refund goes back to the requester, the rest to the provider."""
    agreed = item["agreed_value"] or 0
    refund = max(0, min(refund, agreed))
    provider_gets = agreed - refund
    connection.execute(
        "UPDATE users SET held_balance=MAX(0, held_balance-?) WHERE id=?",
        (agreed, item["requester_id"]),
    )
    if provider_gets:
        connection.execute("UPDATE users SET balance=balance+? WHERE id=?", (provider_gets, item["provider_id"]))
        ledger_entry(connection, item["provider_id"], item["id"], "task_payment", provider_gets,
                     f"Payment for {item['title']}")
    if refund:
        connection.execute("UPDATE users SET balance=balance+? WHERE id=?", (refund, item["requester_id"]))
        ledger_entry(connection, item["requester_id"], item["id"], "refund", refund, note)
    connection.execute(
        """INSERT OR IGNORE INTO transactions(
               request_id,requester_id,provider_id,value,effort_minutes,
               complexity,quality_score,created_at
           ) VALUES (?,?,?,?,?,?,?,?)""",
        (item["id"], item["requester_id"], item["provider_id"], agreed,
         item["effort_minutes"], item["complexity"], item["quality_score"], now()),
    )


@app.get("/requests/{request_id}", response_class=HTMLResponse)
def request_detail(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        expire_negotiations(connection)
        item = load_request(connection, request_id)
        if not item:
            return render(request, "error.html", message="That request does not exist.")
        recommendations = {
            item["id"]: recommendation(
                connection, item["category"], item["effort_minutes"] or 30,
                item["complexity"] or 3, item["quality_score"] or 3,
            )
        }
        history = load_history(connection, user["id"])
        disputes = load_disputes(connection)
        offer = pending_message(connection, request_id, "offer")
        meetup = pending_message(connection, request_id, "meetup")
        message_count = connection.execute(
            "SELECT COUNT(*) c FROM messages WHERE request_id=?", (request_id,)
        ).fetchone()["c"]
        dispute = connection.execute(
            "SELECT * FROM disputes WHERE request_id=?", (request_id,)
        ).fetchone()
    is_owner = item["requester_id"] == user["id"]
    is_provider = item["provider_id"] == user["id"]
    involved = is_owner or is_provider
    my_confirm = (is_owner and item["confirm_requester"]) or (is_provider and item["confirm_provider"])
    hero = hero_for(item, user["id"], offer, meetup, dispute, my_confirm)
    return render(
        request,
        "request_detail.html",
        item=item,
        recommendations=recommendations,
        history=history,
        disputes=disputes,
        pending_offer=offer,
        pending_meetup=meetup,
        dispute=dispute,
        message_count=message_count,
        is_owner=is_owner,
        is_provider=is_provider,
        involved=involved,
        my_confirm=my_confirm,
        hero=hero,
        status_label=STATUS_LABELS.get(item["status"], item["status"]),
        step_index=STATUS_STEP.get(item["status"], 0),
        timeline_steps=TIMELINE_STEPS,
        genre_emoji=GENRE_EMOJI,
    )


def hero_for(item, user_id, offer, meetup, dispute, my_confirm):
    """Decide current status text, who must act, and the primary action."""
    status = item["status"]
    is_owner = item["requester_id"] == user_id
    is_provider = item["provider_id"] == user_id
    involved = is_owner or is_provider
    rid = item["id"]
    waiting = {"actor": "other", "label": "Waiting for the other participant.", "action": None}
    if status == "open":
        if is_owner:
            return {"actor": "none", "label": "Posted. Waiting for a neighbour to offer help.", "action": None}
        return {"actor": "you", "label": "You can offer to help with this task.",
                "action": {"text": "Offer to Help", "modal": "offer-modal"}}
    if status == "negotiating":
        if offer:
            offer_mine = offer["sender_id"] == user_id
            if offer_mine:
                return {"actor": "other", "label": "Your offer was sent. Waiting for a response.",
                        "action": {"text": "View in Chat", "href": f"/chat/{rid}"}}
            return {"actor": "you", "label": "You received an offer — respond to it.",
                    "action": {"text": "Review Offer", "href": f"/chat/{rid}"}}
        if involved:
            return {"actor": "you", "label": "Negotiation is open. Continue in the chat.",
                    "action": {"text": "Continue Chat", "href": f"/chat/{rid}"}}
        return waiting
    if status == "agreement_pending":
        if my_confirm:
            return {"actor": "other", "label": "You accepted the offer. Waiting for the other participant to confirm.",
                    "action": {"text": "View Chat", "href": f"/chat/{rid}"}}
        return {"actor": "you", "label": "The other participant accepted. Please confirm the exchange.",
                "action": {"text": "Confirm Exchange", "modal": "confirm-exchange-modal"}}
    if status == "confirmed":
        if meetup and meetup["sender_id"] != user_id:
            return {"actor": "you", "label": "Meeting proposal received — respond to it.",
                    "action": {"text": "Review Meeting", "href": f"/chat/{rid}"}}
        if meetup:
            return {"actor": "other", "label": "Meeting proposal sent. Waiting for a response.",
                    "action": {"text": "View Chat", "href": f"/chat/{rid}"}}
        return {"actor": "you", "label": "Credits are in escrow. Schedule the task together.",
                "action": {"text": "Propose Meetup", "href": f"/chat/{rid}"}}
    if status == "scheduled":
        return {"actor": "you", "label": "Meeting confirmed. Start the task when it's time.",
                "action": {"text": "Start Task", "modal": "start-task-modal"}}
    if status == "in_progress":
        if is_provider:
            return {"actor": "you", "label": "Task in progress. Mark it complete when done.",
                    "action": {"text": "Mark as Complete", "modal": "complete-modal"}}
        return {"actor": "other", "label": "The helper is working on your task.", "action": None}
    if status == "completion_submitted":
        if is_owner:
            return {"actor": "you", "label": "Completion submitted — please review it.",
                    "action": {"text": "Review Completion", "href": f"/requests/{rid}/review"}}
        return {"actor": "other", "label": "Waiting for the request owner to confirm.", "action": None}
    if status == "disputed":
        if involved:
            return {"actor": "you", "label": "Dispute in progress. Credits are frozen — settle together or ask IoU to mediate.",
                    "action": {"text": "Resolve Dispute", "href": f"/chat/{rid}"}}
        return waiting
    if status == "human_review":
        return {"actor": "other", "label": "A community moderator is reviewing this dispute.", "action": None}
    if status in ("completed", "resolved"):
        return {"actor": "none", "label": "This exchange is complete. You can still give Kudos or raise a dispute within 3 days.", "action": None}
    return waiting


def exchange_flag(connection: sqlite3.Connection, item, user_id: int):
    offer = pending_message(connection, item["id"], "offer")
    meetup = pending_message(connection, item["id"], "meetup")
    dispute = connection.execute(
        "SELECT * FROM disputes WHERE request_id=?", (item["id"],)).fetchone()
    my_confirm = (item["requester_id"] == user_id and item["confirm_requester"]) or \
                 (item["provider_id"] == user_id and item["confirm_provider"])
    hero = hero_for(item, user_id, offer, meetup, dispute, my_confirm)
    cta = hero["action"]["text"] if hero.get("action") else ""
    href = hero["action"]["href"] if hero.get("action") and hero["action"].get("href") else f"/chat/{item['id']}"
    if item["status"] == "completion_submitted" and item["requester_id"] == user_id:
        href = f"/chat/{item['id']}"
    return hero["actor"] == "you", cta, hero["label"], href


@app.post("/requests/{request_id}/offer")
def make_offer(request: Request, request_id: int, mode: str = Form(...),
               amount: int | None = Form(None), note: str = Form(""),
               confirm_outside_range: str | None = Form(None),
               next: str = Form("")):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        expire_negotiations(connection)
        item = load_request(connection, request_id)
        if not item:
            return redirect_back("/")
        is_owner = item["requester_id"] == user["id"]
        is_provider = item["provider_id"] == user["id"]
        allowed = (item["status"] == "open" and not is_owner) or \
                  (item["status"] == "negotiating" and (is_owner or is_provider))
        if not allowed:
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        value = item["requester_value"] if mode == "accept" else max(1, amount or 1)
        old = pending_message(connection, request_id, "offer")
        if old:
            connection.execute("UPDATE messages SET status='countered' WHERE id=?", (old["id"],))
        if item["status"] == "open":
            connection.execute(
                """UPDATE requests SET provider_id=?, status='negotiating',
                   negotiation_deadline=? WHERE id=?""",
                (user["id"], (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec="seconds"), request_id),
            )
        role = "Requester" if is_owner else "Helper"
        if is_owner:
            connection.execute("UPDATE requests SET requester_value=? WHERE id=?", (value, request_id))
        else:
            connection.execute(
                "UPDATE requests SET provider_value=?, provider_buffer=0 WHERE id=?", (value, request_id))
        add_message(connection, request_id, user["id"], "offer",
                    f"{role} offered {value} credits",
                    meta=json.dumps({"amount": value, "note": note.strip()}), status="pending")
        target = item["requester_id"] if not is_owner else item["provider_id"]
        notify(connection, target,
               f"New offer: {value} credits for \"{item['title']}\".", f"/chat/{request_id}", kind="action")
        if mode == "accept":
            add_message(connection, request_id, None, "system",
                        f"{user['name']} accepted the current offer of {value} credits.")
    target = next if next.startswith("/") and not next.startswith("//") else f"/chat/{request_id}"
    return redirect_toast(target, "Offer sent")


@app.post("/offers/{message_id}/accept")
def accept_offer(request: Request, message_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        offer = connection.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not offer or offer["status"] != "pending" or offer["sender_id"] == user["id"]:
            return redirect_back("", "/messages")
        item = load_request(connection, offer["request_id"])
        if user["id"] not in (item["requester_id"], item["provider_id"] or 0):
            return RedirectResponse(f"/requests/{offer['request_id']}", status_code=303)
        amount = json.loads(offer["meta"] or "{}").get("amount", item["requester_value"])
        connection.execute("UPDATE messages SET status='accepted' WHERE id=?", (message_id,))
        flag = "confirm_requester" if item["requester_id"] == user["id"] else "confirm_provider"
        connection.execute(
            f"UPDATE requests SET agreed_value=?, status='agreement_pending', {flag}=1 WHERE id=?",
            (amount, item["id"]),
        )
        add_message(connection, item["id"], None, "system",
                    f"An offer of {amount} credits has been accepted. Waiting for the other participant to confirm the exchange.")
        notify(connection, other_party(item, user["id"]),
               f"The other participant accepted your offer of {amount} credits — please confirm the exchange.",
               f"/requests/{item['id']}", kind="action")
    return redirect_toast(f"/chat/{offer['request_id']}", "Offer accepted")


@app.post("/offers/{message_id}/decline")
def decline_offer(request: Request, message_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        offer = connection.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not offer or offer["status"] != "pending" or offer["sender_id"] == user["id"]:
            return redirect_back("", "/messages")
        item = load_request(connection, offer["request_id"])
        if user["id"] not in (item["requester_id"], item["provider_id"] or 0):
            return RedirectResponse(f"/requests/{offer['request_id']}", status_code=303)
        connection.execute("UPDATE messages SET status='declined' WHERE id=?", (message_id,))
        connection.execute(
            """UPDATE requests SET status='open', provider_id=NULL, provider_value=NULL,
               provider_buffer=NULL, negotiation_deadline=NULL, confirm_requester=0,
               confirm_provider=0 WHERE id=?""", (item["id"],))
        add_message(connection, item["id"], None, "system",
                    "The offer was declined. The request is open on the board again.")
        notify(connection, other_party(item, user["id"]),
               f"Your offer for \"{item['title']}\" was declined.", f"/requests/{item['id']}", kind="action")
    return RedirectResponse(f"/requests/{offer['request_id']}", status_code=303)


@app.post("/requests/{request_id}/confirm-exchange")
def confirm_exchange(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["status"] != "agreement_pending":
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        is_owner = item["requester_id"] == user["id"]
        is_provider = item["provider_id"] == user["id"]
        if not (is_owner or is_provider):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        flag = "confirm_requester" if is_owner else "confirm_provider"
        connection.execute(f"UPDATE requests SET {flag}=1 WHERE id=?", (request_id,))
        item = load_request(connection, request_id)
        if item["confirm_requester"] and item["confirm_provider"]:
            agreed = item["agreed_value"]
            requester = connection.execute("SELECT balance FROM users WHERE id=?",
                                           (item["requester_id"],)).fetchone()
            if requester["balance"] - agreed < MIN_BALANCE:
                return render(request, "error.html", message=(
                    f"The requester doesn't have enough available credits for this exchange "
                    f"({requester['balance']} available, {agreed} needed). Please renegotiate a lower value in the chat."
                ))
            connection.execute(
                "UPDATE users SET balance=balance-?, held_balance=held_balance+? WHERE id=?",
                (agreed, agreed, item["requester_id"]),
            )
            ledger_entry(connection, item["requester_id"], request_id, "escrow_hold", -agreed,
                         f"Escrow hold for {item['title']}")
            add_message(connection, request_id, None, "system",
                        "Exchange confirmed by both participants.")
            add_message(connection, request_id, None, "system",
                        f"Credits are now in escrow ({agreed} credits held).")
            if item["preferred_time"]:
                dt = item["preferred_time"]
                date_part, _, time_part = dt.partition("T")
                connection.execute(
                    """UPDATE requests SET status='scheduled', meetup_date=?, meetup_time=?,
                       meetup_location=? WHERE id=?""",
                    (date_part, time_part or None, item["location"], request_id))
                add_message(connection, request_id, None, "system",
                            f"Meeting fixed from the request: {date_part} at {time_part or 'flexible time'}, {item['location'] or 'location TBD'}.")
                notify(connection, other_party(item, user["id"]),
                       "Exchange confirmed — credits are in escrow. The meetup time is already fixed.",
                       f"/requests/{request_id}", kind="success")
            else:
                connection.execute("UPDATE requests SET status='confirmed' WHERE id=?", (request_id,))
                notify(connection, other_party(item, user["id"]),
                       "Exchange confirmed — credits are in escrow. Time to schedule the task.",
                       f"/requests/{request_id}", kind="success")
    return redirect_toast(f"/chat/{request_id}", "Exchange confirmed — credits are in escrow")


@app.post("/requests/{request_id}/meetup")
def propose_meetup(request: Request, request_id: int, date: str = Form(...),
                   time: str = Form(...), location: str = Form(...)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or user["id"] not in (item["requester_id"], item["provider_id"] or 0):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        if item["status"] not in ("confirmed", "scheduled"):
            return RedirectResponse(f"/chat/{request_id}", status_code=303)
        old = pending_message(connection, request_id, "meetup")
        if old:
            connection.execute("UPDATE messages SET status='replaced' WHERE id=?", (old["id"],))
        add_message(connection, request_id, user["id"], "meetup",
                    f"Meeting proposal: {date} at {time}, {location}",
                    meta=json.dumps({"date": date, "time": time, "location": location}), status="pending")
        notify(connection, other_party(item, user["id"]),
               f"Meeting proposal for \"{item['title']}\": {date} at {time}.", f"/chat/{request_id}", kind="action")
    return redirect_toast(f"/chat/{request_id}", "Meeting proposal sent")


@app.post("/meetups/{message_id}/{action}")
def respond_meetup(request: Request, message_id: int, action: str):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        msg = connection.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not msg or msg["kind"] != "meetup" or msg["status"] != "pending" or msg["sender_id"] == user["id"]:
            return redirect_back("", "/messages")
        item = load_request(connection, msg["request_id"])
        if user["id"] not in (item["requester_id"], item["provider_id"] or 0):
            return RedirectResponse(f"/requests/{msg['request_id']}", status_code=303)
        if action == "accept":
            meta = json.loads(msg["meta"] or "{}")
            connection.execute("UPDATE messages SET status='accepted' WHERE id=?", (message_id,))
            connection.execute(
                "UPDATE requests SET meetup_date=?, meetup_time=?, meetup_location=?, status='scheduled' WHERE id=?",
                (meta.get("date"), meta.get("time"), meta.get("location"), item["id"]))
            add_message(connection, item["id"], None, "system",
                        f"Meeting confirmed for {meta.get('date')} at {meta.get('time')}, {meta.get('location')}.")
            notify(connection, other_party(item, user["id"]),
                   f"Meeting confirmed for \"{item['title']}\".", f"/requests/{item['id']}", kind="success")
        elif action == "decline":
            connection.execute("UPDATE messages SET status='declined' WHERE id=?", (message_id,))
            add_message(connection, item["id"], None, "system",
                        "Meeting proposal declined. Feel free to propose another time.")
            notify(connection, other_party(item, user["id"]),
                   "Meeting proposal declined.", f"/chat/{item['id']}", kind="info")
    toast = "Meeting confirmed" if action == "accept" else "Meeting proposal declined"
    return redirect_toast(f"/chat/{msg['request_id']}", toast)


@app.post("/requests/{request_id}/start")
def start_request(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or user["id"] not in (item["requester_id"], item["provider_id"] or 0):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        if item["status"] in ("confirmed", "scheduled"):
            connection.execute("UPDATE requests SET status='in_progress', started_at=? WHERE id=?",
                               (now(), request_id))
            add_message(connection, request_id, None, "system", "Task started.")
            notify(connection, other_party(item, user["id"]),
                   f"Task started: \"{item['title']}\".", f"/requests/{request_id}", kind="info")
    return redirect_toast(f"/chat/{request_id}", "Task started")


@app.get("/requests/{request_id}/complete", response_class=HTMLResponse)
def complete_page(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["provider_id"] != user["id"] or item["status"] != "in_progress":
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
    return render(request, "complete_task.html", item=item)


@app.post("/requests/{request_id}/complete")
async def complete_submit(request: Request, request_id: int, note: str = Form(""),
                          confirm_done: str | None = Form(None),
                          evidence: list[UploadFile] = File([])):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if confirm_done != "1":
        return render(request, "error.html", message="Please tick the confirmation checkbox before submitting.")
    paths = []
    for upload in evidence[:3]:
        if not upload or not upload.filename:
            continue
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".pdf", ".txt"}:
            continue
        data = await upload.read()
        if len(data) > 5 * 1024 * 1024:
            continue
        filename = f"{secrets.token_hex(12)}{suffix}"
        (UPLOAD_DIR / filename).write_bytes(data)
        paths.append(filename)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["provider_id"] != user["id"] or item["status"] != "in_progress":
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        connection.execute(
            "UPDATE requests SET status='completion_submitted', completion_note=?, completion_evidence=? WHERE id=?",
            (note.strip(), json.dumps(paths), request_id))
        add_message(connection, request_id, None, "system",
                    "Task completion has been submitted for review.")
        notify(connection, item["requester_id"],
               f"The task \"{item['title']}\" has been marked as completed. Please review it.",
               f"/requests/{request_id}/review", kind="action")
    return redirect_toast(f"/chat/{request_id}", "Completion submitted — waiting for the owner to review")


@app.get("/requests/{request_id}/review", response_class=HTMLResponse)
def review_page(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["requester_id"] != user["id"] or item["status"] != "completion_submitted":
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
    return render(request, "review_completion.html", item=item)


@app.post("/requests/{request_id}/review/confirm")
def review_confirm(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["requester_id"] != user["id"] or item["status"] != "completion_submitted":
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        settle_exchange(connection, item, 0, "")
        connection.execute("UPDATE requests SET status='completed', completed_at=? WHERE id=?",
                           (now(), request_id))
        add_message(connection, request_id, None, "system",
                    f"Task completed and {item['agreed_value']} credits released.")
        notify(connection, item["provider_id"],
               f"Completion confirmed — {item['agreed_value']} credits released to you.",
               f"/requests/{request_id}", kind="success")
    return redirect_toast(f"/chat/{request_id}", "✓ Task completed — credits released")


@app.get("/requests/{request_id}/dispute", response_class=HTMLResponse)
def dispute_page(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["requester_id"] != user["id"] or item["status"] != "completion_submitted":
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
    return render(request, "dispute_task.html", item=item)


@app.post("/requests/{request_id}/dispute")
async def dispute_submit(request: Request, request_id: int, reason: str = Form(...),
                         description: str = Form(...), desired_outcome: str = Form(...),
                         refund_amount: int = Form(0),
                         evidence: UploadFile | None = File(None)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    path = None
    if evidence and evidence.filename:
        suffix = Path(evidence.filename).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".pdf", ".txt"}:
            data = await evidence.read()
            if len(data) <= 5 * 1024 * 1024:
                filename = f"{secrets.token_hex(12)}{suffix}"
                (UPLOAD_DIR / filename).write_bytes(data)
                path = filename
    if not path:
        return render(request, "error.html",
                      message="Please attach evidence (png, jpg, pdf or txt, up to 5MB) to open a dispute.")
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["requester_id"] != user["id"] or item["status"] != "completion_submitted":
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        refund = max(0, min(refund_amount, item["agreed_value"]))
        if desired_outcome == "full":
            refund = item["agreed_value"]
        midpoint, spread = comparable_stats(connection, "general")
        rec_text = f"Agreed value {item['agreed_value']} credits; recent band {midpoint - spread}-{midpoint + spread}."
        connection.execute(
            """INSERT OR REPLACE INTO disputes(
               request_id,opened_by,description,evidence_path,recommendation,
               requested_refund_amount,reason,desired_outcome,negotiation_status)
               VALUES (?,?,?,?,?,?,?,?,'open')""",
            (request_id, user["id"], description.strip(), path, rec_text,
             refund, reason, desired_outcome),
        )
        connection.execute("UPDATE requests SET status='disputed' WHERE id=?", (request_id,))
        add_message(connection, request_id, None, "system",
                    "Dispute opened. Credits are temporarily frozen — both participants can try to resolve the issue together.")
        notify(connection, item["provider_id"],
               f"A dispute was opened for \"{item['title']}\". Credits are frozen.",
               f"/chat/{request_id}", kind="action")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@app.post("/requests/{request_id}/settlement")
def propose_settlement(request: Request, request_id: int, amount: int = Form(...),
                       reason: str = Form("")):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["status"] != "disputed":
            return RedirectResponse(f"/chat/{request_id}", status_code=303)
        if user["id"] not in (item["requester_id"], item["provider_id"] or 0):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        amount = max(0, min(amount, item["agreed_value"]))
        old = pending_message(connection, request_id, "settlement")
        if old:
            connection.execute("UPDATE messages SET status='countered' WHERE id=?", (old["id"],))
        add_message(connection, request_id, user["id"], "settlement",
                    f"Proposed refund: {amount} credits" + (f" — {reason}" if reason else ""),
                    meta=json.dumps({"amount": amount, "reason": reason}), status="pending")
        notify(connection, other_party(item, user["id"]),
               f"Settlement proposal: {amount} credits refund.", f"/chat/{request_id}", kind="action")
    return RedirectResponse(f"/chat/{request_id}", status_code=303)


@app.post("/settlements/{message_id}/{action}")
def respond_settlement(request: Request, message_id: int, action: str):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        msg = connection.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not msg or msg["kind"] != "settlement" or msg["status"] != "pending" or msg["sender_id"] == user["id"]:
            return redirect_back("", "/messages")
        item = load_request(connection, msg["request_id"])
        if user["id"] not in (item["requester_id"], item["provider_id"] or 0):
            return RedirectResponse(f"/requests/{msg['request_id']}", status_code=303)
        if action == "accept":
            amount = json.loads(msg["meta"] or "{}").get("amount", 0)
            connection.execute("UPDATE messages SET status='accepted' WHERE id=?", (message_id,))
            settle_exchange(connection, item, amount, f"Settlement for request #{item['id']}")
            connection.execute("UPDATE requests SET status='resolved', completed_at=? WHERE id=?",
                               (now(), item["id"]))
            connection.execute(
                "UPDATE disputes SET status='resolved', negotiation_status='resolved', resolved_at=? WHERE request_id=?",
                (now(), item["id"]))
            add_message(connection, item["id"], None, "system",
                        f"Both participants agreed on a settlement ({amount} credits refunded).")
            notify(connection, other_party(item, user["id"]),
                   "Your dispute has been resolved by agreement.", f"/requests/{item['id']}", kind="success")
        elif action == "decline":
            connection.execute("UPDATE messages SET status='declined' WHERE id=?", (message_id,))
            add_message(connection, item["id"], None, "system",
                        "Settlement proposal declined.")
    toast = "Dispute resolved by agreement" if action == "accept" else "Settlement proposal declined"
    return redirect_toast(f"/chat/{msg['request_id']}", toast)


def mediation_suggestion(connection: sqlite3.Connection, item: sqlite3.Row, dispute: sqlite3.Row) -> dict:
    agreed = item["agreed_value"] or 0
    requested = dispute["requested_refund_amount"] or 0
    midpoint, spread = comparable_stats(connection, "general")
    if requested >= agreed:
        suggested = max(1, round(agreed * 0.6))
    else:
        suggested = max(0, round((requested + max(0, agreed - midpoint)) / 2))
    suggested = max(0, min(suggested, agreed))
    reason = (
        f"The agreed value was {agreed} credits, and the requester asked for a {requested} credit refund. "
        f"Recent comparable tasks settled around {midpoint} credits (band {midpoint - spread}–{midpoint + spread}). "
        f"Balancing the requested refund against the value of work done suggests a {suggested} credit refund."
    )
    return {"amount": suggested, "reason": reason}


@app.get("/requests/{request_id}/mediation", response_class=HTMLResponse)
def mediation_page(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["status"] != "disputed":
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        if user["id"] not in (item["requester_id"], item["provider_id"] or 0):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        dispute = connection.execute(
            "SELECT * FROM disputes WHERE request_id=?", (request_id,)).fetchone()
        suggestion = mediation_suggestion(connection, item, dispute)
        messages = connection.execute(
            "SELECT m.*, u.name sender_name FROM messages m LEFT JOIN users u ON u.id=m.sender_id WHERE m.request_id=? ORDER BY m.created_at, m.id",
            (request_id,)).fetchall()
    my_accept = (user["id"] == item["requester_id"] and dispute["requester_accepted"]) or \
                (user["id"] == item["provider_id"] and dispute["provider_accepted"])
    return render(request, "mediation.html", item=item, dispute=dispute,
                  suggestion=suggestion, messages=messages, my_accept=my_accept)


@app.post("/requests/{request_id}/mediation/accept")
def mediation_accept(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["status"] != "disputed":
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        dispute = connection.execute(
            "SELECT * FROM disputes WHERE request_id=?", (request_id,)).fetchone()
        flag = "requester_accepted" if item["requester_id"] == user["id"] else "provider_accepted"
        connection.execute(f"UPDATE disputes SET {flag}=1 WHERE id=?", (dispute["id"],))
        dispute = connection.execute("SELECT * FROM disputes WHERE id=?", (dispute["id"],)).fetchone()
        if dispute["requester_accepted"] and dispute["provider_accepted"]:
            suggestion = mediation_suggestion(connection, item, dispute)
            settle_exchange(connection, item, suggestion["amount"], "AI mediation settlement")
            connection.execute("UPDATE requests SET status='resolved', completed_at=? WHERE id=?",
                               (now(), request_id))
            connection.execute(
                "UPDATE disputes SET status='resolved', negotiation_status='resolved', moderator_decision='mediated', resolved_at=? WHERE id=?",
                (now(), dispute["id"]))
            add_message(connection, request_id, None, "system",
                        f"Both participants accepted the suggested resolution ({suggestion['amount']} credits refunded).")
            notify(connection, other_party(item, user["id"]),
                   "Your dispute has been resolved via mediation.", f"/requests/{request_id}", kind="success")
        else:
            notify(connection, other_party(item, user["id"]),
                   "A mediation resolution is waiting for your response.", f"/requests/{request_id}/mediation", kind="action")
    return RedirectResponse(f"/requests/{request_id}/mediation", status_code=303)


@app.post("/requests/{request_id}/mediation/reject")
def mediation_reject(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if item and item["status"] == "disputed" and user["id"] in (item["requester_id"], item["provider_id"] or 0):
            connection.execute("UPDATE requests SET status='human_review' WHERE id=?", (request_id,))
            connection.execute("UPDATE disputes SET negotiation_status='human_review', review_since=? WHERE request_id=?",
                               (now(), request_id))
            add_message(connection, request_id, None, "system",
                        "This dispute was escalated to a community moderator.")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@app.get("/disputes/{dispute_id}/review", response_class=HTMLResponse)
def dispute_review_page(request: Request, dispute_id: int):
    user = current_user(request)
    if not user or not user["is_moderator"]:
        return RedirectResponse("/", status_code=303)
    with db() as connection:
        dispute = connection.execute(
            """SELECT d.*, r.title, r.agreed_value, r.requester_id, r.provider_id, r.id rid
               FROM disputes d JOIN requests r ON r.id=d.request_id WHERE d.id=?""",
            (dispute_id,)).fetchone()
        if not dispute:
            return render(request, "error.html", message="Dispute not found.")
        item = load_request(connection, dispute["rid"])
        messages = connection.execute(
            "SELECT m.*, u.name sender_name FROM messages m LEFT JOIN users u ON u.id=m.sender_id WHERE m.request_id=? ORDER BY m.created_at, m.id",
            (dispute["rid"],)).fetchall()
        suggestion = mediation_suggestion(connection, item, dispute)
    return render(request, "dispute_review.html", dispute=dispute, item=item,
                  messages=messages, suggestion=suggestion)


@app.post("/disputes/{dispute_id}/decide")
def decide_dispute(request: Request, dispute_id: int, decision: str = Form(...)):
    user = current_user(request)
    if not user or not user["is_moderator"]:
        return RedirectResponse("/", status_code=303)
    with db() as connection:
        dispute = connection.execute(
            "SELECT d.*, r.agreed_value, r.requester_id, r.provider_id, r.id rid FROM disputes d JOIN requests r ON r.id=d.request_id WHERE d.id=?",
            (dispute_id,)).fetchone()
        if dispute and dispute["status"] == "open":
            refund = {"deny": 0, "partial": dispute["requested_refund_amount"] // 2,
                      "full": dispute["requested_refund_amount"]}.get(decision, 0)
            item = load_request(connection, dispute["rid"])
            already_settled = connection.execute(
                "SELECT 1 FROM transactions WHERE request_id=?", (dispute["rid"],)).fetchone()
            if not already_settled:
                settle_exchange(connection, item, refund, f"Moderator decision ({decision}) for dispute #{dispute_id}")
            connection.execute(
                "UPDATE disputes SET status='resolved', moderator_decision=?, negotiation_status='resolved', resolved_at=? WHERE id=?",
                (decision, now(), dispute_id))
            connection.execute("UPDATE requests SET status='resolved', completed_at=? WHERE id=?",
                               (now(), dispute["rid"]))
            add_message(connection, dispute["rid"], None, "system",
                        f"A moderator resolved this dispute ({decision}, {refund} credits refunded).")
            notify(connection, dispute["requester_id"], "Your dispute has been resolved.", f"/requests/{dispute['rid']}", kind="success")
            notify(connection, dispute["provider_id"], "Your dispute has been resolved.", f"/requests/{dispute['rid']}", kind="success")
    return RedirectResponse("/", status_code=303)


@app.post("/requests/{request_id}/dispute-after")
def dispute_after(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["status"] not in ("completed", "resolved"):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        if user["id"] not in (item["requester_id"], item["provider_id"] or 0):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        existing = connection.execute(
            "SELECT id FROM disputes WHERE request_id=? AND status='open'", (request_id,)).fetchone()
        if not existing:
            connection.execute(
                """INSERT INTO disputes(request_id,opened_by,description,negotiation_status,review_since)
                   VALUES (?,?,'After-completion dispute (no escrow held)','human_review',?)""",
                (request_id, user["id"], now()),
            )
            connection.execute("UPDATE requests SET status='human_review' WHERE id=?", (request_id,))
            add_message(connection, request_id, None, "system",
                        "A dispute was opened after completion and sent to a community moderator.")
            notify(connection, other_party(item, user["id"]),
                   f"A dispute was opened for \"{item['title']}\".", f"/requests/{request_id}", kind="info")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@app.post("/requests/{request_id}/pin")
def toggle_pin(request: Request, request_id: int, next: str = Form("")):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        existing = connection.execute(
            "SELECT 1 FROM pins WHERE user_id=? AND request_id=?",
            (user["id"], request_id)).fetchone()
        if existing:
            connection.execute("DELETE FROM pins WHERE user_id=? AND request_id=?",
                               (user["id"], request_id))
        else:
            connection.execute("INSERT INTO pins(user_id,request_id,created_at) VALUES (?,?,?)",
                               (user["id"], request_id, now()))
    return redirect_back(next, f"/requests/{request_id}")


@app.post("/requests/{request_id}/cancel")
def cancel_request(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if item and item["requester_id"] == user["id"] and item["status"] == "open":
            connection.execute("DELETE FROM requests WHERE id=?", (request_id,))
    return RedirectResponse("/my-posts", status_code=303)


@app.get("/notifications/{notification_id}/go")
def notification_go(request: Request, notification_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        note = connection.execute(
            "SELECT * FROM notifications WHERE id=? AND user_id=?",
            (notification_id, user["id"])).fetchone()
        if note:
            connection.execute("UPDATE notifications SET is_read=1 WHERE id=?", (notification_id,))
            return RedirectResponse(note["link"], status_code=303)
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

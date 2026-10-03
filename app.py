from __future__ import annotations

import contextlib
import hashlib
import json
import hmac
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "ioweu.db"
UPLOAD_DIR = BASE_DIR / "uploads"
MIN_BALANCE = -5
# Dispute flow: user-to-user negotiation window, then AI fair-resolution
# proposal window, then human moderation. Set IOWEU_FAST_DISPUTES=<minutes>
# to compress the negotiation window for live demos (mediation gets 1/3).
_FAST_DISPUTES = os.getenv("IOWEU_FAST_DISPUTES")
DISPUTE_NEGOTIATION_WINDOW = (
    timedelta(minutes=float(_FAST_DISPUTES)) if _FAST_DISPUTES else timedelta(days=3)
)
DISPUTE_MEDIATION_WINDOW = (
    timedelta(minutes=float(_FAST_DISPUTES) / 3) if _FAST_DISPUTES else timedelta(days=1)
)

# ---------------------------------------------------------------------------
# Reliability score (separate from credits — measures dependability on IoU)
# ---------------------------------------------------------------------------
RELIABILITY_START = 60
RESTRICTED_TASK_VALUE_CAP = 5
RELIABILITY_EVENTS = {
    "task_completed": 1,
    "thumbs_up": 2,
    "five_task_streak": 2,
    "mutual_cancellation": 0,
    "late_cancellation": -5,
    "no_show": -10,
    "serious_failure": -10,
    "fraudulent_evidence": -20,
    "broken_circle_commitment": -10,
    "guideline_violation": -5,
}

# Content screening — a starter blocklist of clearly prohibited activity.
# This is a keyword filter, not a moderation model, and it is deliberately
# over-blocking rather than under-blocking.
BANNED_TERMS = (
    "fuck", "shit", "bitch", "cunt", "whore", "slut", "rape", "molest",
    "heroin", "cocaine", "methamphetamine", "crystal meth", "mdma", "ketamine",
    "buy drugs", "sell drugs", "drug deal", "deliver weed", "weed delivery",
    "money laundering", "launder money", "hitman", "murder for hire",
    "pornography", "child porn", "prostitution", "escort service", "sex for money",
    "buy a gun", "sell a gun", "firearm for sale", "counterfeit money",
    "fake passport", "fake id card", "pyramid scheme", "ponzi scheme",
)

LEGAL_DOCS = {
    "tos": ("Terms of Service", [
        ("Platform role", "IoU facilitates community exchanges between neighbours. It does not employ providers, and it does not act as a party to any task agreement between residents."),
        ("User responsibility", "Residents must describe their needs honestly, represent their own abilities truthfully, and communicate expectations clearly before agreeing to an exchange."),
        ("Safety", "Illegal and prohibited activity is not allowed. IoU screens obvious keywords, but residents remain responsible for what they post and accept."),
        ("Qualifications", "IoU does not automatically verify professional qualifications. Discuss and evaluate credentials yourself unless a profile is explicitly marked as verified."),
        ("Credits", "IoU credits are community units used inside the platform. They are not legal tender and are not redeemable for cash."),
        ("Circle", "Circle participation is voluntary and depends on reciprocal commitments between residents. No credits move when a Circle completes."),
        ("Cancellation", "Once both parties agree, a commitment is binding subject to the cancellation and dispute rules published here."),
        ("Moderation", "IoU may investigate reports, restrict or suspend accounts, and record a traceable moderation entry for any action taken."),
        ("AI", "AI recommendations — including credit reference bands, evidence flags and mediation proposals — are advisory. A human makes the final decision when either party rejects a proposal."),
        ("Human moderation", "Unresolved disputes can escalate to a human moderator whose decision is final for that case."),
        ("Account suspension", "Serious or repeated violations may lead to restriction or suspension of an account."),
        ("Data", "See the Privacy Notice for what IoU collects and why."),
    ]),
    "privacy": ("Privacy Notice", [
        ("What we collect", "Account data (name, email, region, neighbourhood), profile data you choose to add, task and Circle activity, ledger entries, chat messages needed to run an exchange, and any evidence files you upload."),
        ("Why we collect it", "To match neighbours, hold and release credits, resolve disputes, keep the ledger honest, and keep accounts secure."),
        ("Identity verification", "This prototype uses simulated verification. No genuine identity document is required or stored during the demo."),
        ("What we do not publish", "Your exact address is never shown publicly. Tasks display an approximate neighbourhood only."),
        ("Private address handling", "Contact details and precise locations are only exchanged between matched participants, and only when you choose to share them."),
        ("Chat and evidence", "Task chats and uploaded evidence may be reviewed by a moderator when a dispute is raised. We tell you this before you agree to a task."),
        ("AI processing", "Required processing powers credit reference bands, evidence flags and mediation proposals. Anything beyond that is optional."),
        ("Optional analytics", "Allowing anonymized usage data to improve recommendations is off by default and stays under your control."),
        ("Your rights", "You can download your data at any time and request account deletion. Some records may remain in de-identified form to preserve ledger integrity or resolve open disputes."),
    ]),
    "guidelines": ("Community Guidelines", [
        ("Respect", "Treat neighbours respectfully, in chat and in person."),
        ("Honest descriptions", "Describe tasks accurately, including effort, urgency and any special requirements."),
        ("No illegal activity", "Do not offer or request prohibited services."),
        ("Credentials", "Do not misrepresent qualifications or experience."),
        ("Credits", "Do not manipulate credits with fake exchanges between accounts you control."),
        ("Evidence", "Do not submit evidence you did not create, or evidence copied from elsewhere."),
        ("Privacy", "Do not share another resident's personal information without permission."),
        ("Harassment", "Harassment of any kind is grounds for restriction."),
        ("Circle commitments", "Honour Circle commitments — someone is relying on you."),
        ("Communicate early", "If plans change, say so early rather than disappearing."),
    ]),
    "disputes": ("Dispute Policy", [
        ("Direct resolution first", "When an issue is raised, both participants get a negotiation window to settle it themselves. Nothing is escalated automatically."),
        ("AI-assisted mediation", "If you cannot agree, IoU proposes a fair resolution with a written explanation. Both participants must accept it."),
        ("Human moderation", "If either party rejects the proposal, a human moderator decides. Outcomes can be no refund, partial refund, full refund, a redo, or a request for more evidence."),
        ("Credits stay frozen", "Disputed credits stay held until the case closes."),
        ("Moderator decisions", "Every moderator action creates a record. Moderators cannot change balances without leaving a traceable moderation entry."),
        ("Reliability impact", "Serious failures and confirmed fraudulent evidence reduce reliability. Repeated failures can restrict or suspend an account."),
        ("Appeals", "Restricted or suspended accounts may appeal. A moderator reviews the appeal and records the result."),
    ]),
    "circle": ("Circle Agreement", [
        ("Voluntary", "Joining a Circle is voluntary and requires every participant to accept."),
        ("No credits", "No IoU credits are exchanged when a Circle completes. That is the point of the Circle."),
        ("Your commitment", "You promise to help with the task you chose. You will not be asked to do your own task."),
        ("Not every need is a match", "The system does not judge whether you can do every task — you choose what you are comfortable helping with."),
        ("Completion still applies", "Each task is still completed normally: mark complete, then the requester confirms. Circle tasks do not require a photo if both parties agree it is unnecessary."),
        ("If someone withdraws", "IoU tries to find a replacement. If none exists, remaining tasks return to the board and the participant who withdrew forfeits standing that affects future Circles."),
        ("Fallback to credits", "If someone received help but did not give theirs, the completed contribution can be converted into a normal credit claim using its displayed reference value."),
    ]),
    "ai": ("AI Transparency Notice", [
        ("Advisory only", "AI recommendations — credit reference bands, evidence flags, mediation proposals — are advisory. They never override you."),
        ("Credit reference band", "Calculated from agreed values for similar completed tasks in the same category over the last 90 days, adjusted for estimated effort, complexity and quality."),
        ("Evidence screening", "Checks file type, size, format consistency, whether the same file was submitted before, and basic image signals. It flags; it does not declare fraud."),
        ("Mediation proposal", "Weighs the agreed value, the requested refund, how much communication took place, whether evidence was supplied, and how similar past disputes resolved."),
        ("Limits", "The system cannot reliably detect every downloaded or AI-generated image. Flags are supporting information for a human decision, never proof."),
        ("Human fallback", "Either party can reject a proposal and escalate to a human moderator."),
        ("Data use", "Required processing happens when you use those features. Optional analytics for improving recommendations is off unless you turn it on."),
    ]),
}


def find_banned_term(*parts: str) -> str | None:
    """Return the first prohibited keyword found in the supplied text."""
    squashed = " ".join(
        re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(part or "").lower())).strip()
        for part in parts
    )
    for term in BANNED_TERMS:
        if term in squashed:
            return term
    return None


def reliability_label(score: int) -> str:
    if score >= 80:
        return "Excellent"
    if score >= 60:
        return "Strong"
    if score >= 30:
        return "Limited"
    if score >= 1:
        return "Restricted"
    return "Suspended"


def reliability_tier(score: int) -> str:
    if score >= 30:
        return "normal"
    if score >= 1:
        return "restricted"
    return "suspended"


def user_tier(user: Any) -> str:
    """Effective standing: an explicit moderator suspension always wins."""
    if user is not None and "account_status" in user.keys() and user["account_status"] == "suspended":
        return "suspended"
    score = user["reliability"] if user is not None and user["reliability"] is not None else RELIABILITY_START
    return reliability_tier(score)


def user_reliability(user: Any) -> int:
    if user is None or "reliability" not in user.keys() or user["reliability"] is None:
        return RELIABILITY_START
    return user["reliability"]


def apply_reliability_event(connection: sqlite3.Connection, user_id: int, event_type: str,
                           change: int, reason: str, request_id: int | None = None) -> int:
    """Move a resident's reliability and record why. Returns the new score."""
    row = connection.execute("SELECT reliability FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return RELIABILITY_START
    old = row["reliability"] if row["reliability"] is not None else RELIABILITY_START
    new = max(0, min(100, old + change))
    if new == old and change != 0:
        return old
    connection.execute("UPDATE users SET reliability=? WHERE id=?", (new, user_id))
    connection.execute(
        """INSERT INTO reliability_events(user_id,request_id,event_type,score_change,reason,created_at)
           VALUES (?,?,?,?,?,?)""",
        (user_id, request_id, event_type, new - old, reason, now()),
    )
    if change < 0 and reliability_tier(new) != reliability_tier(old):
        notify(connection, user_id,
               f"Your reliability moved to {new}/100 ({reliability_label(new)}). {reason}",
               "/profile", kind="warning")
    return new


def is_blocked(connection: sqlite3.Connection, a: int, b: int) -> bool:
    if not a or not b or a == b:
        return False
    return bool(connection.execute(
        "SELECT 1 FROM blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
        (a, b, b, a)).fetchone())


def circle_progress(connection: sqlite3.Connection, proposal_id: int) -> dict[str, int]:
    row = connection.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN r.status IN ('completed','resolved') THEN 1 ELSE 0 END) done
           FROM chain_tasks ct JOIN requests r ON r.id=ct.request_id
           WHERE ct.proposal_id=?""",
        (proposal_id,),
    ).fetchone()
    return {"total": row["total"] or 0, "done": row["done"] or 0}


def close_circle_if_complete(connection: sqlite3.Connection, proposal_id: int) -> None:
    """Mark a Circle complete and celebrate when every linked task is done."""
    if not proposal_id:
        return
    proposal = connection.execute(
        "SELECT * FROM chain_proposals WHERE id=?", (proposal_id,)).fetchone()
    if not proposal or proposal["status"] != "active":
        return
    progress = circle_progress(connection, proposal_id)
    if not progress["total"] or progress["done"] < progress["total"]:
        return
    connection.execute(
        "UPDATE chain_proposals SET status='completed', cancelled_at=NULL WHERE id=?",
        (proposal_id,),
    )
    members = connection.execute(
        "SELECT user_id FROM chain_members WHERE proposal_id=? AND response='accepted'",
        (proposal_id,)).fetchall()
    summary = (f"{progress['total']} needs fulfilled · {progress['total']} neighbours helped "
               f"· 0 credits exchanged")
    for member in members:
        notify(connection, member["user_id"],
               f"Circle complete — {summary}", f"/chains/{proposal_id}", kind="success")
        apply_reliability_event(
            connection, member["user_id"], "circle_completed", 1,
            "You completed your Circle commitment", None)


def assign_circle_tasks(connection: sqlite3.Connection, proposal_id: int) -> list[dict[str, Any]]:
    """Pair each accepted member with a task they are willing to do.

    Preference order: members with the fewest willing helpers get matched first,
    and nobody is ever assigned their own task. Falls back to the natural
    rotation when nobody has picked preferences yet.
    """
    members = connection.execute(
        """SELECT cm.user_id, cm.request_id FROM chain_members cm
           WHERE cm.proposal_id=? AND cm.response='accepted' ORDER BY cm.position""",
        (proposal_id,),
    ).fetchall()
    if len(members) < 2:
        return []
    own: dict[int, int] = {m["user_id"]: m["request_id"] for m in members}
    chosen: dict[int, list[int]] = {
        m["user_id"]: [
            row["request_id"] for row in connection.execute(
                "SELECT request_id FROM chain_interest WHERE proposal_id=? AND user_id=?",
                (proposal_id, m["user_id"]))
        ]
        for m in members
    }
    order = [m["user_id"] for m in members]
    remaining = list(order)
    remaining.sort(key=lambda uid: len([r for r in chosen.get(uid, []) if r != own[uid]]))
    taken: set[int] = set()
    assignment: list[dict[str, Any]] = []
    for helper in remaining:
        options = [r for r in chosen.get(helper, []) if r != own[helper] and r not in taken]
        if not options:
            options = [m["request_id"] for m in members
                       if m["request_id"] != own[helper] and m["request_id"] not in taken]
        if not options:
            continue
        task_id = options[0]
        taken.add(task_id)
        owner = next(m["user_id"] for m in members if m["request_id"] == task_id)
        value = connection.execute(
            "SELECT requester_value FROM requests WHERE id=?", (task_id,)).fetchone()["requester_value"] or 0
        assignment.append({"request_id": task_id, "helper_id": helper,
                           "requester_id": owner, "value": value})
    connection.execute("DELETE FROM chain_tasks WHERE proposal_id=?", (proposal_id,))
    for row in assignment:
        connection.execute(
            """INSERT INTO chain_tasks(proposal_id,request_id,requester_id,helper_id,value)
               VALUES (?,?,?,?,?)""",
            (proposal_id, row["request_id"], row["requester_id"], row["helper_id"], row["value"]),
        )
    return assignment
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
    "others",
)
GENRE_EMOJI = {
    "daily life": "🏠", "education": "📚", "technology": "💻", "creative": "🎨",
    "repair & diy": "🔧", "transport": "🚗", "companionship": "🧑‍🤝‍🧑",
    "family & kids": "👶", "pets & animals": "🐶", "community": "🌱",
    "others": "🗂️",
}
# Categories where a photo usually isn't appropriate — the requester's own
# confirmation is accepted as proof instead.
NO_EVIDENCE_CATEGORIES = {"education", "companionship"}
POLICY_VERSION = "2026-10-prototype"
REPORT_CATEGORIES = (
    "harassment", "unsafe behaviour", "scam or fraud", "inappropriate task",
    "illegal activity", "false evidence", "discrimination", "other",
)
# Spec 31: Circle candidates must agree within roughly ±2 credits of each other.
CIRCLE_VALUE_TOLERANCE = 2
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
    # Never fall back to a static secret: sessions would be forgeable.
    secret_key=os.getenv("IOWEU_SESSION_SECRET") or secrets.token_urlsafe(32),
    max_age=60 * 60 * 24 * 14,
)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["slug"] = lambda value: value.replace("&", "and").replace(" ", "-")
templates.env.filters["from_json"] = lambda value: json.loads(value) if value else []


def _hk(dt_like: str, fmt: str) -> str:
    """Render a stored UTC timestamp in Hong Kong local time."""
    try:
        parsed = datetime.fromisoformat(dt_like)
        return parsed.astimezone(timezone(timedelta(hours=8))).strftime(fmt)
    except (TypeError, ValueError):
        return dt_like or ""


templates.env.filters["hktime"] = lambda value: _hk(value, "%H:%M")
templates.env.filters["hkdate"] = lambda value: _hk(value, "%d %b %Y")


def _file_size(stored_name: str) -> str:
    try:
        size = (UPLOAD_DIR / stored_name).stat().st_size
    except OSError:
        return ""
    if size >= 1024 * 1024:
        return f"{size / 1048576:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


templates.env.globals["file_size"] = _file_size


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
                evidence_path TEXT, evidence_paths TEXT,
                status TEXT NOT NULL DEFAULT 'open',
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
            CREATE TABLE IF NOT EXISTS request_participants (
                request_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(request_id, user_id),
                FOREIGN KEY(request_id) REFERENCES requests(id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS pins (
                user_id INTEGER NOT NULL, request_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id, request_id)
            );
            CREATE TABLE IF NOT EXISTS evidence_audits (
                id INTEGER PRIMARY KEY, request_id INTEGER, dispute_id INTEGER,
                filename TEXT NOT NULL, stored_path TEXT NOT NULL,
                sha256 TEXT NOT NULL, mime_type TEXT, byte_size INTEGER NOT NULL,
                screening_status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chain_proposals (
                id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
                balancing_amount INTEGER NOT NULL DEFAULT 0,
                balance_explanation TEXT NOT NULL, created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL, activated_at TEXT, cancelled_at TEXT
            );
            CREATE TABLE IF NOT EXISTS chain_members (
                proposal_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                request_id INTEGER NOT NULL, position INTEGER NOT NULL,
                response TEXT NOT NULL DEFAULT 'pending', responded_at TEXT,
                PRIMARY KEY(proposal_id, user_id),
                FOREIGN KEY(proposal_id) REFERENCES chain_proposals(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(request_id) REFERENCES requests(id)
            );
            CREATE TABLE IF NOT EXISTS chain_tasks (
                proposal_id INTEGER NOT NULL, request_id INTEGER NOT NULL,
                requester_id INTEGER NOT NULL, helper_id INTEGER NOT NULL,
                value INTEGER NOT NULL, PRIMARY KEY(proposal_id, request_id),
                FOREIGN KEY(proposal_id) REFERENCES chain_proposals(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS chain_messages (
                id INTEGER PRIMARY KEY, proposal_id INTEGER NOT NULL,
                sender_id INTEGER, body TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(proposal_id) REFERENCES chain_proposals(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS reliability_events (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, request_id INTEGER,
                event_type TEXT NOT NULL, score_change INTEGER NOT NULL,
                reason TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'report',
                reporter_id INTEGER, reported_user_id INTEGER, reported_request_id INTEGER,
                category TEXT NOT NULL, description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', resolution TEXT,
                moderator_id INTEGER, created_at TEXT NOT NULL, resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS blocks (
                blocker_id INTEGER NOT NULL, blocked_id INTEGER NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(blocker_id, blocked_id)
            );
            CREATE TABLE IF NOT EXISTS consents (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                policy TEXT NOT NULL, version TEXT NOT NULL, accepted_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chain_interest (
                proposal_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                request_id INTEGER NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(proposal_id, user_id, request_id)
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
            ("consent_analytics", "INTEGER NOT NULL DEFAULT 0"),
            ("reliability", f"INTEGER NOT NULL DEFAULT {RELIABILITY_START}"),
            ("account_status", "TEXT NOT NULL DEFAULT 'active'"),
            ("analytics_consent", "INTEGER NOT NULL DEFAULT 0"),
            ("username", "TEXT"),
            ("bio", "TEXT"),
            ("verification_identity", "TEXT NOT NULL DEFAULT 'unverified'"),
            ("verification_neighbourhood", "TEXT NOT NULL DEFAULT 'unverified'"),
            ("vis_photo", "INTEGER NOT NULL DEFAULT 1"),
            ("vis_neighbourhood", "INTEGER NOT NULL DEFAULT 1"),
            ("vis_skills", "INTEGER NOT NULL DEFAULT 1"),
            ("vis_bio", "INTEGER NOT NULL DEFAULT 1"),
            ("vis_completed", "INTEGER NOT NULL DEFAULT 1"),
            ("vis_circle", "INTEGER NOT NULL DEFAULT 1"),
            ("circle_enabled", "INTEGER NOT NULL DEFAULT 1"),
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
            ("provider_completed_at", "TEXT"), ("requester_confirmed_at", "TEXT"),
            ("release_at", "TEXT"), ("proof_path", "TEXT"), ("proof_status", "TEXT"),
            ("dispute_eligible", "INTEGER NOT NULL DEFAULT 1"),
            ("dispute_window_days", "INTEGER NOT NULL DEFAULT 3"),
            ("settlement_value", "INTEGER"),
            ("circle_id", "INTEGER"),
            ("cancel_requested_by", "INTEGER"),
            ("cancel_reason", "TEXT"),
            ("cancel_compensation", "INTEGER NOT NULL DEFAULT 0"),
            ("evidence_required", "INTEGER NOT NULL DEFAULT 1"),
            ("duration_minutes", "INTEGER NOT NULL DEFAULT 0"),
            ("community_id", "INTEGER"),
            ("visibility", "TEXT NOT NULL DEFAULT 'public'"),
        ):
            if column not in columns:
                connection.execute(f"ALTER TABLE requests ADD COLUMN {column} {definition}")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS communities(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'custom',
                district TEXT,
                color TEXT NOT NULL DEFAULT '#cfe7e8',
                code TEXT UNIQUE,
                water_level INTEGER NOT NULL DEFAULT 18,
                jars_filled INTEGER NOT NULL DEFAULT 0,
                exchanges INTEGER NOT NULL DEFAULT 0,
                credits_total INTEGER NOT NULL DEFAULT 0,
                filled INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS community_members(
                community_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                joined_at TEXT,
                PRIMARY KEY (community_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS community_contributions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                community_id INTEGER NOT NULL,
                request_id INTEGER NOT NULL,
                user_id INTEGER,
                credits INTEGER NOT NULL DEFAULT 0,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_community_members_user ON community_members(user_id);
            CREATE INDEX IF NOT EXISTS idx_requests_community ON requests(community_id);
            """
        )
        notif_columns = {row["name"] for row in connection.execute("PRAGMA table_info(notifications)")}
        if "kind" not in notif_columns:
            connection.execute("ALTER TABLE notifications ADD COLUMN kind TEXT NOT NULL DEFAULT 'info'")
        message_columns = {row["name"] for row in connection.execute("PRAGMA table_info(messages)")}
        for column, definition in (("meta", "TEXT"), ("status", "TEXT"), ("attachment", "TEXT")):
            if column not in message_columns:
                connection.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")
        connection.execute(
            f"UPDATE users SET reliability={RELIABILITY_START} WHERE reliability IS NULL")
        dispute_cols = {row["name"] for row in connection.execute("PRAGMA table_info(disputes)")}
        for column, definition in (
                ("reason", "TEXT"), ("desired_outcome", "TEXT"),
                ("requester_accepted", "INTEGER NOT NULL DEFAULT 0"),
                ("provider_accepted", "INTEGER NOT NULL DEFAULT 0"),
                ("review_since", "TEXT"),
                ("negotiation_deadline", "TEXT"),
                ("mediation_deadline", "TEXT"),
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
        if "evidence_paths" not in dispute_columns:
            connection.execute("ALTER TABLE disputes ADD COLUMN evidence_paths TEXT")
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
                ("Alex Resident", "alex@example.com", "demo123", 10, 0),
                ("Sam Resident", "sam@example.com", "demo123", 10, 0),
                ("Morgan Moderator", "moderator@example.com", "demo123", 10, 1),
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
        effort_hint = max(2, round(effort_minutes / 15))
        midpoint = max(effort_hint, min(midpoint, effort_hint * 5))
        spread = max(1, min(spread, midpoint // 2 or 1))
        return {
            "recommended": midpoint,
            "range": [max(1, midpoint - spread), midpoint + spread],
            "benchmark": midpoint,
            "adjustments": {"effort": 1.0, "complexity": 1.0, "quality": 1.0},
            "sample_size": 0,
            "explanation": (
                f"No completed {category.lower()} comparisons are available yet; "
                f"the fallback reference uses estimated effort of {effort_minutes} minutes."
            ),
        }
    benchmark = rows[len(rows) // 2]["value"]
    measured_effort = [row["effort_minutes"] for row in rows if row["effort_minutes"]]
    typical_effort = sum(measured_effort) / len(measured_effort) if measured_effort else 30
    effort_factor = max(0.5, min(1.5, effort_minutes / typical_effort))
    complexity_factor = max(0.7, min(1.3, 1 + 0.1 * (complexity - 3)))
    quality_factor = max(0.8, min(1.2, 1 + 0.05 * (quality_score - 3)))
    suggested = max(1, round(benchmark * effort_factor * complexity_factor * quality_factor))
    effort_hint = max(2, round(effort_minutes / 15))
    suggested = max(effort_hint, min(suggested, effort_hint * 5))
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
        "explanation": (
            f"Based on {len(rows)} comparable {category.lower()} task(s), "
            f"estimated effort of {effort_minutes} minutes, complexity {complexity}/5, "
            f"and a historical benchmark of {benchmark} credits."
        ),
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
            context["unread_msgs"] = unread_message_count(connection, user["id"])
            context["unread_notifs"] = connection.execute(
                "SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0",
                (user["id"],)).fetchone()["c"]
    return templates.TemplateResponse(request=request, name=template, context=context)


def expire_negotiations(connection: sqlite3.Connection) -> None:
    expired = connection.execute(
        """SELECT id, title, requester_id, provider_id FROM requests
           WHERE status='negotiating' AND negotiation_deadline IS NOT NULL
             AND negotiation_deadline <= ?""",
        (now(),),
    ).fetchall()
    for item in expired:
        connection.execute(
            """UPDATE requests
               SET status='open', provider_id=NULL, provider_value=NULL,
                   provider_buffer=NULL, negotiation_deadline=NULL
               WHERE id=?""",
            (item["id"],),
        )
        add_message(
            connection, item["id"], None, "system",
            "The negotiation window closed. The request is open on the board again.",
        )
        for user_id in (item["requester_id"], item["provider_id"]):
            notify(connection, user_id,
                   f"The negotiation window closed for \"{item['title']}\" — the request is open again.",
                   f"/requests/{item['id']}", kind="info")


def ledger_entry(connection: sqlite3.Connection, user_id: int, request_id: int | None,
                 kind: str, amount: int, description: str) -> None:
    connection.execute(
        "INSERT INTO ledger(user_id,request_id,kind,amount,description,created_at) VALUES (?,?,?,?,?,?)",
        (user_id, request_id, kind, amount, description, now()),
    )


def add_message(connection: sqlite3.Connection, request_id: int, sender_id: int | None,
                kind: str, body: str, meta: str | None = None, status: str | None = None,
                attachment: str | None = None) -> int:
    cursor = connection.execute(
        """INSERT INTO messages(request_id,sender_id,kind,body,meta,status,attachment,created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (request_id, sender_id, kind, body, meta, status, attachment, now()),
    )
    return cursor.lastrowid


def inspect_image(data: bytes, suffix: str) -> list[str]:
    """Cheap structural signals that an image may be synthetic or reused.

    These are flags for review, never a fraud verdict.
    """
    if suffix not in {".png", ".jpg", ".jpeg"}:
        return []
    flags: list[str] = []
    if len(data) < 12_000 and len(data) > 2_000:
        flags.append("File is unusually small for a photo, which is common in generated images")
    exif = data[: 64 * 1024].find(b"Exif\x00\x00")
    camera_tags = [tag for tag in (b"Android", b"iPhone", b"Canon", b"NIKON", b"samsung")
                   if tag in data[: 128 * 1024]]
    if exif == -1 and not camera_tags:
        flags.append("No camera metadata found, so the photo cannot be linked to a device")
    return flags


def screen_evidence(filename: str, content_type: str | None, data: bytes,
                    connection: sqlite3.Connection) -> tuple[str, list[str]]:
    """Validate an upload and return (stored_name, review_flags).

    Hard failures still raise; uncertain signals become reviewable flags so a
    human decides rather than the system declaring fraud.
    """
    suffix = Path(filename).suffix.lower()
    allowed = {".png", ".jpg", ".jpeg", ".pdf", ".txt"}
    if suffix not in allowed:
        raise ValueError("Evidence must be a PNG, JPG, PDF, or TXT file.")
    if not data or len(data) > 5 * 1024 * 1024:
        raise ValueError("Evidence must be non-empty and smaller than 5 MB.")
    signatures = {
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": data.startswith(b"\xff\xd8\xff"),
        ".jpeg": data.startswith(b"\xff\xd8\xff"),
        ".pdf": data.startswith(b"%PDF-"),
        ".txt": True,
    }
    if not signatures[suffix]:
        raise ValueError("Evidence content does not match its file type.")
    if suffix in {".png", ".jpg", ".jpeg"} and content_type not in {
        "image/png", "image/jpeg", "image/jpg"
    }:
        raise ValueError("Image evidence has an invalid MIME type.")
    digest = hashlib.sha256(data).hexdigest()
    flags: list[str] = []
    duplicate = connection.execute(
        """SELECT 1 FROM requests WHERE completion_evidence LIKE ? LIMIT 1""",
        (f"%{digest}%",),
    ).fetchone() or connection.execute(
        """SELECT 1 FROM disputes WHERE evidence_paths LIKE ? LIMIT 1""",
        (f"%{digest}%",),
    ).fetchone()
    if duplicate:
        flags.append("An identical file was submitted before, so this may be reused evidence")
    flags.extend(inspect_image(data, suffix))
    stored = f"{digest}{suffix}"
    connection.execute(
        """INSERT INTO evidence_audits(filename,stored_path,sha256,mime_type,byte_size,
           screening_status,created_at) VALUES (?,?,?,?,?,?,?)""",
        (filename, stored, digest, content_type, len(data),
         "requires_review" if flags else "accepted", now()),
    )
    return stored, flags


def negotiation_bounds(item: sqlite3.Row, is_owner: bool) -> tuple[int, int]:
    if is_owner and item["provider_value"] is not None:
        value, buffer = item["provider_value"], item["provider_buffer"] or 0
    else:
        value, buffer = item["requester_value"], item["requester_buffer"] or 0
    return max(1, value - buffer), value + buffer


def notify(connection: sqlite3.Connection, user_id: int | None, body: str, link: str,
           kind: str = "info") -> None:
    if not user_id:
        return
    connection.execute(
        "INSERT INTO notifications(user_id,body,link,kind,created_at) VALUES (?,?,?,?,?)",
        (user_id, body, link, kind, now()),
    )


def detect_circular_matches(connection: sqlite3.Connection, max_size: int = 6) -> list[dict[str, Any]]:
    """Group open tasks with similar values for voluntary task circulation."""
    if max_size < 3:
        return []
    requests = connection.execute(
        """SELECT r.*, u.name requester_name FROM requests r
           JOIN users u ON u.id=r.requester_id
           WHERE r.status='open' AND r.provider_id IS NULL
           ORDER BY r.requester_value, r.id""").fetchall()
    by_user: dict[int, sqlite3.Row] = {}
    for item in requests:
        by_user.setdefault(item["requester_id"], item)
    candidates = list(by_user.values())
    blocked_pairs = {
        (row["blocker_id"], row["blocked_id"]) for row in connection.execute(
            "SELECT blocker_id, blocked_id FROM blocks")
    }
    blocked_pairs |= {(b, a) for a, b in blocked_pairs}
    matches = []
    seen: set[tuple[int, ...]] = set()
    for start in range(len(candidates)):
        for size in range(min(max_size, len(candidates) - start), 2, -1):
            group = candidates[start:start + size]
            users = [item["requester_id"] for item in group]
            if any((a, b) in blocked_pairs for a in users for b in users if a != b):
                continue
            values = [item["requester_value"] or 0 for item in group]
            if max(values) - min(values) > CIRCLE_VALUE_TOLERANCE * 2:
                continue
            request_ids = tuple(item["id"] for item in group)
            if request_ids in seen:
                continue
            seen.add(request_ids)
            average = round(sum(values) / len(values))
            matches.append({
                "users": tuple(item["requester_id"] for item in group),
                "requests": list(request_ids),
                "values": values,
                "titles": [item["title"] for item in group],
                "balancing_amount": max(values) - min(values),
                "balance_explanation": (
                    f"These {len(group)} open tasks are between {min(values)} and {max(values)} credits. "
                    f"Everyone can choose one other task to complete; the average is {average} credits."
                ),
            })
            break
    return matches[:10]


def expire_chain_proposals(connection: sqlite3.Connection) -> None:
    expired = connection.execute(
        """SELECT id FROM chain_proposals
           WHERE status='pending' AND expires_at <= ?""", (now(),)).fetchall()
    for proposal in expired:
        connection.execute(
            "UPDATE chain_proposals SET status='expired', cancelled_at=? WHERE id=?",
            (now(), proposal["id"]),
        )
        members = connection.execute(
            "SELECT user_id FROM chain_members WHERE proposal_id=?", (proposal["id"],)
        ).fetchall()
        for member in members:
            notify(connection, member["user_id"],
                   "A circular task-chain invitation expired.",
                   f"/chains/{proposal['id']}", kind="info")


def create_chain_proposal(connection: sqlite3.Connection, match: dict[str, Any],
                          days: int = 3) -> int:
    """Persist an invitation only; requests, escrow, and credits remain untouched."""
    existing = connection.execute(
        """SELECT cp.id FROM chain_proposals cp
           JOIN chain_members cm ON cm.proposal_id=cp.id
           WHERE cp.status='pending'
           GROUP BY cp.id
           HAVING COUNT(*)=? AND SUM(cm.request_id IN (%s))=?"""
        % ",".join("?" for _ in match["requests"]),
        (len(match["requests"]), *match["requests"], len(match["requests"])),
    ).fetchone()
    if existing:
        return existing["id"]
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")
    cursor = connection.execute(
        """INSERT INTO chain_proposals(status,balancing_amount,balance_explanation,created_at,expires_at)
           VALUES ('pending',?,?,?,?)""",
        (match["balancing_amount"], match["balance_explanation"], now(), expires),
    )
    proposal_id = cursor.lastrowid
    for position, user_id in enumerate(match["users"]):
        connection.execute(
            """INSERT INTO chain_members(proposal_id,user_id,request_id,position)
               VALUES (?,?,?,?)""",
            (proposal_id, user_id, match["requests"][position], position),
        )
    # A helps B, B helps C, C helps A.
    for position, request_id in enumerate(match["requests"]):
        connection.execute(
            """INSERT INTO chain_tasks(proposal_id,request_id,requester_id,helper_id,value)
               VALUES (?,?,?,?,?)""",
            (proposal_id, request_id, match["users"][position],
            match["users"][(position - 1) % len(match["users"])], match["values"][position]),
        )
    for user_id in match["users"]:
        notify(connection, user_id,
               "You have been invited to a circular task chain. Review all tasks before accepting.",
               f"/chains/{proposal_id}", kind="action")
    return proposal_id


def ensure_chain_invitations(connection: sqlite3.Connection) -> None:
    for match in detect_circular_matches(connection):
        create_chain_proposal(connection, match)


def chain_for_user(connection: sqlite3.Connection, proposal_id: int, user_id: int):
    return connection.execute(
        """SELECT cp.*, cm.user_id, cm.request_id, cm.position, cm.response,
                  r.title, r.description, r.category, r.requester_value,
                  u.name requester_name
           FROM chain_proposals cp JOIN chain_members cm ON cm.proposal_id=cp.id
           JOIN requests r ON r.id=cm.request_id JOIN users u ON u.id=cm.user_id
           WHERE cp.id=? AND EXISTS (SELECT 1 FROM chain_members x
                                     WHERE x.proposal_id=cp.id AND x.user_id=?)
             AND (cp.status != 'active' OR cm.response='accepted')
           ORDER BY cm.position""", (proposal_id, user_id)).fetchall()


STATUS_LABELS = {
    "open": "Waiting for Helper",
    "negotiating": "Negotiating",
    "agreement_pending": "Agreement Pending",
    "confirmed": "Confirmed",
    "scheduled": "Scheduled",
    "in_progress": "In Progress",
    "completion_submitted": "Completion Submitted",
    "completed_pending_release": "Dispute Window",
    "completed": "Completed",
    "disputed": "Disputed",
    "mediation": "Fair Resolution",
    "human_review": "Human Review",
    "resolved": "Resolved",
    "cancelled": "Cancelled",
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
    "completion_submitted": 3, "completed_pending_release": 3, "completed": 3, "resolved": 3,
    "disputed": 4, "mediation": 4, "human_review": 4, "cancelled": 0,
}

STATUS_STEP = {
    "open": 0, "negotiating": 1, "agreement_pending": 2, "confirmed": 4,
    "scheduled": 5, "in_progress": 6, "completion_submitted": 7,
    "completed": 9, "disputed": 7, "mediation": 8, "human_review": 8,
    "resolved": 9, "cancelled": 0,
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
           WHERE (r.requester_id=? OR r.provider_id=?
                  OR EXISTS (SELECT 1 FROM request_participants rp
                             WHERE rp.request_id=r.id AND rp.user_id=?))
             AND m.sender_id IS NOT NULL AND m.sender_id != ?
             AND m.id > COALESCE(tr.last_read_id, 0)""",
        (user_id, user_id, user_id, user_id, user_id),
    ).fetchone()["c"]


def notify_moderators(connection: sqlite3.Connection, body: str, link: str) -> None:
    for moderator in connection.execute("SELECT id FROM users WHERE is_moderator=1").fetchall():
        notify(connection, moderator["id"], body, link, kind="action")


def advance_disputes(connection: sqlite3.Connection) -> None:
    """Move disputes through timed stages.

    disputed (3-day user negotiation) -> mediation (AI fair resolution,
    1-day window) -> human_review (community moderator decides).
    """
    current = now()
    to_mediation = connection.execute(
        """SELECT d.id, d.request_id, r.title, r.requester_id, r.provider_id
           FROM disputes d JOIN requests r ON r.id=d.request_id
           WHERE d.status='open' AND d.negotiation_deadline IS NOT NULL
             AND d.negotiation_deadline <= ? AND r.status='disputed'""",
        (current,),
    ).fetchall()
    for row in to_mediation:
        mediation_deadline = (
            datetime.now(timezone.utc) + DISPUTE_MEDIATION_WINDOW
        ).isoformat(timespec="seconds")
        connection.execute(
            "UPDATE disputes SET negotiation_status='mediation', mediation_deadline=? WHERE id=?",
            (mediation_deadline, row["id"]),
        )
        connection.execute(
            "UPDATE requests SET status='mediation' WHERE id=?", (row["request_id"],))
        add_message(
            connection, row["request_id"], None, "system",
            "The three-day resolution window closed without agreement. "
            "IoU has prepared a Fair Resolution proposal for both participants.",
        )
        for user_id in (row["requester_id"], row["provider_id"]):
            notify(connection, user_id,
                   f"IoU proposed a Fair Resolution for \"{row['title']}\" — review it within one day.",
                   f"/requests/{row['request_id']}/mediation", kind="action")
    to_human = connection.execute(
        """SELECT d.id, d.request_id, r.title, r.requester_id, r.provider_id
           FROM disputes d JOIN requests r ON r.id=d.request_id
           WHERE d.status='open' AND d.negotiation_status='mediation'
             AND d.mediation_deadline IS NOT NULL
             AND d.mediation_deadline <= ? AND r.status='mediation'""",
        (current,),
    ).fetchall()
    for row in to_human:
        connection.execute(
            "UPDATE requests SET status='human_review' WHERE id=?", (row["request_id"],))
        connection.execute(
            "UPDATE disputes SET negotiation_status='human_review', review_since=? WHERE id=?",
            (current, row["id"]),
        )
        add_message(
            connection, row["request_id"], None, "system",
            "The Fair Resolution was not accepted by both participants. "
            "A community moderator will now make the final decision.",
        )
        for user_id in (row["requester_id"], row["provider_id"]):
            notify(connection, user_id,
                   f"\"{row['title']}\" was escalated to a community moderator.",
                   f"/requests/{row['request_id']}", kind="info")
        notify_moderators(
            connection,
            f"A dispute for \"{row['title']}\" needs your final decision.",
            f"/disputes/{row['id']}/review",
        )


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
    threading.Thread(target=_release_worker, name="escrow-release-worker", daemon=True).start()


def _release_worker() -> None:
    while True:
        try:
            with db() as connection:
                release_due_payments(connection)
                create_deadline_reminders(connection)
                expire_negotiations(connection)
                expire_chain_proposals(connection)
                ensure_chain_invitations(connection)
                advance_disputes(connection)
        except sqlite3.Error:
            logger.exception("Background maintenance failed")
        time.sleep(30)


def create_deadline_reminders(connection: sqlite3.Connection) -> None:
    current = datetime.now(timezone.utc)
    soon = (current + timedelta(days=1)).isoformat(timespec="seconds")
    rows = connection.execute(
        """SELECT * FROM requests
           WHERE status='negotiating' AND negotiation_deadline IS NOT NULL
             AND negotiation_deadline <= ?""",
        (soon,),
    ).fetchall()
    for item in rows:
        for user_id in (item["requester_id"], item["provider_id"]):
            if not user_id:
                continue
            body = f"Negotiation deadline is approaching for \"{item['title']}\"."
            exists = connection.execute(
                "SELECT 1 FROM notifications WHERE user_id=? AND body=?",
                (user_id, body),
            ).fetchone()
            if not exists:
                notify(connection, user_id, body, f"/chat/{item['id']}", kind="action")
    due = connection.execute(
        """SELECT * FROM requests
           WHERE status='completed_pending_release' AND release_at IS NOT NULL
             AND release_at <= ? AND release_at > ?""",
        (soon, now()),
    ).fetchall()
    for item in due:
        body = f"Payment for \"{item['title']}\" will release after the dispute window."
        exists = connection.execute(
            "SELECT 1 FROM notifications WHERE user_id=? AND body=?",
            (item["requester_id"], body),
        ).fetchone()
        if not exists:
            notify(connection, item["requester_id"], body, f"/requests/{item['id']}", kind="info")
            notify(connection, item["provider_id"], body, f"/requests/{item['id']}", kind="info")


def load_board(connection: sqlite3.Connection):
    expire_negotiations(connection)
    requests = connection.execute(
        """SELECT r.*, u.name requester_name, p.name provider_name
           FROM requests r JOIN users u ON u.id=r.requester_id
           LEFT JOIN users p ON p.id=r.provider_id
           WHERE COALESCE(r.visibility, 'public') = 'public'
           ORDER BY r.created_at DESC"""
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


COMMUNITY_WATER_BASE = 18
COMMUNITY_WATER_PER_CREDIT = 3
COMMUNITY_REWARD = 5


def my_communities(connection: sqlite3.Connection, user_id: int) -> list:
    return connection.execute(
        """SELECT c.*,
                  (SELECT COUNT(*) FROM community_members m WHERE m.community_id=c.id) member_count
           FROM communities c JOIN community_members m ON m.community_id=c.id
           WHERE m.user_id=?
           ORDER BY (c.kind='location') DESC, c.name""",
        (user_id,),
    ).fetchall()


def is_community_member(connection: sqlite3.Connection, community_id: int, user_id: int) -> bool:
    return bool(connection.execute(
        "SELECT 1 FROM community_members WHERE community_id=? AND user_id=?",
        (community_id, user_id),
    ).fetchone())


def ensure_location_community(connection: sqlite3.Connection, user) -> int | None:
    district = (user["district"] or "").strip()
    if not district:
        return None
    row = connection.execute(
        "SELECT id FROM communities WHERE kind='location' AND district=?",
        (district,),
    ).fetchone()
    if row:
        community_id = row["id"]
    else:
        cursor = connection.execute(
            """INSERT INTO communities(name,description,kind,district,color,created_at)
               VALUES (?,?,?,?,?,?)""",
            (f"{district} Neighbours",
             f"Everyone who lives in {district} — a neighbourhood jar that fills itself.",
             "location", district, "#cfe7e8", now()),
        )
        community_id = cursor.lastrowid
    connection.execute(
        "INSERT OR IGNORE INTO community_members(community_id,user_id,joined_at) VALUES (?,?,?)",
        (community_id, user["id"], now()),
    )
    return community_id


def add_community_stone(connection: sqlite3.Connection, item, contributor_id: int) -> dict | None:
    """Record a finished exchange as one stone in the community jar."""
    community_id = item["community_id"]
    if not community_id:
        return None
    agreed = item["agreed_value"] or item["requester_value"] or 1
    connection.execute(
        """INSERT INTO community_contributions(community_id,request_id,user_id,credits,created_at)
           VALUES (?,?,?,?,?)""",
        (community_id, item["id"], contributor_id, agreed, now()),
    )
    comm = connection.execute("SELECT * FROM communities WHERE id=?",
                              (community_id,)).fetchone()
    rise = max(1, round(agreed * COMMUNITY_WATER_PER_CREDIT))
    level = min(100, comm["water_level"] + rise)
    jars = comm["jars_filled"]
    reward = False
    if level >= 100:
        reward = True
        jars += 1
        level = COMMUNITY_WATER_BASE
    connection.execute(
        """UPDATE communities SET water_level=?, jars_filled=?,
           exchanges=exchanges+1, credits_total=credits_total+?, filled=? WHERE id=?""",
        (level, jars, agreed, 1 if reward else comm["filled"], community_id),
    )
    if reward:
        members = connection.execute(
            "SELECT user_id FROM community_members WHERE community_id=?",
            (community_id,),
        ).fetchall()
        for member in members:
            connection.execute("UPDATE users SET balance=balance+? WHERE id=?",
                               (COMMUNITY_REWARD, member["user_id"]))
            ledger_entry(connection, member["user_id"], item["id"], "community_reward",
                         COMMUNITY_REWARD,
                         f"Community jar filled — everyone in \"{comm['name']}\" shared +{COMMUNITY_REWARD} credits")
            notify(connection, member["user_id"],
                   f"Your community \"{comm['name']}\" filled the jar! Everyone received +{COMMUNITY_REWARD} credits.",
                   "/community", kind="success")
    return {"level": level, "credits": agreed, "rise": rise, "reward": reward}


def release_due_payments(connection: sqlite3.Connection) -> None:
    due = connection.execute(
        """SELECT * FROM requests
           WHERE status='completed_pending_release' AND release_at IS NOT NULL
             AND release_at <= ?
             AND NOT EXISTS (
                 SELECT 1 FROM disputes d
                 WHERE d.request_id=requests.id AND d.status='open'
             )""",
        (now(),),
    ).fetchall()
    for item in due:
        if item["circle_id"]:
            connection.execute(
                "UPDATE requests SET status='completed', settlement_value=0 WHERE id=?",
                (item["id"],),
            )
            close_circle_if_complete(connection, item["circle_id"])
            continue
        settle_exchange(
            connection,
            item,
            0,
            f"Payment released for {item['title']} after the dispute window",
        )
        connection.execute(
            "UPDATE requests SET status='completed', settlement_value=NULL WHERE id=?",
            (item["id"],),
        )
        add_message(
            connection,
            item["id"],
            None,
            "system",
            f"The dispute window closed and {item['agreed_value']} credits were released.",
        )
        notify(
            connection,
            item["provider_id"],
            f"Payment released for \"{item['title']}\".",
            f"/requests/{item['id']}",
            kind="success",
        )


def record_consent(connection: sqlite3.Connection, user_id: int, policy: str,
                   version: str | None = None) -> None:
    connection.execute(
        "INSERT INTO consents(user_id,policy,version,accepted_at) VALUES (?,?,?,?)",
        (user_id, policy, version or POLICY_VERSION, now()))


def record_completion(connection: sqlite3.Connection, provider_id: int,
                      request_id: int, title: str) -> None:
    """Reliability 6.2: completed work earns +1, and every fifth earns a bonus."""
    apply_reliability_event(connection, provider_id, "task_completed",
                            RELIABILITY_EVENTS["task_completed"],
                            f"Completed \"{title}\"", request_id)
    done = connection.execute(
        "SELECT COUNT(*) c FROM transactions WHERE provider_id=?", (provider_id,)).fetchone()["c"]
    awarded = connection.execute(
        """SELECT COUNT(*) c FROM reliability_events
           WHERE user_id=? AND event_type='five_task_streak'""", (provider_id,)).fetchone()["c"]
    if done // 5 > awarded:
        apply_reliability_event(connection, provider_id, "five_task_streak",
                                RELIABILITY_EVENTS["five_task_streak"],
                                "Five completed exchanges in a row", None)


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
            and item["status"] not in ("completed", "resolved", "cancelled")
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
        user_skills = [x for x in (user["skills"] or "").split(",") if x]
        matched_genres = {g for g, subs in SKILL_TREE.items() if any(x in subs for x in user_skills)}
        open_others = [i for i in requests if i["requester_id"] != user["id"]
                       and i["provider_id"] is None and i["status"] == "open"]
        user_district = (user["district"] or "").strip().lower()
        open_others.sort(key=lambda i: (
            i["category"] not in matched_genres,
            not (i["location"] and user_district and i["location"].strip().lower() == user_district),
            -i["id"]))
        recommended_tasks = open_others[:6]
        matched_ids = {i["id"] for i in open_others if i["category"] in matched_genres}
        counts = {
            "my_open": sum(1 for i in requests if i["requester_id"] == user["id"] and i["status"] not in ("completed", "resolved", "cancelled")),
            "helping": sum(1 for i in requests if i["provider_id"] == user["id"] and i["status"] not in ("completed", "resolved", "cancelled")),
        }
        history = load_history(connection, user["id"])
        # Only unresolved disputes belong in the moderator queue.
        disputes = [d for d in load_disputes(connection) if d["status"] == "open"]
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
        # Spec 10: a Circle banner when compatible needs can form a loop nearby.
        circle_invite = None
        if user["circle_enabled"]:
            circle_invite = connection.execute(
                """SELECT cp.*, COUNT(cm.user_id) member_count,
                          COALESCE(SUM(cm.response='accepted'),0) accepted_count
                   FROM chain_proposals cp JOIN chain_members cm ON cm.proposal_id=cp.id
                   WHERE cp.status='pending'
                     AND EXISTS (SELECT 1 FROM chain_members x WHERE x.proposal_id=cp.id
                                 AND x.user_id=? AND x.response='pending')
                   GROUP BY cp.id ORDER BY cp.created_at DESC LIMIT 1""",
                (user["id"],)).fetchone()
        tier = user_tier(user)
        score = user_reliability(user)
        communities = my_communities(connection, user["id"])
        standing = {
            "score": score,
            "label": reliability_label(score),
            "tier": tier,
            "restricted_cap": RESTRICTED_TASK_VALUE_CAP,
        }
        circles_done = connection.execute(
            """SELECT COUNT(DISTINCT cp.id) c FROM chain_proposals cp
               JOIN chain_members cm ON cm.proposal_id=cp.id
               WHERE cm.user_id=? AND cp.status='completed'""", (user["id"],)).fetchone()["c"]
    return render(
        request,
        "dashboard.html",
        page="home",
        active_items=active_items,
        circle_invite=circle_invite,
        standing=standing,
        circles_done=circles_done,
        exchanges=exchanges,
        action_notifs=action_notifs,
        action_count=action_count,
        pinned_ids=pinned_ids,
        recommended_tasks=recommended_tasks,
        matched_ids=matched_ids,
        counts=counts,
        history=history,
        disputes=disputes,
        ledger=ledger,
        notifications=notifications,
        recommendations=recommendations,
        stats=stats,
        task_categories=TASK_CATEGORIES,
        genre_emoji=GENRE_EMOJI,
        communities=communities,
    )


@app.get("/history", response_class=HTMLResponse)
def credit_history(request: Request, view: str = "all"):
    """Spec 43: credits page with summary cards and filterable history."""
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    kinds = {
        "all": "", "earned": "AND l.amount > 0 AND l.kind IN ('task_payment','welcome_credit')",
        "spent": "AND l.amount < 0 AND l.kind NOT IN ('refund','cancellation_refund')",
        "held": "AND l.kind='escrow_hold'",
        "refunded": "AND l.kind IN ('refund','cancellation_refund')",
        "bonus": "AND l.kind IN ('kudos_bonus','community_test')",
    }
    clause = kinds.get(view, "")
    with db() as connection:
        sql = (
            """SELECT l.*, r.title FROM ledger l LEFT JOIN requests r ON r.id=l.request_id
               WHERE l.user_id=? %s ORDER BY l.created_at DESC""" % clause
        )
        ledger = connection.execute(sql, (user["id"],)).fetchall()
        summary = {
            "available": user["balance"],
            "held": user["held_balance"],
            "starting": 10,
            "earned": connection.execute(
                """SELECT COALESCE(SUM(l.amount),0) s FROM ledger l
                   WHERE l.user_id=? AND l.amount>0 AND l.kind IN ('task_payment','welcome_credit')""",
                (user["id"],)).fetchone()["s"],
            "spent": abs(connection.execute(
                """SELECT COALESCE(SUM(l.amount),0) s FROM ledger l
                   WHERE l.user_id=? AND l.amount<0
                     AND l.kind NOT IN ('refund','cancellation_refund')""",
                (user["id"],)).fetchone()["s"]),
            "bonus": connection.execute(
                """SELECT COALESCE(SUM(l.amount),0) s FROM ledger l
                   WHERE l.user_id=? AND l.kind IN ('kudos_bonus','community_test')""",
                (user["id"],)).fetchone()["s"],
            "circle": connection.execute(
                """SELECT COUNT(DISTINCT cp.id) c FROM chain_proposals cp
                   JOIN chain_members cm ON cm.proposal_id=cp.id
                   WHERE cm.user_id=? AND cp.status='completed'""", (user["id"],)).fetchone()["c"],
        }
    return render(request, "history.html", page="credits", ledger=ledger,
                  summary=summary, view=view, views=list(kinds))


@app.get("/chain-invitations", response_class=HTMLResponse)
def chain_invitations(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        expire_chain_proposals(connection)
        ensure_chain_invitations(connection)
        proposals = connection.execute(
            """SELECT cp.*, cm.response, COUNT(allm.user_id) member_count,
                      SUM(allm.response='accepted') accepted_count
               FROM chain_proposals cp JOIN chain_members cm ON cm.proposal_id=cp.id
               JOIN chain_members allm ON allm.proposal_id=cp.id
               WHERE cm.user_id=? GROUP BY cp.id ORDER BY cp.created_at DESC""",
            (user["id"],)).fetchall()
    return render(request, "chain_invitations.html", page="circle", proposals=proposals)


@app.get("/chains/suggestions", response_class=HTMLResponse)
def chain_suggestions(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        ensure_chain_invitations(connection)
        matches = [m for m in detect_circular_matches(connection)
                   if user["id"] in m["users"]]
    return render(request, "chain_suggestions.html", page="circle", matches=matches)


@app.post("/chains/propose")
def propose_chain(request: Request, match_index: int = Form(...)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        matches = [m for m in detect_circular_matches(connection) if user["id"] in m["users"]]
        if match_index < 0 or match_index >= len(matches):
            return RedirectResponse("/chains/suggestions", status_code=303)
        proposal_id = create_chain_proposal(connection, matches[match_index])
    return redirect_toast(f"/chains/{proposal_id}", "Chain invitations sent")


@app.get("/chains/{proposal_id}", response_class=HTMLResponse)
def chain_review(request: Request, proposal_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        rows = chain_for_user(connection, proposal_id, user["id"])
        if not rows:
            return render(request, "error.html", message="That chain invitation does not exist.")
        messages = connection.execute(
            """SELECT cm.*, u.name sender_name FROM chain_messages cm
               LEFT JOIN users u ON u.id=cm.sender_id
               WHERE proposal_id=? ORDER BY cm.id""", (proposal_id,)).fetchall()
        tasks = connection.execute(
            """SELECT ct.*, r.title, r.requester_value, r.status request_status,
                      u.name requester_name, h.name helper_name
               FROM chain_tasks ct JOIN requests r ON r.id=ct.request_id
               JOIN users u ON u.id=ct.requester_id
               JOIN users h ON h.id=ct.helper_id
               WHERE ct.proposal_id=? ORDER BY ct.request_id""",
            (proposal_id,),
        ).fetchall()
        all_members = connection.execute(
            """SELECT cm.user_id, cm.request_id, cm.response, cm.position, r.title,
                      r.description, r.category, r.requester_value, u.name requester_name
               FROM chain_members cm JOIN requests r ON r.id=cm.request_id
               JOIN users u ON u.id=cm.user_id
               WHERE cm.proposal_id=? ORDER BY cm.position""", (proposal_id,)).fetchall()
        my_interests = {
            row["request_id"] for row in connection.execute(
                "SELECT request_id FROM chain_interest WHERE proposal_id=? AND user_id=?",
                (proposal_id, user["id"]))
        }
        progress = circle_progress(connection, proposal_id)
    return render(request, "chain_review.html", page="circle", proposal=rows[0], members=rows,
                  tasks=tasks, all_members=all_members, my_interests=my_interests,
                  my_own_request=next((m["request_id"] for m in rows
                                       if m["user_id"] == user["id"]), None),
                  progress=progress,
                  messages=messages, my_response=next(
                      row["response"] for row in rows if row["user_id"] == user["id"]))


@app.post("/chains/{proposal_id}/interest")
def chain_interest(request: Request, proposal_id: int, tasks: list[int] = Form([])):
    """Record which of the other tasks this resident is willing to help with."""
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        rows = chain_for_user(connection, proposal_id, user["id"])
        if not rows or rows[0]["status"] not in ("pending", "active"):
            return RedirectResponse(f"/chains/{proposal_id}", status_code=303)
        own = next((row["request_id"] for row in rows if row["user_id"] == user["id"]), None)
        valid = {row["request_id"] for row in rows}
        connection.execute(
            "DELETE FROM chain_interest WHERE proposal_id=? AND user_id=?",
            (proposal_id, user["id"]))
        for task_id in tasks[:10]:
            if task_id in valid and task_id != own:
                connection.execute(
                    """INSERT OR IGNORE INTO chain_interest(proposal_id,user_id,request_id,created_at)
                       VALUES (?,?,?,?)""", (proposal_id, user["id"], task_id, now()))
        if rows[0]["status"] == "active":
            refresh_circle_assignment(connection, proposal_id)
    return redirect_toast(f"/chains/{proposal_id}", "Saved the tasks you can help with")


def refresh_circle_assignment(connection: sqlite3.Connection, proposal_id: int) -> None:
    """Re-run interest-based matching and wire the tasks up to the Circle."""
    assignment = assign_circle_tasks(connection, proposal_id)
    for row in assignment:
        connection.execute(
            """UPDATE requests
               SET provider_id=?, status='negotiating', circle_id=?,
                   negotiation_deadline=?
               WHERE id=? AND provider_id IS NULL""",
            (row["helper_id"], proposal_id,
             (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec="seconds"),
             row["request_id"]),
        )
        notify(connection, row["helper_id"],
               "A Circle is forming — open the task you chose to help with.",
               f"/chat/{row['request_id']}", kind="action")


@app.post("/chains/{proposal_id}/respond")
def chain_respond(request: Request, proposal_id: int, response: str = Form(...)):
    user = current_user(request)
    if not user or response not in {"accepted", "declined"}:
        return RedirectResponse("/login" if not user else f"/chains/{proposal_id}", status_code=303)
    with db() as connection:
        expire_chain_proposals(connection)
        rows = chain_for_user(connection, proposal_id, user["id"])
        if not rows or rows[0]["status"] != "pending":
            return RedirectResponse(f"/chains/{proposal_id}", status_code=303)
        connection.execute(
            """UPDATE chain_members SET response=?, responded_at=?
               WHERE proposal_id=? AND user_id=?""",
            (response, now(), proposal_id, user["id"]),
        )
        accepted_rows = connection.execute(
            """SELECT user_id, request_id FROM chain_members
               WHERE proposal_id=? AND response='accepted' ORDER BY position""",
            (proposal_id,),
        ).fetchall()
        if len(accepted_rows) >= 3:
            connection.execute(
                "UPDATE chain_proposals SET status='active', activated_at=? WHERE id=?",
                (now(), proposal_id))
            assignment = assign_circle_tasks(connection, proposal_id)
            for row in assignment:
                connection.execute(
                    """UPDATE requests
                       SET provider_id=?, provider_value=requester_value,
                           provider_buffer=requester_buffer, status='negotiating',
                           circle_id=?, negotiation_deadline=?
                       WHERE id=? AND status='open'""",
                    (row["helper_id"], proposal_id,
                     (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec="seconds"),
                     row["request_id"]),
                )
                notify(
                    connection,
                    row["helper_id"],
                    "The Circle activated. You have been matched to a task you said you could help with.",
                    f"/chat/{row['request_id']}",
                    kind="action",
                )
            unassigned = connection.execute(
                """SELECT m.request_id, m.user_id FROM chain_members m
                   WHERE m.proposal_id=? AND m.response='accepted'
                     AND NOT EXISTS (SELECT 1 FROM chain_tasks t
                                     WHERE t.proposal_id=m.proposal_id
                                       AND t.request_id=m.request_id)""",
                (proposal_id,)).fetchall()
            for member in unassigned:
                notify(connection, member["user_id"],
                       "The Circle activated, but nobody has been assigned your task yet. "
                       "Check who you can help with to complete the loop.",
                       f"/chains/{proposal_id}", kind="action")
            body = (
                f"A Circle is active with {len(accepted_rows)} participants. Everyone gives help and "
                "receives help; no credits move between neighbours."
            )
        elif response == "declined":
            body = (
                "A participant declined, but the invitation remains open while IoU looks "
                "for the minimum of three willing participants."
            )
        else:
            body = "A participant joined the task chain invitation."
        members = connection.execute(
            "SELECT user_id FROM chain_members WHERE proposal_id=? AND user_id!=?",
            (proposal_id, user["id"])).fetchall()
        for member in members:
            notify(connection, member["user_id"], body, f"/chains/{proposal_id}",
                   kind="action" if response == "accepted" else "info")
    return RedirectResponse(f"/chains/{proposal_id}", status_code=303)


@app.post("/chains/{proposal_id}/leave")
def chain_leave(request: Request, proposal_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        rows = chain_for_user(connection, proposal_id, user["id"])
        if rows and rows[0]["status"] == "pending":
            connection.execute(
                "UPDATE chain_members SET response='left', responded_at=? WHERE proposal_id=? AND user_id=?",
                (now(), proposal_id, user["id"]))
            remaining = connection.execute(
                "SELECT COUNT(*) c FROM chain_members WHERE proposal_id=? AND response!='left'",
                (proposal_id,),
            ).fetchone()["c"]
            if remaining < 3:
                connection.execute(
                    "UPDATE chain_proposals SET status='cancelled', cancelled_at=? WHERE id=?",
                    (now(), proposal_id))
            for member in connection.execute(
                "SELECT user_id FROM chain_members WHERE proposal_id=? AND user_id!=?",
                (proposal_id, user["id"])):
                notify(connection, member["user_id"], "A neighbour left this Circle invitation.",
                       f"/chains/{proposal_id}")
        elif rows and rows[0]["status"] == "active":
            handle_circle_withdrawal(connection, proposal_id, user["id"])
    return RedirectResponse("/chain-invitations", status_code=303)


def handle_circle_withdrawal(connection: sqlite3.Connection, proposal_id: int, user_id: int) -> None:
    """Spec 39: someone walks away from a live Circle.

    Completed work stays honoured; unmet needs go back on the board; the person
    who received help but stops giving loses standing.
    """
    progress = circle_progress(connection, proposal_id)
    connection.execute(
        """UPDATE chain_members SET response='withdrawn', responded_at=?
           WHERE proposal_id=? AND user_id=?""",
        (now(), proposal_id, user_id))
    connection.execute(
        "DELETE FROM chain_tasks WHERE proposal_id=? AND helper_id=?", (proposal_id, user_id))
    pending = connection.execute(
        """SELECT ct.request_id, r.title FROM chain_tasks ct JOIN requests r ON r.id=ct.request_id
           WHERE ct.proposal_id=? AND r.status NOT IN ('completed','resolved')""",
        (proposal_id,)).fetchall()
    for row in pending:
        connection.execute(
            """UPDATE requests SET provider_id=NULL, provider_value=NULL, provider_buffer=NULL,
                       status='open', circle_id=NULL, negotiation_deadline=NULL WHERE id=?""",
            (row["request_id"],))
        notify(connection,
               connection.execute("SELECT requester_id FROM requests WHERE id=?",
                                  (row["request_id"],)).fetchone()["requester_id"],
               f"Your Circle partner withdrew, so \"{row['title']}\" is back on the board to repost.",
               f"/requests/{row['request_id']}", kind="warning")
    connection.execute(
        "UPDATE chain_proposals SET status='dissolved', cancelled_at=? WHERE id=?",
        (now(), proposal_id))
    if progress["done"]:
        apply_reliability_event(
            connection, user_id, "broken_circle_commitment",
            RELIABILITY_EVENTS["broken_circle_commitment"],
            "Withdrew from an active Circle after neighbours had already been helped", None)
    for member in connection.execute(
            "SELECT user_id FROM chain_members WHERE proposal_id=? AND user_id!=?",
            (proposal_id, user_id)):
        notify(connection, member["user_id"],
               "A Circle neighbour withdrew. Tasks still open are back on the board and "
               "completed work stays honoured.", f"/chains/{proposal_id}", kind="warning")


@app.post("/chains/{proposal_id}/messages")
def chain_message(request: Request, proposal_id: int, body: str = Form(...)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        rows = chain_for_user(connection, proposal_id, user["id"])
        if rows and body.strip():
            connection.execute(
                "INSERT INTO chain_messages(proposal_id,sender_id,body,created_at) VALUES (?,?,?,?)",
                (proposal_id, user["id"], body.strip(), now()))
            for member in rows:
                if member["user_id"] != user["id"]:
                    notify(connection, member["user_id"], "New message in your circular task chain.",
                            f"/chains/{proposal_id}", kind="info")
    return RedirectResponse(f"/chains/{proposal_id}", status_code=303)


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
               WHERE (r.requester_id=? OR r.provider_id=?
                      OR EXISTS (SELECT 1 FROM request_participants rp
                                 WHERE rp.request_id=r.id AND rp.user_id=?))
                 AND EXISTS (SELECT 1 FROM messages WHERE request_id=r.id)
               ORDER BY last_at DESC""",
            (user["id"], user["id"], user["id"])).fetchall()
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
        my_active = [item for item in mine if item["status"] not in ("completed", "resolved", "open", "cancelled")]
        my_cancelled = [item for item in mine if item["status"] == "cancelled"]
        my_completed = [item for item in mine if item["status"] in ("completed", "resolved")]
        history = load_history(connection, user["id"])
        disputes = load_disputes(connection)
    return render(
        request,
        "my_posts.html",
        page="posts",
        my_open=my_open,
        my_active=my_active,
        my_cancelled=my_cancelled,
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
        helping_active = [item for item in joined if item["status"] not in ("completed", "resolved", "cancelled")]
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
            and item["status"] == "open"
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


@app.get("/recommended", response_class=HTMLResponse)
def recommended_page(request: Request):
    return RedirectResponse("/browse", status_code=303)


@app.get("/community", response_class=HTMLResponse)
def community_page(request: Request, cid: int | None = Query(None),
                   stone: int | None = Query(None), created: str = "",
                   joined: str = "", join_error: str = ""):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not request.query_params.get("preview"):
        return RedirectResponse("/", status_code=303)
    with db() as connection:
        ensure_location_community(connection, user)
        communities = my_communities(connection, user["id"])
        current = None
        if communities:
            current = next((c for c in communities if c["id"] == cid), communities[0])
        stone_info = None
        if current and stone:
            req = connection.execute(
                "SELECT * FROM requests WHERE id=?", (stone,)).fetchone()
            if req and req["community_id"] == current["id"]:
                stone_info = {
                    "title": req["title"],
                    "credits": req["agreed_value"] or req["requester_value"] or 1,
                }
        need_help, helping, activity = [], [], []
        if current:
            need_help = connection.execute(
                """SELECT r.*, u.name requester_name FROM requests r
                   JOIN users u ON u.id=r.requester_id
                   WHERE r.community_id=? AND r.status='open' AND r.requester_id != ?
                   ORDER BY r.created_at DESC""",
                (current["id"], user["id"]),
            ).fetchall()
            helping = connection.execute(
                """SELECT r.*, u.name requester_name FROM requests r
                   JOIN users u ON u.id=r.requester_id
                   WHERE r.community_id=? AND r.provider_id=?
                     AND r.status NOT IN ('completed','resolved','cancelled')
                   ORDER BY r.created_at DESC""",
                (current["id"], user["id"]),
            ).fetchall()
            activity = connection.execute(
                """SELECT cc.credits, cc.created_at, r.title, r.category, u.name contributor_name
                   FROM community_contributions cc
                   JOIN requests r ON r.id=cc.request_id
                   LEFT JOIN users u ON u.id=cc.user_id
                   WHERE cc.community_id=? ORDER BY cc.created_at DESC, cc.id DESC LIMIT 8""",
                (current["id"],),
            ).fetchall()
    return render(request, "community.html", page="community",
                  communities=communities, current=current, stone_info=stone_info,
                  need_help=need_help, helping=helping, activity=activity,
                  created=created or None, joined=joined or None,
                  join_error=join_error or None, genre_emoji=GENRE_EMOJI)


@app.post("/community/create")
def community_create(request: Request, name: str = Form(...),
                     description: str = Form(""), color: str = Form("#cfe7e8")):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    name = name.strip()
    if not (2 <= len(name) <= 40):
        return redirect_toast("/community", "Community name needs 2–40 characters")
    with db() as connection:
        code = None
        for _ in range(20):
            candidate = f"{secrets.randbelow(900000) + 100000}"
            if not connection.execute("SELECT 1 FROM communities WHERE code=?",
                                      (candidate,)).fetchone():
                code = candidate
                break
        cursor = connection.execute(
            """INSERT INTO communities(name,description,kind,color,code,created_by,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (name, description.strip(), "custom",
             color if color.startswith("#") else "#cfe7e8", code, user["id"], now()),
        )
        community_id = cursor.lastrowid
        connection.execute(
            "INSERT OR IGNORE INTO community_members(community_id,user_id,role,joined_at) VALUES (?,?,?,?)",
            (community_id, user["id"], "founder", now()),
        )
    return RedirectResponse(f"/community?cid={community_id}&created={code}", status_code=303)


@app.post("/community/join")
def community_join(request: Request, code: str = Form(...)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    code = code.strip()
    with db() as connection:
        comm = connection.execute("SELECT * FROM communities WHERE code=?",
                                  (code,)).fetchone()
        if not comm:
            return RedirectResponse("/community?join_error=1", status_code=303)
        already = is_community_member(connection, comm["id"], user["id"])
        connection.execute(
            "INSERT OR IGNORE INTO community_members(community_id,user_id,joined_at) VALUES (?,?,?)",
            (comm["id"], user["id"], now()),
        )
        if already:
            return RedirectResponse(f"/community?cid={comm['id']}", status_code=303)
    return RedirectResponse(f"/community?cid={comm['id']}&joined=1", status_code=303)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(BASE_DIR / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/uploads/{filename}")
def uploaded_file(request: Request, filename: str):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    safe_name = Path(filename).name
    if safe_name != filename or not (UPLOAD_DIR / safe_name).is_file():
        return render(request, "error.html", message="Evidence file not found.")
    return FileResponse(UPLOAD_DIR / safe_name)


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
    confirm_password: str = Form(""),
    username: str = Form(""),
    address: str = Form(""),
    district: str = Form(""),
    birthday: str = Form(""),
    language: str = Form("en"),
    skills: list[str] = Form([]),
    consent_tos: str | None = Form(None),
    consent_privacy: str | None = Form(None),
    consent_guidelines: str | None = Form(None),
    consent_competence: str | None = Form(None),
    consent_analytics: str | None = Form(None),
    agree_tos: str | None = Form(None),
    agree_privacy: str | None = Form(None),
    agree_guidelines: str | None = Form(None),
    agree_competence: str | None = Form(None),
    analytics_consent: str | None = Form(None),
):
    email = email.strip().lower()
    username = username.strip()
    # The consent screen has shipped under two field-name spellings; accept either
    # so the flow keeps working whichever version of register.html is live.
    tos_ok = agree_tos or consent_tos
    privacy_ok = agree_privacy or consent_privacy
    guidelines_ok = agree_guidelines or consent_guidelines
    competence_ok = agree_competence or consent_competence
    opted_in = 1 if (analytics_consent or consent_analytics) else 0
    district = district.strip()
    if district and district not in HK_DISTRICTS:
        district = ""
    all_skills = {skill for skills_list in SKILL_TREE.values() for skill in skills_list}
    skills_clean = [s for s in skills if s in all_skills]
    missing = [
        label
        for value, label in (
            (tos_ok, "the Terms of Service"),
            (privacy_ok, "the Privacy Notice"),
            (guidelines_ok, "the Community Guidelines"),
            (competence_ok, "the note about provider competence"),
        )
        if not value
    ]
    if missing:
        return render(
            request, "register.html",
            error="Please accept " + ", ".join(missing) + " to continue.",
            skill_tree=SKILL_TREE, genre_emoji=GENRE_EMOJI, hk_regions=HK_REGIONS,
        )
    if password != confirm_password:
        return render(request, "register.html", error="The two passwords do not match.",
                      skill_tree=SKILL_TREE, genre_emoji=GENRE_EMOJI, hk_regions=HK_REGIONS)
    age = None
    if birthday:
        try:
            born = datetime.fromisoformat(birthday)
            today = datetime.now(timezone.utc)
            age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        except ValueError:
            age = None
    with db() as connection:
        try:
            cursor = connection.execute(
                """INSERT INTO users(name,email,password,balance,is_moderator,language,
                   address,district,hkid,address_id,age,birthday,skills,created_at,
                   reliability,account_status,analytics_consent,consent_analytics,username,
                   verification_identity,verification_neighbourhood)
                   VALUES (?,?,?,10,0,?,?,?,'',?,?,?,?,?,?, 'active',?,?,?,'simulated','verified')""",
                (name.strip(), email, hash_password(password), language,
                 address.strip(), district, district, age, birthday or None,
                 ",".join(skills_clean), now(), RELIABILITY_START, opted_in, opted_in,
                 username or None),
            )
            user_id = cursor.lastrowid
            ledger_entry(connection, user_id, None, "welcome_credit", 10,
                         "Welcome credits for joining the community")
        except sqlite3.IntegrityError:
            # Never sign the visitor into an existing account: that would let
            # anyone take over a registered email.
            return render(
                request,
                "register.html",
                error="That email is already registered. Please log in instead.",
                skill_tree=SKILL_TREE, genre_emoji=GENRE_EMOJI, hk_regions=HK_REGIONS,
            )
        record_consent(connection, user_id, "tos", tos_ok)
        record_consent(connection, user_id, "privacy", privacy_ok)
        record_consent(connection, user_id, "guidelines", guidelines_ok)
        record_consent(connection, user_id, "competence", competence_ok)
        if opted_in:
            record_consent(connection, user_id, "analytics", analytics_consent or consent_analytics)
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
    with db() as connection:
        blocked = connection.execute(
            """SELECT b.blocked_id, u.name FROM blocks b JOIN users u ON u.id=b.blocked_id
               WHERE b.blocker_id=?""", (user["id"],)).fetchall()
        consents = connection.execute(
            "SELECT policy, version, accepted_at FROM consents WHERE user_id=? ORDER BY id DESC",
            (user["id"],)).fetchall()
    return render(request, "account.html", page="account", genre_emoji=GENRE_EMOJI,
                  skill_tree=SKILL_TREE, hk_districts=HK_DISTRICTS, saved=saved == "1",
                  blocked=blocked, consents=consents)


@app.post("/account")
def account_save(
    request: Request,
    name: str = Form(...),
    username: str = Form(""),
    bio: str = Form(""),
    address: str = Form(""),
    district: str = Form(""),
    age: int | None = Form(None),
    birthday: str = Form(""),
    language: str = Form("en"),
    consent_analytics: str | None = Form(None),
    analytics_consent: str | None = Form(None),
    circle_enabled: str | None = Form(None),
    vis_photo: str | None = Form(None),
    vis_neighbourhood: str | None = Form(None),
    vis_skills: str | None = Form(None),
    vis_bio: str | None = Form(None),
    vis_completed: str | None = Form(None),
    vis_circle: str | None = Form(None),
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    district = district.strip()
    if district and district not in HK_DISTRICTS:
        district = ""
    opted_in = 1 if (analytics_consent or consent_analytics) else 0
    with db() as connection:
        connection.execute(
            """UPDATE users SET name=?, username=?, bio=?, address=?, district=?, address_id=?,
                   age=?, birthday=?, language=?, analytics_consent=?, consent_analytics=?,
                   circle_enabled=?,
                   vis_photo=?, vis_neighbourhood=?, vis_skills=?, vis_bio=?, vis_completed=?,
                   vis_circle=? WHERE id=?""",
            (name.strip(), username.strip() or None, bio.strip() or None, address.strip(),
             district, district, age, birthday.strip() or None, language, opted_in, opted_in,
             1 if circle_enabled else 0,
             1 if vis_photo else 0, 1 if vis_neighbourhood else 0, 1 if vis_skills else 0,
             1 if vis_bio else 0, 1 if vis_completed else 0, 1 if vis_circle else 0,
             user["id"]),
        )
        if opted_in:
            record_consent(connection, user["id"], "analytics")
    return RedirectResponse("/account?saved=1", status_code=303)


@app.get("/profile/{user_id}", response_class=HTMLResponse)
@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, user_id: int | None = None):
    """Spec 41: a public profile whose contents respect the owner's privacy choices."""
    viewer = current_user(request)
    if not viewer:
        return RedirectResponse("/login", status_code=303)
    target_id = user_id or viewer["id"]
    with db() as connection:
        profile = connection.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
        if not profile:
            return render(request, "error.html", message="That profile does not exist.")
        thumbs = connection.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(kudos_bonus),0) bonus FROM transactions "
            "WHERE provider_id=? AND kudos_given=1", (target_id,)).fetchone()
        completed = connection.execute(
            "SELECT COUNT(*) c FROM transactions WHERE provider_id=?", (target_id,)).fetchone()["c"]
        circles = connection.execute(
            """SELECT COUNT(DISTINCT cp.id) c FROM chain_proposals cp
               JOIN chain_members cm ON cm.proposal_id=cp.id
               WHERE cm.user_id=? AND cp.status='completed'""", (target_id,)).fetchone()["c"]
        events = connection.execute(
            """SELECT * FROM reliability_events WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT 8""",
            (target_id,)).fetchall()
        tasks_done = connection.execute(
            """SELECT t.*, r.title FROM transactions t JOIN requests r ON r.id=t.request_id
               WHERE t.provider_id=? ORDER BY t.created_at DESC LIMIT 6""",
            (target_id,)).fetchall()
        annual_opt_in = connection.execute(
            "SELECT 1 FROM consents WHERE user_id=? AND policy='analytics' LIMIT 1",
            (target_id,)).fetchone()
    owns_profile = target_id == viewer["id"]
    return render(
        request, "profile.html", page="profile", profile=profile, owns_profile=owns_profile,
        thumbs=thumbs, completed=completed, circles=circles, events=events,
        tasks_done=tasks_done, analytics_opt_in=bool(annual_opt_in),
        circle_badge=profile["verification_neighbourhood"] == "verified",
    )


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
    with db() as connection:
        communities = my_communities(connection, user["id"])
    return render(request, "new_request.html", page="posts",
                  task_categories=TASK_CATEGORIES, genre_emoji=GENRE_EMOJI,
                  hk_districts=HK_DISTRICTS, communities=communities)


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
                      (SELECT u2.name FROM messages m JOIN users u2 ON u2.id=m.sender_id
                        WHERE m.request_id=r.id AND m.sender_id IS NOT NULL
                        ORDER BY m.created_at DESC, m.id DESC LIMIT 1) last_sender_name,
                      (SELECT COUNT(*) FROM messages m WHERE m.request_id=r.id) msg_count,
                      (SELECT COUNT(*) FROM messages m
                        LEFT JOIN thread_reads tr ON tr.request_id=m.request_id AND tr.user_id=?
                        WHERE m.request_id=r.id AND m.sender_id IS NOT NULL AND m.sender_id != ?
                          AND m.id > COALESCE(tr.last_read_id, 0)) unread_count
               FROM requests r JOIN users u ON u.id=r.requester_id
               LEFT JOIN users p ON p.id=r.provider_id
               WHERE (r.requester_id=? OR r.provider_id=?
                      OR EXISTS (SELECT 1 FROM request_participants rp
                                 WHERE rp.request_id=r.id AND rp.user_id=?))
                 AND EXISTS (SELECT 1 FROM messages m WHERE m.request_id=r.id)
               ORDER BY last_at DESC""",
            (user["id"], user["id"], user["id"], user["id"], user["id"]),
        ).fetchall()
        exchanges = []
        for t in threads:
            item = load_request(connection, t["id"])
            needs, cta, label, href = exchange_flag(connection, item, user["id"])
            exchanges.append({"t": t, "needs": needs, "cta": cta, "label": label, "href": href})
        exchanges.sort(key=lambda e: not e["needs"])
    return render(request, "messages.html", page="messages", exchanges=exchanges)


@app.get("/chat/{request_id}", response_class=HTMLResponse)
def chat_page(request: Request, request_id: int, with_id: int | None = Query(None)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        expire_negotiations(connection)
        advance_disputes(connection)
        item = load_request(connection, request_id)
        is_participant = connection.execute(
            "SELECT 1 FROM request_participants WHERE request_id=? AND user_id=?",
            (request_id, user["id"]),
        ).fetchone()
        if item and item["status"] == "open" and item["requester_id"] != user["id"] and not is_participant:
            if item["visibility"] == "community" and not is_community_member(
                    connection, item["community_id"], user["id"]):
                return render(request, "error.html",
                              message="This task is only open to members of its community.")
            if is_blocked(connection, user["id"], item["requester_id"]):
                return render(request, "error.html",
                              message="You can't join this task — you have blocked this resident.")
            connection.execute(
                "INSERT OR IGNORE INTO request_participants(request_id,user_id,created_at) VALUES (?,?,?)",
                (request_id, user["id"], now()),
            )
            is_participant = True
        if not item or (
            user["id"] not in (item["requester_id"], item["provider_id"] or 0)
            and not is_participant
        ):
            return render(request, "error.html", message="You are not part of this exchange.")
        messages = connection.execute(
            """SELECT m.*, u.name sender_name FROM messages m
               LEFT JOIN users u ON u.id=m.sender_id
               WHERE m.request_id=? AND (? IS NULL OR m.kind='system'
                     OR m.sender_id IN (?, ?))
               ORDER BY m.created_at, m.id""",
            (request_id, with_id, user["id"], with_id or 0),
        ).fetchall()
        participants = connection.execute(
            """SELECT u.id, u.name,
                      (SELECT body FROM messages m WHERE m.request_id=? AND m.sender_id=u.id
                        ORDER BY m.created_at DESC, m.id DESC LIMIT 1) last_body,
                      (SELECT attachment FROM messages m WHERE m.request_id=? AND m.sender_id=u.id
                        ORDER BY m.created_at DESC, m.id DESC LIMIT 1) last_attachment,
                      (SELECT created_at FROM messages m WHERE m.request_id=? AND m.sender_id=u.id
                        ORDER BY m.created_at DESC, m.id DESC LIMIT 1) last_at
               FROM messages m2 JOIN users u ON u.id=m2.sender_id
               WHERE m2.request_id=? AND m2.sender_id IS NOT NULL AND m2.sender_id != ?
               GROUP BY u.id ORDER BY last_at DESC""",
            (request_id, request_id, request_id, request_id, user["id"]),
        ).fetchall()
        other_uid = item["requester_id"] if item["requester_id"] != user["id"] else item["provider_id"]
        if other_uid and all(p["id"] != other_uid for p in participants):
            other_row = connection.execute(
                "SELECT id, name FROM users WHERE id=?", (other_uid,)).fetchone()
            if other_row:
                participants.append({"id": other_row["id"], "name": other_row["name"],
                                     "last_body": None, "last_at": None})
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
    return render(request, "chat.html", page="messages", item=item, messages=messages,
                  pending_offer=pending_offer, pending_meetup=pending_meetup,
                  pending_settlement=pending_settlement, dispute=dispute, rec=rec,
                  my_confirm=my_confirm, hero=hero, genre_emoji=GENRE_EMOJI,
                  participants=participants, with_id=with_id,
                  connected="connected" in request.query_params,
                  step_index=STATUS_STEP.get(item["status"], 0),
                  timeline_steps=TIMELINE_STEPS,
                  status_label=STATUS_LABELS.get(item["status"], item["status"]))


@app.get("/chat/{request_id}/feed", response_class=HTMLResponse)
def chat_feed(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return HTMLResponse("", status_code=401)
    with db() as connection:
        item = load_request(connection, request_id)
        participant = connection.execute(
            "SELECT 1 FROM request_participants WHERE request_id=? AND user_id=?",
            (request_id, user["id"]),
        ).fetchone()
        if not item or (
            user["id"] not in (item["requester_id"], item["provider_id"] or 0)
            and not participant
        ):
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
async def chat_post(request: Request, request_id: int, body: str = Form(""),
                    attachment: UploadFile | None = File(None)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    stored = None
    with db() as connection:
        item = connection.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        participant = connection.execute(
            "SELECT 1 FROM request_participants WHERE request_id=? AND user_id=?",
            (request_id, user["id"]),
        ).fetchone()
        allowed = item and (
            user["id"] in (item["requester_id"], item["provider_id"] or 0)
            or (item["status"] == "open" and user["id"] != item["requester_id"] and participant)
        )
        if not allowed:
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        if attachment is not None and attachment.filename:
            data = await attachment.read()
            try:
                stored, _flags = screen_evidence(
                    attachment.filename, attachment.content_type, data, connection)
            except ValueError as error:
                return render(request, "error.html", message=str(error))
            (UPLOAD_DIR / stored).write_bytes(data)
        term = find_banned_term(body)
        if term:
            return render(request, "error.html", message=(
                f"Your message wasn't sent — IoU blocks messages containing \"{term}\"."
            ))
        if body.strip() or stored:
            add_message(connection, request_id, user["id"], "chat",
                        body.strip(), attachment=stored)
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
    duration_minutes: int = Form(0),
    confirm_outside_range: str | None = Form(None),
    confirm_accurate: str | None = Form(None),
    community_id: int | None = Form(None),
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user_tier(user) == "suspended":
        return render(request, "error.html", message=(
            "Your account is suspended, so you cannot post tasks right now. You can still review "
            "your history, follow open disputes, export your data, or submit an appeal."
        ))
    if confirm_accurate != "1":
        return render(request, "error.html", message=(
            "Please confirm that your description accurately represents the requested task."
        ))
    term = find_banned_term(title, description)
    if term:
        return render(request, "error.html", message=(
            f"This task can't be posted: the word or phrase \"{term}\" is on IoU's prohibited list. "
            "If you think this is a mistake, rephrase the task and try again."
        ))
    if user["balance"] <= 0:
        return render(
            request,
            "error.html",
            message=(
                f"Your available balance is {user['balance']} credits. "
                "Your balance needs rebalancing — help a neighbour to earn credits back first, "
                "or join an available Circle."
            ),
        )
    # Spec 7.4: the projected balance after escrow must not fall below the floor.
    if user["balance"] - max(1, offered_value) < MIN_BALANCE:
        return render(
            request,
            "error.html",
            message=(
                f"This request would exceed your {MIN_BALANCE} credit limit: "
                f"{user['balance']} available − {max(1, offered_value)} credits "
                f"would leave {user['balance'] - max(1, offered_value)}."
            ),
        )
    category = category.strip().lower()
    if category not in TASK_CATEGORIES:
        return render(request, "error.html", message="Please choose a valid task category.")
    offered_value, offered_buffer = max(1, offered_value), max(0, offered_buffer)
    if user_tier(user) == "restricted" and offered_value > RESTRICTED_TASK_VALUE_CAP:
        return render(request, "error.html", message=(
            f"Your reliability is currently {user_reliability(user)} (Restricted), so the largest "
            f"task you can post is {RESTRICTED_TASK_VALUE_CAP} credits. Complete tasks successfully "
            "to restore full privileges."
        ))
    effort_minutes, complexity = infer_task_attributes(title, description, category)
    if duration_minutes and 5 <= duration_minutes <= 1440:
        effort_minutes = duration_minutes
    with db() as connection:
        community_id_value = None
        if community_id:
            if not is_community_member(connection, community_id, user["id"]):
                return render(request, "error.html",
                              message="You can only post to communities you belong to.")
            community_id_value = community_id
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
                requester_value,requester_buffer,effort_minutes,complexity,created_at,duration_minutes,
                community_id,visibility
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                title.strip(), description.strip(), category.strip().lower(),
                location.strip(), needed_by.strip() or None, urgency.strip() or None,
                preferred_time.strip() or None,
                user["id"], offered_value, offered_buffer, effort_minutes,
                complexity, now(), duration_minutes or 0,
                community_id_value, "community" if community_id_value else "public",
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
                         f"Community appreciation bonus for {item['request_id']}")
            apply_reliability_event(
                connection, item["provider_id"], "thumbs_up", RELIABILITY_EVENTS["thumbs_up"],
                "Received a thumbs-up for a completed task", request_id)
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
    """Release escrow: refund goes back to the requester, the rest to the provider.

    Circle tasks move no credits at all — that is the whole point of a Circle.
    """
    if item["circle_id"]:
        connection.execute(
            "UPDATE requests SET status='completed', settlement_value=0 WHERE id=?",
            (item["id"],),
        )
        record_completion(connection, item["provider_id"], item["id"], item["title"])
        close_circle_if_complete(connection, item["circle_id"])
        return
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
    record_completion(connection, item["provider_id"], item["id"], item["title"])


@app.get("/requests/{request_id}", response_class=HTMLResponse)
def request_detail(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        expire_negotiations(connection)
        advance_disputes(connection)
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
        requester_profile = connection.execute(
            "SELECT id,name,reliability,district FROM users WHERE id=?",
            (item["requester_id"],)).fetchone()
        requester_thumbs = connection.execute(
            "SELECT COUNT(*) c FROM transactions WHERE provider_id=? AND kudos_given=1",
            (item["requester_id"],)).fetchone()["c"]
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
        requester_profile=requester_profile,
        requester_thumbs=requester_thumbs,
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
                "action": {"text": "Propose Meetup", "modal": "meetup-modal"}}
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
    if status == "completed_pending_release":
        if involved:
            return {"actor": "none", "label": "Completion confirmed. Credits remain held during the three-day dispute window.", "action": None}
        return waiting
    if status == "disputed":
        if involved:
            return {"actor": "you", "label": "Dispute in progress. Credits are frozen — settle together or wait for IoU's Fair Resolution.",
                    "action": {"text": "Resolve in Chat", "href": f"/chat/{rid}"}}
        return waiting
    if status == "mediation":
        if involved:
            return {"actor": "you", "label": "IoU proposed a Fair Resolution — both participants have one day to accept it.",
                    "action": {"text": "Review Fair Resolution", "href": f"/requests/{rid}/mediation"}}
        return waiting
    if status == "human_review":
        return {"actor": "other", "label": "A community moderator is reviewing this dispute.", "action": None}
    if status in ("completed", "resolved"):
        window = item["dispute_window_days"] or 3
        return {"actor": "none", "label": f"This exchange is complete. You can still give Kudos or raise a dispute within {window} day(s).", "action": None}
    if status == "cancelled":
        return {"actor": "none", "label": "This request was cancelled by the requester.", "action": None}
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
               offer_buffer: int = Form(0),
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
        if allowed and item["visibility"] == "community" and not is_community_member(
                connection, item["community_id"], user["id"]):
            return render(request, "error.html",
                          message="This task is only open to members of its community.")
        if not allowed:
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        value = item["requester_value"] if mode == "accept" else max(1, amount or 1)
        offer_buffer = max(0, offer_buffer)
        if mode != "accept":
            low, high = negotiation_bounds(item, is_owner)
            if (value < low or value > high) and confirm_outside_range != "1":
                return render(
                    request,
                    "error.html",
                    message=f"That offer is outside the current negotiation range of {low}–{high} credits. "
                            "Go back and use the offer window — tick the confirmation to send it anyway.",
                )
        old = pending_message(connection, request_id, "offer")
        if old:
            connection.execute("UPDATE messages SET status='countered' WHERE id=?", (old["id"],))
        if item["status"] == "open":
            connection.execute(
                """UPDATE requests SET provider_id=?, status='negotiating',
                   negotiation_deadline=? WHERE id=?""",
                (user["id"], (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec="seconds"), request_id),
            )
            connection.execute(
                "INSERT OR IGNORE INTO request_participants(request_id,user_id,created_at) VALUES (?,?,?)",
                (request_id, user["id"], now()),
            )
        role = "Requester" if is_owner else "Helper"
        if is_owner:
            connection.execute("UPDATE requests SET requester_value=? WHERE id=?", (value, request_id))
        else:
            connection.execute(
                "UPDATE requests SET provider_value=?, provider_buffer=? WHERE id=?",
                (value, offer_buffer, request_id))
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
        if (user["id"] == item["provider_id"] and user_tier(user) != "normal"
                and amount > RESTRICTED_TASK_VALUE_CAP):
            return render(request, "error.html", message=(
                f"Your reliability is {user_reliability(user)}, so you cannot accept a task worth "
                f"more than {RESTRICTED_TASK_VALUE_CAP} credits. Finish successful tasks to restore "
                "full privileges."
            ))
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
            in_circle = bool(item["circle_id"])
            if not in_circle:
                requester = connection.execute("SELECT balance FROM users WHERE id=?",
                                               (item["requester_id"],)).fetchone()
                if requester["balance"] < 0:
                    return render(request, "error.html", message=(
                        f"The requester's balance is negative ({requester['balance']} credits). "
                        "They need to earn credits back above 0 before starting a new exchange."
                    ))
                if requester["balance"] - agreed < MIN_BALANCE:
                    return render(request, "error.html", message=(
                        f"The requester doesn't have enough available credits for this exchange "
                        f"({requester['balance']} available, {agreed} needed, floor is {MIN_BALANCE}). Please renegotiate a lower value in the chat."
                    ))
                connection.execute(
                    "UPDATE users SET balance=balance-?, held_balance=held_balance+? WHERE id=?",
                    (agreed, agreed, item["requester_id"]),
                )
                ledger_entry(connection, item["requester_id"], request_id, "escrow_hold", -agreed,
                             f"Escrow hold for {item['title']}")
            add_message(connection, request_id, None, "system",
                        "Exchange confirmed by both participants.")
            add_message(
                connection, request_id, None, "system",
                "This task is part of a Circle — no credits are held and none will be exchanged."
                if in_circle else
                f"Credits are now in escrow ({agreed} credits held).",
            )
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
                       "Circle agreement confirmed — no credits are exchanged." if in_circle
                       else "Exchange confirmed — credits are in escrow. The meetup time is already fixed.",
                       f"/requests/{request_id}", kind="success")
            else:
                connection.execute("UPDATE requests SET status='confirmed' WHERE id=?", (request_id,))
                notify(connection, other_party(item, user["id"]),
                       "Circle agreement confirmed — no credits move." if in_circle
                       else "Exchange confirmed — credits are in escrow. Time to schedule the task.",
                       f"/requests/{request_id}", kind="success")
    just_connected = bool(item["confirm_requester"] and item["confirm_provider"])
    if just_connected:
        return redirect_toast(f"/chat/{request_id}?connected=1",
                              "Circle agreement confirmed — no credits exchanged"
                              if item["circle_id"] else "Exchange confirmed — credits are in escrow")
    return redirect_toast(f"/chat/{request_id}",
                          "Waiting for the other neighbour to confirm too.")


@app.post("/requests/{request_id}/meetup")
def propose_meetup(request: Request, request_id: int, date: str = Form(...),
                   time: str = Form(""), location: str = Form("")):
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
        if location.strip().lower() == "online":
            body = f"Online session proposal: {date}" + (f" at {time}" if time else "")
            meta = json.dumps({"date": date, "time": time or "Any time", "location": "Online"})
        else:
            body = f"Meeting proposal: {date} at {time}, {location}"
            meta = json.dumps({"date": date, "time": time, "location": location})
        add_message(connection, request_id, user["id"], "meetup", body, meta=meta, status="pending")
        notify(connection, other_party(item, user["id"]),
               f"Meeting proposal for \"{item['title']}\": {date}.", f"/chat/{request_id}", kind="action")
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
    paths, review_flags = [], []
    with db() as connection:
        # Validate the exchange first: screening records the file digest, so a
        # rejected submit would otherwise burn the file and orphan it on disk.
        item = load_request(connection, request_id)
        if not item or item["provider_id"] != user["id"] or item["status"] != "in_progress":
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        for upload in evidence[:3]:
            if not upload or not upload.filename:
                continue
            try:
                data = await upload.read()
                filename, flags = screen_evidence(
                    upload.filename, upload.content_type, data, connection
                )
            except ValueError as error:
                return render(request, "error.html", message=str(error))
            (UPLOAD_DIR / filename).write_bytes(data)
            paths.append(filename)
            review_flags.extend(f"{upload.filename}: {flag}" for flag in flags)
        proof_optional = item["category"] in NO_EVIDENCE_CATEGORIES
        if not paths and not proof_optional:
            return render(
                request,
                "error.html",
                message="Please attach at least one completion evidence file (PNG, JPG, PDF, or TXT, up to 5MB).",
            )
        connection.execute(
            """UPDATE requests
               SET status='completion_submitted', completion_note=?, completion_evidence=?,
                   proof_status=?, evidence_required=?
               WHERE id=?""",
            (note.strip(), json.dumps(paths),
             "requires_review" if review_flags else "accepted",
             0 if proof_optional and not paths else 1,
             request_id),
        )
        add_message(connection, request_id, None, "system",
                    "Task completion has been submitted for review.")
        if review_flags:
            add_message(
                connection, request_id, None, "system",
                "Evidence requires review — " + "; ".join(review_flags) +
                " This is a flag for a human to look at, not a conclusion.",
            )
        elif paths:
            add_message(connection, request_id, None, "system",
                        "Evidence passed the automated integrity checks.")
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
        if (not item or item["requester_id"] != user["id"]
                or item["status"] != "completion_submitted"
                or not item["dispute_eligible"]):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
    return render(request, "review_completion.html", item=item)


@app.post("/requests/{request_id}/review/confirm")
def review_confirm(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if (not item or item["requester_id"] != user["id"]
                or item["status"] != "completion_submitted"
                or not item["dispute_eligible"]):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        release_at = (
            datetime.now(timezone.utc) + timedelta(days=3)
        ).isoformat(timespec="seconds")
        connection.execute(
            """UPDATE requests
               SET status='completed_pending_release',
                   requester_confirmed_at=?, completed_at=?, release_at=?,
                   settlement_value=?
               WHERE id=?""",
            (now(), now(), release_at, item["agreed_value"], request_id),
        )
        add_message(connection, request_id, None, "system",
                    f"Task completion confirmed. {item['agreed_value']} credits remain held for three days in case of dispute.")
        notify(connection, item["provider_id"],
               f"Completion confirmed — {item['agreed_value']} credits will be released after the three-day dispute window.",
               f"/requests/{request_id}", kind="success")
        stone = None
        if item["community_id"]:
            stone = add_community_stone(connection, item, user["id"])
    if stone:
        return redirect_toast(
            f"/community?cid={item['community_id']}&stone={request_id}",
            "Another little thing done.")
    return redirect_toast(f"/chat/{request_id}", "✓ Completion confirmed — credits held for three days")


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
                         compensation: str = Form(""),
                         evidence: list[UploadFile] = File(default=[])):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["requester_id"] != user["id"] or item["status"] != "completion_submitted":
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        paths, review_flags = [], []
        for upload in evidence[:5]:
            if upload.filename:
                data = await upload.read()
                try:
                    path, flags = screen_evidence(upload.filename, upload.content_type, data, connection)
                except ValueError as error:
                    return render(request, "error.html", message=str(error))
                (UPLOAD_DIR / path).write_bytes(data)
                paths.append(path)
                review_flags.extend(f"{upload.filename}: {flag}" for flag in flags)
        refund = max(0, min(refund_amount, item["agreed_value"]))
        if desired_outcome == "full":
            refund = item["agreed_value"]
        negotiation_deadline = (
            datetime.now(timezone.utc) + DISPUTE_NEGOTIATION_WINDOW
        ).isoformat(timespec="seconds")
        connection.execute(
            """INSERT OR REPLACE INTO disputes(
               request_id,opened_by,description,evidence_path,evidence_paths,recommendation,
               requested_refund_amount,reason,desired_outcome,negotiation_status,negotiation_deadline)
               VALUES (?,?,?,?,?,?,?,?,?,'open',?)""",
            (request_id, user["id"], description.strip(),
             paths[0] if paths else None, json.dumps(paths),
             "Evidence requires review — " + "; ".join(review_flags) if review_flags else
             "Evidence passed the automated integrity checks.",
             refund, reason, desired_outcome, negotiation_deadline),
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
                       reason: str = Form(""), resolution: str = Form("refund"),
                       compensation: str = Form("")):
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
        resolution = resolution if resolution in {"refund", "redo", "other"} else "refund"
        label = (f"Proposed refund: {amount} credits" if resolution == "refund"
                 else "Proposed redo/fix" if resolution == "redo"
                 else f"Proposed alternative compensation: {compensation.strip() or 'to be agreed'}")
        add_message(connection, request_id, user["id"], "settlement",
                    label + (f" — {reason}" if reason else ""),
                    meta=json.dumps({"amount": amount, "reason": reason,
                                     "resolution": resolution,
                                     "compensation": compensation.strip()}),
                    status="pending")
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
            meta = json.loads(msg["meta"] or "{}")
            amount = meta.get("amount", 0)
            connection.execute("UPDATE messages SET status='accepted' WHERE id=?", (message_id,))
            if meta.get("resolution") == "redo":
                connection.execute(
                    """UPDATE requests SET status='in_progress', completion_note=NULL,
                       completion_evidence=NULL, completed_at=NULL, release_at=NULL,
                       settlement_value=NULL WHERE id=?""",
                    (item["id"],),
                )
                connection.execute(
                    "UPDATE disputes SET status='resolved', negotiation_status='redo_agreed', resolved_at=? WHERE request_id=?",
                    (now(), item["id"]),
                )
            else:
                settle_exchange(connection, item, amount, f"Settlement for request #{item['id']}")
                connection.execute("UPDATE requests SET status='resolved', completed_at=? WHERE id=?",
                                   (now(), item["id"]))
                connection.execute(
                    "UPDATE disputes SET status='resolved', negotiation_status='resolved', resolved_at=? WHERE request_id=?",
                    (now(), item["id"]))
            add_message(connection, item["id"], None, "system",
                        "Both participants agreed that the task should be redone or fixed."
                        if meta.get("resolution") == "redo"
                        else f"Both participants agreed to alternative compensation: {meta.get('compensation')}"
                        if meta.get("resolution") == "other"
                        else f"Both participants agreed on a settlement ({amount} credits refunded).")
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
    midpoint, spread = comparable_stats(connection, item["category"])
    message_count = connection.execute(
        "SELECT COUNT(*) FROM messages WHERE request_id=? AND kind IN ('chat','text')",
        (item["id"],),
    ).fetchone()[0]
    prior_disputes = connection.execute(
        """SELECT COUNT(*) FROM disputes d
           JOIN requests r ON r.id=d.request_id
           WHERE d.status IN ('resolved','closed') AND r.category=?""",
        (item["category"],),
    ).fetchone()[0]
    evidence_paths = dispute["evidence_paths"] if "evidence_paths" in dispute.keys() else None
    evidence_count = len(json.loads(evidence_paths or "[]")) if evidence_paths else (
        1 if dispute["evidence_path"] else 0
    )
    if requested >= agreed:
        suggested = max(1, round(agreed * 0.6))
    else:
        suggested = max(0, round((requested + max(0, agreed - midpoint)) / 2))
    suggested = max(0, min(suggested, agreed))
    reason = (
        f"The agreed value was {agreed} credits, and the requester asked for a {requested} credit refund. "
        f"Recent comparable {item['category']} tasks settled around {midpoint} credits (band {midpoint - spread}–{midpoint + spread}). "
        f"The task description, complaint, {message_count} chat messages, and {evidence_count} evidence file(s) were considered. "
        f"The platform has {prior_disputes} resolved disputes in this category. "
        f"Balancing these signals against the value of work done suggests a {suggested} credit refund."
    )
    return {"amount": suggested, "reason": reason}


@app.get("/requests/{request_id}/mediation", response_class=HTMLResponse)
def mediation_page(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        advance_disputes(connection)
        item = load_request(connection, request_id)
        if not item or item["status"] not in ("disputed", "mediation"):
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
                  suggestion=suggestion, messages=messages, my_accept=my_accept,
                  can_act=item["status"] == "mediation")


@app.post("/requests/{request_id}/mediation/accept")
def mediation_accept(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["status"] != "mediation":
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        if user["id"] not in (item["requester_id"], item["provider_id"] or 0):
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
                        f"Both participants accepted the Fair Resolution ({suggestion['amount']} credits refunded).")
            notify(connection, other_party(item, user["id"]),
                   "Your dispute has been resolved via the Fair Resolution.", f"/requests/{request_id}", kind="success")
        else:
            notify(connection, other_party(item, user["id"]),
                   "A Fair Resolution is waiting for your response.", f"/requests/{request_id}/mediation", kind="action")
    return RedirectResponse(f"/requests/{request_id}/mediation", status_code=303)


@app.post("/requests/{request_id}/mediation/reject")
def mediation_reject(request: Request, request_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if item and item["status"] == "mediation" and user["id"] in (item["requester_id"], item["provider_id"] or 0):
            connection.execute("UPDATE requests SET status='human_review' WHERE id=?", (request_id,))
            connection.execute("UPDATE disputes SET negotiation_status='human_review', review_since=? WHERE request_id=?",
                               (now(), request_id))
            add_message(connection, request_id, None, "system",
                        "The Fair Resolution was rejected. A community moderator will make the final decision.")
            dispute = connection.execute(
                "SELECT id FROM disputes WHERE request_id=?", (request_id,)).fetchone()
            if dispute:
                notify_moderators(connection,
                                  f"A dispute needs your final decision.",
                                  f"/disputes/{dispute['id']}/review")
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
        audits = connection.execute(
            "SELECT * FROM evidence_audits WHERE stored_path IN (?, ?, ?, ?, ?)",
            tuple((json.loads(dispute["evidence_paths"] or "[]") if dispute["evidence_paths"]
                   else [dispute["evidence_path"]]) + [None] * 5)[:5],
        ).fetchall()
        suggestion = mediation_suggestion(connection, item, dispute)
    return render(request, "dispute_review.html", dispute=dispute, item=item,
                  messages=messages, suggestion=suggestion, audits=audits)


@app.get("/moderator/requests/{request_id}/policy", response_class=HTMLResponse)
def dispute_policy_page(request: Request, request_id: int):
    user = current_user(request)
    if not user or not user["is_moderator"]:
        return RedirectResponse("/", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
    if not item:
        return render(request, "error.html", message="Request not found.")
    return render(request, "dispute_policy.html", item=item)


@app.post("/moderator/requests/{request_id}/policy")
def update_dispute_policy(request: Request, request_id: int,
                          eligible: str = Form("1"),
                          window_days: int = Form(3)):
    user = current_user(request)
    if not user or not user["is_moderator"]:
        return RedirectResponse("/", status_code=303)
    window_days = max(0, min(window_days, 30))
    with db() as connection:
        connection.execute(
            "UPDATE requests SET dispute_eligible=?, dispute_window_days=? WHERE id=?",
            (1 if eligible == "1" else 0, window_days, request_id),
        )
    return redirect_toast(f"/requests/{request_id}", "Dispute policy updated")


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
            if decision == "freeze":
                connection.execute(
                    "UPDATE disputes SET moderator_decision='freeze', negotiation_status='frozen', review_since=? WHERE id=?",
                    (now(), dispute_id),
                )
                connection.execute(
                    "UPDATE requests SET status='human_review' WHERE id=?",
                    (dispute["rid"],),
                )
                add_message(
                    connection,
                    dispute["rid"],
                    None,
                    "system",
                    "A moderator kept the credits frozen pending further evidence.",
                )
                notify(
                    connection,
                    dispute["requester_id"],
                    "A moderator kept your dispute frozen pending further evidence.",
                    f"/requests/{dispute['rid']}",
                    kind="action",
                )
                notify(
                    connection,
                    dispute["provider_id"],
                    "A moderator kept the dispute frozen pending further evidence.",
                    f"/requests/{dispute['rid']}",
                    kind="action",
                )
                return RedirectResponse("/", status_code=303)
            requested = dispute["requested_refund_amount"] or dispute["agreed_value"]
            refund = {"deny": 0, "partial": max(1, requested // 2),
                      "full": requested}.get(decision, 0)
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
        if not item or item["status"] not in ("completed", "completed_pending_release"):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        if user["id"] not in (item["requester_id"], item["provider_id"] or 0):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        if not item["dispute_eligible"]:
            return render(request, "error.html", message="This task is not eligible for post-completion disputes.")
        if item["status"] == "completed":
            if not item["completed_at"]:
                return RedirectResponse(f"/requests/{request_id}", status_code=303)
            completed_at = datetime.fromisoformat(item["completed_at"])
            if datetime.now(timezone.utc) > completed_at + timedelta(days=item["dispute_window_days"] or 3):
                return render(
                    request,
                    "error.html",
                    message="The three-day dispute window for this exchange has closed.",
                )
        existing = connection.execute(
            "SELECT id FROM disputes WHERE request_id=? AND status='open'", (request_id,)).fetchone()
        if not existing:
            connection.execute(
                """INSERT INTO disputes(request_id,opened_by,description,negotiation_status,review_since)
                   VALUES (?,?,'After-completion dispute (no escrow held)','human_review',?)""",
                (request_id, user["id"], now()),
            )
            connection.execute(
                "UPDATE requests SET status='human_review' WHERE id=?",
                (request_id,),
            )
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
def cancel_request(request: Request, request_id: int, reason: str = Form("")):
    """Spec 21 — cancellation is requested, not unilateral, once two sides are involved."""
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item:
            return RedirectResponse("/my-posts", status_code=303)
        involved = user["id"] in (item["requester_id"], item["provider_id"] or 0)
        if not involved or item["status"] in ("completed", "resolved", "cancelled"):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        if item["status"] in ("disputed", "mediation", "human_review"):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        other = other_party(item, user["id"])
        # Nobody has committed yet — the requester can simply withdraw the post.
        if not other and item["requester_id"] == user["id"]:
            connection.execute(
                "UPDATE requests SET status='cancelled' WHERE id=?", (request_id,))
            add_message(connection, request_id, None, "system",
                        "The requester withdrew this request before anyone committed.")
            return redirect_toast("/my-posts", "Request withdrawn")
        label = "the requester" if item["requester_id"] == user["id"] else "the helper"
        connection.execute(
            """UPDATE requests
               SET cancel_requested_by=?, cancel_reason=?, cancel_compensation=0
               WHERE id=?""",
            (user["id"], reason.strip(), request_id))
        add_message(connection, request_id, None, "system",
                    f"Cancellation requested by {label}. Waiting for the other participant to agree.")
        notify(connection, other,
               f"Cancellation requested for \"{item['title']}\". Agree to cancel or discuss it first.",
               f"/requests/{request_id}", kind="action")
    return redirect_toast(f"/requests/{request_id}", "Cancellation request sent")


@app.post("/requests/{request_id}/cancel/respond")
def cancel_respond(request: Request, request_id: int, action: str = Form(...),
                   compensation: int = Form(0)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["cancel_requested_by"] in (None, user["id"]):
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        initiator = item["cancel_requested_by"]
        if action == "discuss":
            notify(connection, initiator,
                   f"Your cancellation request for \"{item['title']}\" was not agreed yet — "
                   "the other participant wants to talk first.",
                   f"/chat/{request_id}", kind="info")
            return redirect_toast(f"/chat/{request_id}", "Open the chat to discuss the cancellation")
        if action == "compensation":
            hold = item["settlement_value"] or item["agreed_value"] or 0
            amount = max(0, min(compensation, hold))
            if not amount:
                return redirect_toast(f"/requests/{request_id}", "Enter the credits you're asking for")
            connection.execute(
                "UPDATE requests SET cancel_compensation=? WHERE id=?", (amount, request_id))
            add_message(connection, request_id, None, "system",
                        f"Compensation requested: {amount} credits to finish this cancellation.")
            notify(connection, initiator,
                   f"The other participant asked for {amount} credits to agree to cancelling "
                   f"\"{item['title']}\".", f"/requests/{request_id}", kind="action")
            return redirect_toast(f"/requests/{request_id}", f"Asked for {amount} credits")
        if action == "agree":
            amount = item["cancel_compensation"] or 0
            _finalise_cancellation(connection, item, mutual=True)
            return redirect_toast("/messages", "Cancellation agreed")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@app.post("/requests/{request_id}/cancel/compensation")
def cancel_compensation(request: Request, request_id: int, action: str = Form(...)):
    """Accept, counter or escalate a compensation claim attached to a cancellation."""
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        item = load_request(connection, request_id)
        if not item or item["cancel_requested_by"] != user["id"]:
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        amount = item["cancel_compensation"] or 0
        if action == "accept":
            _finalise_cancellation(connection, item, mutual=True, compensation=amount)
            return redirect_toast("/messages", f"Cancellation agreed with {amount} credits compensation")
        if action == "escalate":
            connection.execute(
                "UPDATE requests SET cancel_compensation=0 WHERE id=?", (request_id,))
            open_cancellation_dispute(connection, item, user["id"], amount)
            return redirect_toast(f"/requests/{request_id}/mediation",
                                  "Escalated to a fair resolution")
        connection.execute(
            "UPDATE requests SET cancel_compensation=0 WHERE id=?", (request_id,))
        notify(connection, other_party(item, user["id"]),
               f"Your compensation request for \"{item['title']}\" was declined. Discuss it in chat "
               "or escalate it to IoU.", f"/requests/{request_id}", kind="warning")
    return redirect_toast(f"/requests/{request_id}", "Compensation request declined")


def _finalise_cancellation(connection: sqlite3.Connection, item: sqlite3.Row,
                           mutual: bool, compensation: int = 0) -> None:
    """Release anything held and record the reliability consequence."""
    held = item["settlement_value"] or item["agreed_value"] or 0
    request_id = item["id"]
    requester_id, provider_id = item["requester_id"], item["provider_id"]
    if compensation and provider_id:
        connection.execute(
            "UPDATE users SET held_balance=MAX(0, held_balance-?) WHERE id=?",
            (held, requester_id))
        connection.execute(
            "UPDATE users SET balance=balance+? WHERE id=?", (compensation, provider_id))
        connection.execute(
            "UPDATE users SET balance=balance+? WHERE id=?",
            (max(0, held - compensation), requester_id))
        if compensation:
            ledger_entry(connection, provider_id, request_id, "cancellation_compensation",
                         compensation, f"Compensation for cancelling {item['title']}")
        refund = max(0, held - compensation)
        if refund:
            ledger_entry(connection, requester_id, request_id, "cancellation_refund", refund,
                         f"Credits returned for cancelled task {item['title']}")
    elif held:
        connection.execute(
            "UPDATE users SET held_balance=MAX(0, held_balance-?) WHERE id=?",
            (held, requester_id))
        connection.execute(
            "UPDATE users SET balance=balance+? WHERE id=?", (held, requester_id))
        ledger_entry(connection, requester_id, request_id, "cancellation_refund", held,
                     f"Credits returned for cancelled task {item['title']}")
    connection.execute(
        """UPDATE requests SET status='cancelled', cancel_compensation=?,
               cancel_requested_by=NULL WHERE id=?""",
        (compensation, request_id))
    add_message(connection, request_id, None, "system",
                "Both participants agreed to cancel this task. No reliability penalty applies."
                if mutual else "This request was cancelled.")
    for participant in {requester_id, provider_id} - {None}:
        notify(connection, participant,
               f"\"{item['title']}\" was cancelled by agreement.",
               f"/requests/{request_id}", kind="info")
    if not mutual and provider_id:
        apply_reliability_event(
            connection, item["cancel_requested_by"] or requester_id, "late_cancellation",
            RELIABILITY_EVENTS["late_cancellation"],
            "Cancelled after the other participant had already committed", request_id)


def open_cancellation_dispute(connection: sqlite3.Connection, item: sqlite3.Row,
                              opened_by: int, amount: int) -> None:
    """Route an unresolved cancellation compensation through the normal ladder."""
    negotiation_deadline = (
        datetime.now(timezone.utc) + DISPUTE_NEGOTIATION_WINDOW
    ).isoformat(timespec="seconds")
    connection.execute(
        """INSERT OR REPLACE INTO disputes(
               request_id,opened_by,description,reason,desired_outcome,
               requested_refund_amount,negotiation_status,negotiation_deadline,recommendation)
           VALUES (?,?,?,?,?,?,'open',?,?)""",
        (item["id"], opened_by,
         f"Cancellation compensation under discussion — {amount} credits were requested.",
         "cancellation", "partial", amount, negotiation_deadline,
         "Agreed task value; this dispute is about cancelling it fairly."),
    )
    connection.execute("UPDATE requests SET status='disputed' WHERE id=?", (item["id"],))
    add_message(connection, item["id"], None, "system",
                "The cancellation could not be agreed, so it entered IoU's resolution process.")
    notify(connection, other_party(item, opened_by),
           "A cancellation disagreement needs a joint resolution.",
           f"/requests/{item['id']}", kind="action")


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
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "Sign in to use the credit estimator."}, status_code=401)
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


# ---------------------------------------------------------------------------
# Legal, consent and data rights (spec 4, 51-56)
# ---------------------------------------------------------------------------
@app.get("/legal/{slug}", response_class=HTMLResponse)
def legal_page(request: Request, slug: str):
    doc = LEGAL_DOCS.get(slug)
    if not doc:
        return render(request, "error.html", message="That policy page does not exist.")
    return render(request, "legal.html", page="legal", slug=slug,
                  title=doc[0], sections=doc[1], policy_version=POLICY_VERSION)


@app.get("/legal", response_class=HTMLResponse)
def legal_index(request: Request):
    return render(request, "legal.html", page="legal", slug="index",
                  title="Policies & agreements",
                  sections=[(title, summary) for _, doc in LEGAL_DOCS.items()
                            for title, summary in [doc[1][0]]],
                  policy_version=POLICY_VERSION, index=[
                      (slug, doc[0]) for slug, doc in LEGAL_DOCS.items()])


@app.get("/download-my-data")
def download_my_data(request: Request):
    """Spec 55: export everything IoU holds about this resident."""
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        payload = {
            "account": {
                "name": user["name"], "username": user["username"], "email": user["email"],
                "district": user["district"], "neighbourhood": user["address"],
                "age": user["age"], "bio": user["bio"],
                "skills": [s for s in (user["skills"] or "").split(",") if s],
                "created_at": user["created_at"],
                "reliability": user["reliability"],
                "analytics_consent": bool(user["analytics_consent"]),
                "verification_identity": user["verification_identity"],
                "verification_neighbourhood": user["verification_neighbourhood"],
            },
            "credits": {"balance": user["balance"], "held": user["held_balance"]},
            "ledger": [dict(r) for r in connection.execute(
                "SELECT * FROM ledger WHERE user_id=? ORDER BY created_at", (user["id"],))],
            "tasks": [dict(r) for r in connection.execute(
                "SELECT id,title,description,category,location,status,requester_value,"
                "agreed_value,created_at FROM requests WHERE requester_id=?", (user["id"],))],
            "tasks_helped": [dict(r) for r in connection.execute(
                "SELECT id,title,status,agreed_value,created_at FROM requests WHERE provider_id=?",
                (user["id"],))],
            "reliability_events": [dict(r) for r in connection.execute(
                "SELECT * FROM reliability_events WHERE user_id=? ORDER BY created_at",
                (user["id"],))],
            "circle_participation": [dict(r) for r in connection.execute(
                """SELECT cp.id, cp.status, cp.created_at FROM chain_proposals cp
                   JOIN chain_members cm ON cm.proposal_id=cp.id WHERE cm.user_id=?""",
                (user["id"],))],
            "privacy_settings": {key: bool(user[key]) for key in (
                "vis_photo", "vis_neighbourhood", "vis_skills", "vis_bio",
                "vis_completed", "vis_circle", "analytics_consent", "circle_enabled")},
            "consents": [dict(r) for r in connection.execute(
                "SELECT policy,version,accepted_at FROM consents WHERE user_id=?", (user["id"],))],
        }
    return JSONResponse(
        payload,
        headers={"Content-Disposition": "attachment; filename=iou-my-data.json"},
    )


@app.post("/delete-account")
def delete_account(request: Request, password: str = Form(...), confirm: str = Form("")):
    """Spec 56: deactivate the account without breaking the ledger."""
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if confirm.strip().upper() != "DELETE" or not verify_password(password, user["password"]):
        return render(request, "error.html",
                      message="Type DELETE and re-enter your password to confirm deletion.")
    with db() as connection:
        connection.execute(
            "UPDATE users SET name='Deleted resident', email=?, password=?, bio=NULL, "
            "skills='', username=NULL, address=NULL, account_status='suspended', "
            "reliability=0, vis_photo=0, vis_neighbourhood=0, vis_skills=0, vis_bio=0 "
            "WHERE id=?",
            (f"deleted-{user['id']}@example.invalid", hash_password(secrets.token_urlsafe(16)),
             user["id"]),
        )
    request.session.clear()
    return RedirectResponse("/login?deleted=1", status_code=303)


# ---------------------------------------------------------------------------
# Safety: reports, blocks and appeals (spec 47, 48, 57)
# ---------------------------------------------------------------------------
@app.get("/report", response_class=HTMLResponse)
def report_page(request: Request, user_id: int = 0, request_id: int = 0):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, "report.html", reported_user_id=user_id or None,
                  reported_request_id=request_id or None,
                  categories=REPORT_CATEGORIES)


@app.post("/report")
def submit_report(request: Request, category: str = Form(...), description: str = Form(...),
                  reported_user_id: int = Form(0), reported_request_id: int = Form(0)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if category not in REPORT_CATEGORIES:
        return render(request, "error.html", message="Please choose a valid report category.")
    if not reported_user_id and not reported_request_id:
        return render(request, "error.html", message="Nothing to report.")
    with db() as connection:
        connection.execute(
            """INSERT INTO reports(reporter_id,reported_user_id,reported_request_id,
                   category,description,created_at) VALUES (?,?,?,?,?,?)""",
            (user["id"], reported_user_id or None, reported_request_id or None,
             category, description.strip(), now()))
        for row in connection.execute("SELECT id FROM users WHERE is_moderator=1"):
            notify(connection, row["id"], "A new report needs review.",
                   "/moderator/reports", kind="action")
    return redirect_toast("/browse" if not reported_request_id else f"/requests/{reported_request_id}",
                          "Report sent to the moderators")


@app.post("/users/{user_id}/block")
def block_user(request: Request, user_id: int, next: str = Form("")):
    user = current_user(request)
    if not user or user_id == user["id"]:
        return RedirectResponse("/login" if not user else next or "/", status_code=303)
    with db() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO blocks(blocker_id,blocked_id,created_at) VALUES (?,?,?)",
            (user["id"], user_id, now()))
    return redirect_back(next, "/account")


@app.post("/users/{user_id}/unblock")
def unblock_user(request: Request, user_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        connection.execute(
            "DELETE FROM blocks WHERE blocker_id=? AND blocked_id=?", (user["id"], user_id))
    return RedirectResponse("/account", status_code=303)


@app.post("/appeal")
def submit_appeal(request: Request, description: str = Form(...)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db() as connection:
        connection.execute(
            """INSERT INTO reports(kind,reporter_id,reported_user_id,category,description,created_at)
               VALUES ('appeal',?,?,'reliability_appeal',?,?)""",
            (user["id"], user["id"], description.strip(), now()))
        for row in connection.execute("SELECT id FROM users WHERE is_moderator=1"):
            notify(connection, row["id"], "A resident appealed an account restriction.",
                   "/moderator/reports", kind="action")
    return redirect_toast("/account", "Appeal submitted — a moderator will review it")


# ---------------------------------------------------------------------------
# Moderator workspace (spec 57)
# ---------------------------------------------------------------------------
@app.get("/moderator/reports", response_class=HTMLResponse)
def moderator_reports(request: Request):
    user = current_user(request)
    if not user or not user["is_moderator"]:
        return RedirectResponse("/" if user else "/login", status_code=303)
    with db() as connection:
        reports = connection.execute(
            """SELECT rp.*, ru.name reporter_name, du.name reported_name, rq.title request_title
               FROM reports rp LEFT JOIN users ru ON ru.id=rp.reporter_id
               LEFT JOIN users du ON du.id=rp.reported_user_id
               LEFT JOIN requests rq ON rq.id=rp.reported_request_id
               ORDER BY (rp.status='open') DESC, rp.id DESC""").fetchall()
        appeals = [row for row in reports if row["kind"] == "appeal"]
    return render(request, "moderator_reports.html", page="moderator", reports=reports,
                  appeals=appeals, categories=REPORT_CATEGORIES)


@app.post("/moderator/reports/{report_id}/resolve")
def resolve_report(request: Request, report_id: int, resolution: str = Form(...),
                   note: str = Form("")):
    user = current_user(request)
    if not user or not user["is_moderator"]:
        return RedirectResponse("/" if user else "/login", status_code=303)
    with db() as connection:
        report = connection.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if not report or report["status"] != "open":
            return RedirectResponse("/moderator/reports", status_code=303)
        target = report["reported_user_id"]
        detail = note.strip() or f"Moderator action: {resolution}"
        if resolution == "dismiss":
            pass
        elif resolution == "warning":
            apply_reliability_event(connection, target, "guideline_violation",
                                    RELIABILITY_EVENTS["guideline_violation"], detail, None)
        elif resolution == "fraud_evidence":
            apply_reliability_event(connection, target, "fraudulent_evidence",
                                    RELIABILITY_EVENTS["fraudulent_evidence"], detail, None)
        elif resolution == "restrict":
            connection.execute("UPDATE users SET reliability=MIN(reliability, 25) WHERE id=?", (target,))
            apply_reliability_event(connection, target, "serious_failure",
                                    RELIABILITY_EVENTS["serious_failure"], detail, None)
        elif resolution == "suspend":
            connection.execute(
                "UPDATE users SET account_status='suspended' WHERE id=?", (target,))
            apply_reliability_event(connection, target, "serious_failure",
                                    RELIABILITY_EVENTS["serious_failure"], detail, None)
        elif resolution == "restore":
            connection.execute(
                "UPDATE users SET account_status='active', reliability=MAX(reliability, 60) "
                "WHERE id=?", (target,))
            apply_reliability_event(connection, target, "appeal_upheld", 20,
                                    "Appeal upheld — standing restored", None)
        connection.execute(
            """UPDATE reports SET status='resolved', resolution=?, moderator_id=?, resolved_at=?
               WHERE id=?""",
            (resolution, user["id"], now(), report_id))
        if report["kind"] == "appeal":
            notify(connection, report["reporter_id"],
                   f"Your appeal was reviewed — outcome: {resolution.replace('_', ' ')}.",
                   "/account", kind="success")
    return redirect_toast("/moderator/reports", "Report resolved")

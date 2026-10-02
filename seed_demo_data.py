"""Populate realistic completed exchanges for the local recommendation demo."""

from datetime import datetime, timedelta, timezone

from app import db, init_db


SAMPLES = [
    ("daily life", "Grocery pickup", 30, 3, 3, 4),
    ("daily life", "Prescription pickup", 20, 2, 4, 3),
    ("daily life", "Grocery pickup", 45, 3, 5, 5),
    ("daily life", "Parcel collection", 25, 2, 4, 4),
    ("family & kids", "School pickup", 60, 4, 4, 8),
    ("family & kids", "After-school supervision", 90, 4, 5, 11),
    ("family & kids", "Playtime supervision", 45, 3, 3, 6),
    ("technology", "Set up printer", 40, 3, 4, 5),
    ("technology", "Phone troubleshooting", 60, 4, 4, 8),
    ("technology", "Laptop setup", 120, 5, 5, 16),
    ("repair & diy", "Water plants", 20, 1, 5, 3),
    ("repair & diy", "Assemble shelf", 75, 4, 4, 10),
]


def main() -> None:
    init_db()
    with db() as connection:
        requester = connection.execute("SELECT id FROM users WHERE email='alex@example.com'").fetchone()
        provider = connection.execute("SELECT id FROM users WHERE email='sam@example.com'").fetchone()
        if not requester or not provider:
            raise RuntimeError("Demo users are missing; run the app once first.")
        existing = connection.execute(
            "SELECT COUNT(*) FROM requests WHERE title LIKE '[Demo history]%'"
        ).fetchone()[0]
        if existing:
            print(f"Demo history already exists ({existing} transactions).")
            return
        for index, (category, title, minutes, complexity, quality, value) in enumerate(SAMPLES):
            created = (datetime.now(timezone.utc) - timedelta(days=7 + index * 6)).isoformat(timespec="seconds")
            cursor = connection.execute(
                """INSERT INTO requests(
                    title, description, category, requester_id, provider_id,
                    status, requester_value, requester_buffer, provider_value,
                    provider_buffer, agreed_value, effort_minutes, complexity,
                    quality_score, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"[Demo history] {title}",
                    f"Example completed {category} exchange used to calibrate recommendations.",
                    category, requester["id"], provider["id"], "completed",
                    value, 1, value, 1, value, minutes, complexity, quality, created,
                ),
            )
            connection.execute(
                """INSERT INTO transactions(
                    request_id, requester_id, provider_id, value, effort_minutes,
                    complexity, quality_score, created_at
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    cursor.lastrowid, requester["id"], provider["id"], value,
                    minutes, complexity, quality, created,
                ),
            )
        print(f"Inserted {len(SAMPLES)} demo transactions.")


if __name__ == "__main__":
    main()

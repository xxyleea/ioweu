"""Rebuild ioweu.db as a realistic, hand-crafted dataset for the HacKU demo.

Every value is derived from the app's own credit math (welcome grant of 10,
floor of effort_minutes/15, complexity/quality factors), so the deterministic
recommendation checker agrees with every price on the board.

Creates four neighbouring Circle states for the booth walkthrough:
  - Kennedy Town  : one member joined, one rejoined without a task, rest pending
  - Sai Ying Pun  : fresh invites, nothing accepted yet (home banner state)
  - Sheung Wan    : fully confirmed loop, Circle ACTIVE
  - North Point   : active loop with an OPEN withdrawal recovery case

Run:  ./.venv/Scripts/python.exe seed_showcase.py
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app as A

DB = Path("ioweu.db")


# ---------------------------------------------------------------- rate card
def price(minutes: int, complexity: int = 3, quality: int = 3) -> int:
    """Same math as recommendation(): floor minutes/15, then factor up."""
    base = max(2, round(minutes / 15))
    cf = 1 + 0.1 * (complexity - 3)
    qf = 1 + 0.05 * (quality - 3)
    return max(2, round(base * cf * qf))


USERS = [
    # name, email, district, address, age, skills, reliability, balance, moderator
    ("Vivian Lau", "vivian.lau.hk@gmail.com", "Kennedy Town", "12 Smithfield Road", 41, "Mediation,Community Organising", 88, 46, 1),
    ("Marcus Cheng", "marcus.cheng88@gmail.com", "Kennedy Town", "27 Bellevue Avenue", 34, "Coding,Web Development", 74, 31, 0),
    ("Aisha Rahman", "aisha.rahman.hk@gmail.com", "Kennedy Town", "5 Davis Street", 29, "Cooking,Grocery Shopping", 81, 27, 0),
    ("Ken Wong", "kenwong.cw@gmail.com", "Kennedy Town", "18 Cadogan Street", 24, "Dog Walking,Plant Care", 55, 9, 0),
    ("Priya Nair", "priya.nair.home@gmail.com", "Sai Ying Pun", "45 Queen's Road West", 37, "Tutoring,Math", 90, 52, 0),
    ("Tomas Ferreira", "tomas.ferreira.pt@gmail.com", "Sai Ying Pun", "8 High Street", 31, "Photography,Illustration", 68, 18, 0),
    ("Grace Ng", "grace.ng.syp@gmail.com", "Sai Ying Pun", "132 Des Voeux Road West", 52, "Sewing,Alterations", 77, 24, 0),
    ("Daniel Cho", "daniel.cho.mail@gmail.com", "Sai Ying Pun", "3 Centre Street", 26, "Furniture Assembly,Basic Repairs", 62, 12, 0),
    ("Mei Fong", "mei.fong.home@gmail.com", "Sheung Wan", "61 Bonham Strand", 47, "Baking,Event Planning", 84, 38, 0),
    ("Oliver Bennett", "oliver.bennett.uk@gmail.com", "Sheung Wan", "9 Tai Ping Shan Street", 33, "Bicycle Repair,DIY", 71, 22, 0),
    ("Hana Ito", "hana.ito.jp@gmail.com", "Sheung Wan", "28 Upper Station Street", 38, "Piano,Japanese Tutoring", 79, 19, 0),
    ("Raj Patel", "raj.patel.work@gmail.com", "North Point", "66 King's Road", 44, "Driving,Delivery,Furniture Moving", 65, 14, 0),
    ("Carmen Sze", "carmen.sze.np@gmail.com", "North Point", "11 Chun Yeung Street", 55, "Elderly Companionship,Cantonese", 86, 33, 0),
    ("Leo Ma", "leo.ma.np@gmail.com", "North Point", "23 Tin Chiu Street", 22, "Phone Troubleshooting,Social Media", 58, 7, 0),
]

HISTORY = [
    # category, title, description, minutes, complexity, quality, days_ago, helper, requester
    ("daily life", "Grocery run for Mrs. Cheung upstairs", "Weekly wet-market shop delivered to her door.", 30, 3, 4, 58, 2, 0),
    ("daily life", "Medicine pickup from the pharmacy", "Collect prescription and drop it at her flat.", 30, 2, 5, 49, 1, 9),
    ("daily life", "Two bags up from the minibus", "Carried shopping bags up three flights of stairs.", 30, 3, 3, 41, 8, 4),
    ("daily life", "Cook a congee pot for a new mum", "Prepared and left a warm dinner on the doorstep.", 60, 3, 5, 33, 0, 12),
    ("pets & animals", "Walk Biscuit the corgi", "Evening walk around the block, 30 minutes.", 30, 2, 4, 56, 3, 13),
    ("pets & animals", "Feed two cats over a weekend", "Two visits a day while owners were away.", 60, 3, 4, 35, 6, 5),
    ("pets & animals", "Vet taxi for a senior rabbit", "Carried the hutch to the clinic and back.", 45, 3, 5, 21, 10, 7),
    ("technology", "Set up a Wi-Fi router", "Configured and tested the new router.", 60, 3, 4, 52, 1, 3),
    ("technology", "Phone troubleshooting for a grandparent", "Set up WhatsApp and photo backups.", 60, 3, 5, 38, 13, 11),
    ("technology", "Laptop setup for a small studio", "New laptop, email, printer, cloud folder.", 120, 4, 4, 26, 1, 6),
    ("technology", "Fix a laptop that would not boot", "Reinstalled the system, kept all files.", 120, 4, 5, 12, 13, 1),
    ("repair & diy", "Assemble an IKEA wardrobe", "PAX wardrobe, all parts ready beforehand.", 75, 4, 4, 47, 7, 4),
    ("repair & diy", "Tighten a wobbly dining chair", "One leg reglued and clamped overnight.", 45, 2, 4, 30, 9, 8),
    ("repair & diy", "Repair a leaking kitchen tap", "Replaced the washer, no more drip.", 60, 3, 5, 18, 9, 10),
    ("family & kids", "School pickup for a P4 boy", "Walked him from school to the flat.", 60, 3, 4, 44, 12, 2),
    ("family & kids", "Saturday supervision, 2-5pm", "Puzzles and picture books with a 5-year-old.", 90, 3, 4, 29, 5, 0),
    ("family & kids", "Birthday party setup", "Decorations and games table for eight kids.", 90, 4, 5, 15, 8, 6),
    ("education", "DSE math past-paper session", "Went through one full paper, marked it.", 60, 3, 4, 40, 4, 13),
    ("education", "Beginner piano lesson", "First three scales and a simple piece.", 60, 3, 5, 24, 10, 3),
    ("education", "Cantonese conversation practice", "One hour of everyday dialogue practice.", 60, 3, 4, 9, 12, 9),
    ("transport", "Parcel run from the MTR locker", "Small parcel carried home from the station.", 30, 2, 4, 43, 11, 5),
    ("transport", "Drive a sofa across the island", "Van already owned; two people loading.", 75, 4, 4, 20, 11, 8),
    ("companionship", "Tea and chat with Uncle Wong", "Weekly visit, read the newspaper together.", 60, 2, 5, 51, 0, 12),
    ("companionship", "Accompany to a clinic appointment", "Sat in for the consultation, took notes.", 60, 3, 5, 27, 2, 10),
    ("creative", "Photograph a bakery's menu items", "Twelve pastries, natural light, edited set.", 60, 3, 5, 34, 5, 8),
    ("creative", "Design a block-party poster", "One A3 poster, two rounds of feedback.", 90, 3, 4, 16, 5, 0),
]

# Circle cast: one group per district. (task title, description, minutes,
# complexity, needed_in_days, category)
CIRCLE_TASKS = {
    "Kennedy Town": [
        ("Grocery pickup at the wet market", "One canvas bag of vegetables and fruit from the Smithfield market, to my door.", 30, 3, 4, "daily life"),
        ("Walk my beagle on Thursday evening", "Friendly six-year-old beagle, one 45-minute evening walk around the block.", 45, 3, 3, "pets & animals"),
        ("Collect my medicine from the clinic", "Small paper bag from the Kennedy Town clinic counter to my flat.", 30, 3, 5, "daily life"),
        ("Return two library books", "Drop two books at the Smithfield public library counter any weekday.", 45, 3, 6, "daily life"),
    ],
    "Sai Ying Pun": [
        ("Weekly fruit box pickup", "One box of fruit from the fruit shop on Centre Street, up to my walk-up.", 30, 3, 3, "daily life"),
        ("Water my balcony plants for a day trip", "Nine small pots on the balcony, one 45-minute visit.", 45, 3, 5, "daily life"),
        ("Help reach a high shelf box", "Carry one box of winter clothes down from the top cupboard.", 30, 3, 4, "daily life"),
        ("Post three parcels at the post office", "Carry three prepaid parcels to the Sai Ying Pun post office counter.", 45, 3, 6, "daily life"),
    ],
    "Sheung Wan": [
        ("Walk Mochi the shiba in the morning", "Calm five-year-old shiba, one 45-minute morning walk before 9am.", 45, 3, 4, "pets & animals"),
        ("Pick up my dry cleaning", "One garment bag from the cleaners on Hollywood Road.", 30, 3, 3, "daily life"),
        ("Deliver a birthday cake tin", "Borrowed cake tin to return to Auntie Mei two doors down.", 30, 3, 5, "daily life"),
    ],
    "North Point": [
        ("Grocery top-up from the wet market", "One bag of produce from Chun Yeung Street market to my lift lobby.", 45, 3, 4, "daily life"),
        ("Collect a prescription on Tuesday", "Small pharmacy bag from the clinic near the ferry pier.", 30, 3, 3, "daily life"),
        ("Return a rented dvd to the shop", "One dvd case back to the rental shop, five minutes away.", 30, 3, 6, "daily life"),
    ],
}

EXTRA_TASKS = [
    # owner_email, title, description, category, days, urgency, minutes, complexity
    ("vivian.lau.hk@gmail.com", "Move six theatre costume boxes", "From a storage room in Sai Wan to a car at the kerb; two trips down the stairs.", "daily life", 5, "urgent", 75, 4),
    ("daniel.cho.mail@gmail.com", "Assemble a study desk before Sunday", "120cm desk, all tools needed; happy to help carry it up too.", "repair & diy", 3, "soon", 75, 4),
    ("hana.ito.jp@gmail.com", "Piano practice audience wanted", "Play for my students' informal recital, clapping required.", "creative", 8, "flexible", 60, 3),
    ("leo.ma.np@gmail.com", "Set up grandma's new phone", "Contacts, WhatsApp, and teaching her the camera, patiently.", "technology", 6, "soon", 60, 3),
]

# Chats on completed jobs: HISTORY index -> [(speaker 'h'|'r', text), ...].
# Threads involve the three demo logins: Priya, Carmen, Vivian.
CHATS = {
    0: [("r", "Hi Aisha! Same standing order as last week — choy sum, tofu, and whatever fruit looks good."),
        ("h", "Got it! Leaving around 5pm, should reach you before dinner."),
        ("r", "You're a star. Money for the vegetables is in the red bag by the door."),
        ("h", "Delivered! Everything is on the kitchen bench like last time.")],
    2: [("r", "Mei, are you around Tuesday afternoon? The minibus drops me at the stop but the stairs are the problem."),
        ("h", "Tuesday works — meet you at the stop at 3:30."),
        ("r", "Lifesaver. Only two bags this time, I promise!"),
        ("h", "Haha, last time it was four. See you Tuesday!")],
    3: [("r", "Vivian, you mentioned you make a mean congee — any chance of one more pot this week?"),
        ("h", "Of course. Pumpkin and dried scallop okay? I'll bring it over around 6."),
        ("r", "Perfect. I'll leave a clean pot on the doorstep for the return trip!")],
    11: [("r", "Daniel, the wardrobe boxes arrived — all parts accounted for, I checked twice."),
         ("h", "Great. Do you have a hammer and a rubber mallet? I'll bring the rest."),
         ("r", "Both in the kitchen. Saturday morning still okay?"),
         ("h", "9am sharp. Should be done before lunch.")],
    14: [("r", "Carmen, could you take Thursday's pickup? I'll be at the clinic with my mum."),
         ("h", "No problem, I'll be at the school gate at 3:15. He knows me from the block party."),
         ("r", "He does! Snack money is in his front pocket."),
         ("h", "Home safe, fed and happy. See you next pickup!")],
    17: [("r", "Hi Ms. Nair, my mock paper went badly... could we go through it on Saturday?"),
         ("h", "Bring the paper and your calculator — we'll do the tricky questions first."),
         ("r", "Thank you!! I'll bring the marking scheme too."),
         ("h", "Good session today. Redo Q7 to Q9 before next Saturday and you'll be fine.")],
    19: [("r", "Hi Carmen, Oliver here — keen for another session before my dim sum bet with a colleague."),
         ("h", "Haha, sure. This week: ordering at the tea restaurant, and the taxi phrases."),
         ("r", "Exactly what I need. MTR exit B as usual?"),
         ("h", "Yes. Homework: read the menu I gave you out loud, twice.")],
    22: [("r", "Vivian, Uncle Wong keeps asking when you're free for tea and the newspapers."),
         ("h", "Tell him Thursday at 3. I'll bring egg tarts from the bakery on Catchick."),
         ("r", "He'll be delighted — he already saved you the sudoku section."),
         ("h", "That man plans better than I do.")],
    25: [("r", "Tomas, the residents' committee loved the first draft. One change — bigger time, we start at 2pm."),
         ("h", "Easy. New version with 2pm highlighted, sending tonight."),
         ("r", "You're fast! Could you do six printed copies too?"),
         ("h", "Done — dropping them to your mailbox on the way to class tomorrow.")],
}

# Circle group chat (chain_messages): district -> [(member email, hours_ago, text)]
GROUP_CHAT = {
    "Kennedy Town": [
        ("aisha.rahman.hk@gmail.com", 30, "Morning all! Nice to see our four needs line up so neatly."),
        ("marcus.cheng88@gmail.com", 26, "Vivian, don't worry about Biscuit — he's gentle once he knows you."),
        ("vivian.lau.hk@gmail.com", 24, "I used to have a beagle. He'll love Smithfield at sunset."),
        ("kenwong.cw@gmail.com", 6, "Still deciding between the market pickup and the library run — will pick tonight!"),
    ],
    "Sheung Wan": [
        ("mei.fong.home@gmail.com", 28, "All set for this week — I'll grab the dry cleaning after my market run."),
        ("oliver.bennett.uk@gmail.com", 22, "The cake tin is on my kitchen bench, Hana. If I'm out, it's in the hallway."),
        ("hana.ito.jp@gmail.com", 8, "Perfect. Mochi and I will be at the park by 8 — see you all at handover!"),
    ],
    "North Point": [
        ("carmen.sze.np@gmail.com", 30, "My half is sorted — Tuesday prescription pickup is booked with the clinic."),
        ("raj.patel.work@gmail.com", 25, "I can cover the dvd run if Leo is still stuck in Singapore."),
        ("leo.ma.np@gmail.com", 4, "Thanks both — Singapore got extended another week. I've filed the withdrawal request, sorry again."),
    ],
}


def chat_time(days: int, frac: float) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=-days + frac)
    return dt.isoformat(timespec="seconds")


def main() -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB) + suffix)
        if p.exists():
            p.unlink()
    A.init_db()

    with A.db() as conn:
        # Fresh cast --------------------------------------------------------
        conn.execute("DELETE FROM users")
        ids = {}
        for name, email, district, address, age, skills, reliability, balance, mod in USERS:
            cur = conn.execute(
                """INSERT INTO users(name,email,password,balance,is_moderator,language,
                       address,district,hkid,address_id,age,skills,created_at,
                       reliability,account_status,username,
                       verification_identity,verification_neighbourhood,
                       circle_enabled,community_helper)
                   VALUES (?,?,?,?,?,'en',?,?,?,?,?,?,?,?,'active',?,?,?,1,?)""",
                (name, email, A.hash_password("demo123"), balance, mod, address,
                 district, f"Z{(hash(email) % 900000) + 100000}({age % 10})",
                 district, age, skills, now_iso(-200), reliability,
                 email.split("@")[0], "verified" if reliability > 60 else "unverified",
                 "verified", 1 if mod else 0))
            ids[email] = cur.lastrowid
        for email in ids:  # welcome grant, same as a real signup
            A.ledger_entry(conn, ids[email], None, "welcome_credit", 10,
                           "Welcome credits for joining the community")

        mail_to_id = ids

        # Completed history (anchors the recommendation benchmarks) ---------
        hist_ids = []   # request id per HISTORY index, for chat seeding
        for (cat, title, desc, minutes, cx, q, days, h, r) in HISTORY:
            value = price(minutes, cx, q)
            created = now_iso(-days)
            completed = now_iso(-days + 2)
            cur = conn.execute(
                """INSERT INTO requests(title,description,category,requester_id,provider_id,
                       status,requester_value,requester_buffer,provider_value,provider_buffer,
                       agreed_value,effort_minutes,complexity,quality_score,
                       created_at,completed_at)
                   VALUES (?,?,?,?,?,'completed',?,?,?,?,?,?,?,?,?,?)""",
                (title, desc, cat, mail_to_id[USERS[r][1]], mail_to_id[USERS[h][1]],
                 value, 1, value, 1, value, minutes, cx, q, created, completed))
            hist_ids.append(cur.lastrowid)
            conn.execute(
                """INSERT INTO transactions(request_id,requester_id,provider_id,value,
                       effort_minutes,complexity,quality_score,kudos_given,kudos_bonus,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (cur.lastrowid, mail_to_id[USERS[r][1]], mail_to_id[USERS[h][1]],
                 value, minutes, cx, q, 1 if q >= 5 else 0, 1 if q >= 5 else 1, completed))

        # Chat history on completed jobs ------------------------------------
        for idx, thread in CHATS.items():
            days, h, r = HISTORY[idx][6], HISTORY[idx][7], HISTORY[idx][8]
            n = len(thread)
            for i, (who, body) in enumerate(thread):
                sender = mail_to_id[USERS[h][1] if who == "h" else USERS[r][1]]
                conn.execute(
                    """INSERT INTO messages(request_id,sender_id,kind,body,created_at)
                       VALUES (?,?,'chat',?,?)""",
                    (hist_ids[idx], sender, body, chat_time(days, (i + 1) / (n + 1) * 1.8)))

        # Open requests, priced by the app's own recommendation -------------
        circle_requests = {}   # district -> [request_id, ...]
        circle_owners = {}     # district -> [owner_user_id, ...] (same order)
        for district, tasks in CIRCLE_TASKS.items():
            owners = [u for u in USERS if u[2] == district]
            circle_requests[district] = []
            circle_owners[district] = []
            for i, (title, desc, minutes, cx, days, cat) in enumerate(tasks):
                owner_email = owners[i % len(owners)][1]
                rec = A.recommendation(conn, cat, minutes, cx)
                value = rec["recommended"]
                cur = conn.execute(
                    """INSERT INTO requests(title,description,category,location,needed_by,
                           urgency,requester_id,requester_value,requester_buffer,
                           effort_minutes,complexity,reservation_status,created_at)
                       VALUES (?,?,?,?,?,'flexible',?,?,?,?,?,'public',?)""",
                    (title, desc, cat, district, iso_date(days), mail_to_id[owner_email],
                     value, 1, minutes, cx, now_iso(-2)))
                circle_requests[district].append(cur.lastrowid)
                circle_owners[district].append(mail_to_id[owner_email])

        extra_ids = []
        for (email, title, desc, cat, days, urgency, minutes, cx) in EXTRA_TASKS:
            rec = A.recommendation(conn, cat, minutes, cx)
            cur = conn.execute(
                """INSERT INTO requests(title,description,category,location,needed_by,
                       urgency,requester_id,requester_value,requester_buffer,
                       effort_minutes,complexity,reservation_status,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,'public',?)""",
                (title, desc, cat, USERS[[u[1] for u in USERS].index(email)][2],
                 iso_date(days), urgency, mail_to_id[email], rec["recommended"], 1,
                 minutes, cx, now_iso(-1)))
            extra_ids.append(cur.lastrowid)

        # One disputed completed job ----------------------------------------
        disp_req = conn.execute(
            """INSERT INTO requests(title,description,category,requester_id,provider_id,
                   status,requester_value,provider_value,agreed_value,effort_minutes,
                   complexity,quality_score,created_at,completed_at)
               VALUES ('Hang three picture frames',
                       'Frames hung in the living room; one bracket arrived cracked.',
                       'repair & diy',?,?, 'disputed',6,6,6,45,3,3,?,?)""",
            (mail_to_id["grace.ng.syp@gmail.com"], mail_to_id["daniel.cho.mail@gmail.com"],
             now_iso(-9), now_iso(-6))).lastrowid
        conn.execute(
            """INSERT INTO transactions(request_id,requester_id,provider_id,value,
                   effort_minutes,complexity,quality_score,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (disp_req, mail_to_id["grace.ng.syp@gmail.com"],
             mail_to_id["daniel.cho.mail@gmail.com"], 6, 45, 3, 3, now_iso(-6)))
        conn.execute(
            """INSERT INTO disputes(request_id,opened_by,description,status,
                   requested_refund_amount,negotiation_status,review_since)
               VALUES (?,?,?,'open',3,'open',?)""",
            (disp_req, mail_to_id["grace.ng.syp@gmail.com"],
             "One of the three brackets was cracked and the frame leans to the left. "
             "Daniel offered to come back but cannot make it this week; I would like "
             "2-3 credits back to buy a replacement bracket.", now_iso(-5)))

        # Form the four Circles explicitly through the app's own persistence --
        conn.execute("DELETE FROM notifications")
        proposals = {}
        for district, tasks in CIRCLE_TASKS.items():
            reqs = circle_requests[district]
            values = [conn.execute("SELECT requester_value FROM requests WHERE id=?",
                                   (rid,)).fetchone()["requester_value"] for rid in reqs]
            titles = [conn.execute("SELECT title FROM requests WHERE id=?",
                                   (rid,)).fetchone()["title"] for rid in reqs]
            match = {
                "users": tuple(circle_owners[district]),
                "requests": reqs,
                "values": values,
                "titles": titles,
                "balancing_amount": max(values) - min(values),
                "balance_explanation": (
                    f"These {len(reqs)} nearby needs are between {min(values)} and "
                    f"{max(values)} credits. Pick one neighbour to help and someone picks "
                    "yours — when the loop closes, no credits move."),
            }
            proposals[district] = A.create_chain_proposal(conn, match)

        # Kennedy Town: one joined (selected), one rejoined, rest pending ----
        kt = proposals.get("Kennedy Town")
        if kt:
            members = conn.execute(
                "SELECT user_id, request_id FROM chain_members WHERE proposal_id=? ORDER BY position",
                (kt,)).fetchall()
            target = next(m["request_id"] for m in members if m["user_id"] != members[0]["user_id"])
            A.select_circle_task(conn, kt, members[0]["user_id"], target)
            # second member rejoined without picking a task yet
            conn.execute(
                """UPDATE chain_members SET response='accepted', responded_at=?
                   WHERE proposal_id=? AND user_id=?""",
                (A.now(), kt, members[1]["user_id"]))

        # Sheung Wan: everyone selects, propose + confirm -> ACTIVE ----------
        sw = proposals.get("Sheung Wan")
        if sw:
            members = conn.execute(
                "SELECT user_id, request_id FROM chain_members WHERE proposal_id=? ORDER BY position",
                (sw,)).fetchall()
            for i, m in enumerate(members):
                target = members[(i + 1) % len(members)]["request_id"]
                ok, msg = A.select_circle_task(conn, sw, m["user_id"], target)
                assert ok, f"SW select failed: {msg}"
            ok, msg = A.propose_start(conn, sw, members[0]["user_id"])
            assert ok, f"SW propose failed: {msg}"
            for m in members[1:]:
                ok, msg = A.confirm_start(conn, sw, m["user_id"])
            status = conn.execute("SELECT status FROM chain_proposals WHERE id=?", (sw,)).fetchone()["status"]
            assert status == "active", f"SW circle ended as {status}"

        # North Point: active, then an open withdrawal recovery case ---------
        np = proposals.get("North Point")
        if np:
            members = conn.execute(
                "SELECT user_id, request_id FROM chain_members WHERE proposal_id=? ORDER BY position",
                (np,)).fetchall()
            for i, m in enumerate(members):
                target = members[(i + 1) % len(members)]["request_id"]
                ok, msg = A.select_circle_task(conn, np, m["user_id"], target)
                assert ok, f"NP select failed: {msg}"
            ok, msg = A.propose_start(conn, np, members[0]["user_id"])
            assert ok, f"NP propose failed: {msg}"
            for m in members[1:]:
                A.confirm_start(conn, np, m["user_id"])
            case_id, wmsg = A.request_circle_withdrawal(
                conn, np, members[2]["user_id"],
                "I have been posted to Singapore for two weeks with work and cannot "
                "finish my side of the loop. Sorry everyone.")
            assert case_id, f"NP withdrawal failed: {wmsg}"

        # Circle group chat ---------------------------------------------------
        for district, msgs in GROUP_CHAT.items():
            pid = proposals.get(district)
            if not pid:
                continue
            for (email, hours_ago, body) in msgs:
                ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO chain_messages(proposal_id,sender_id,body,created_at) VALUES (?,?,?,?)",
                    (pid, ids[email], body, ts))

    print("Seed complete.")
    report()


def now_iso(days: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    return dt.isoformat(timespec="seconds")


def iso_date(days: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    return dt.date().isoformat()


def report() -> None:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    print("users:", c.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    print("open requests:", c.execute("SELECT COUNT(*) FROM requests WHERE status='open'").fetchone()[0])
    print("completed history:", c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0])
    for r in c.execute("SELECT id,status FROM chain_proposals ORDER BY id"):
        print("  circle", r["id"], r["status"])
    print("recovery cases:", [tuple(r) for r in c.execute(
        "SELECT id,circle_id,status FROM circle_recovery_cases")])
    print("disputes:", [tuple(r) for r in c.execute("SELECT id,status FROM disputes")])
    # realism scan
    bad = []
    for table, col in (("users", "name"), ("users", "email"), ("requests", "title"),
                       ("requests", "description")):
        for pat in ("demo", "sample", "placeholder", "mock", "lorem"):
            hits = c.execute(f"SELECT COUNT(*) FROM {table} WHERE lower({col}) LIKE ?",
                             (f"%{pat}%",)).fetchone()[0]
            if hits:
                bad.append((table, col, pat, hits))
    print("realism scan:", "CLEAN" if not bad else bad)
    c.close()


if __name__ == "__main__":
    main()

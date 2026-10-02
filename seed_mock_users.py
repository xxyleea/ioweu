"""Create 10 mock neighbours with addresses, skills and open requests.

Idempotent: skips if the mock neighbours already exist.
"""

from datetime import datetime, timedelta, timezone

from app import db, hash_password, init_db, infer_task_attributes, now

NEIGHBOURS = [
    ("Maya Lam", "maya.lam@example.com", "Kennedy Town", "12 Smithfield Road", 34, "Cooking,Grocery Shopping"),
    ("Jason Wong", "jason.wong@example.com", "Sai Ying Pun", "8 High Street", 27, "Coding,Web Development"),
    ("Priya Sharma", "priya.sharma@example.com", "Sheung Wan", "45 Queen's Road West", 31, "Photography,Illustration"),
    ("Uncle Wong", "uncle.wong@example.com", "Central", "3 Des Voeux Road", 68, "Conversation,Elderly Companionship"),
    ("Chloe Cheung", "chloe.cheung@example.com", "Wan Chai", "20 Johnston Road", 29, "Babysitting,Reading with Children"),
    ("Marcus Lee", "marcus.lee@example.com", "Causeway Bay", "7 Russell Street", 41, "Dog Walking,Pet Sitting"),
    ("Anna Kwan", "anna.kwan@example.com", "North Point", "66 King's Road", 23, "Math,Exam Preparation"),
    ("Tom Lau", "tom.lau@example.com", "Quarry Bay", "9 Taikoo Shing Road", 36, "Driving,Delivery"),
    ("Grace Ho", "grace.ho@example.com", "Tai Koo", "2 Tai Fung Avenue", 45, "Event Planning,Decoration"),
    ("Kelvin Yuen", "kelvin.yuen@example.com", "Pok Fu Lam", "88 Pok Fu Lam Road", 33, "Bicycle Repair,Basic Repairs"),
]

MOCK_REQUESTS = [
    ("maya.lam@example.com", "Help carry groceries up the hill", "Two big bags from the wet market, about 10 minutes of carrying.", "daily life", "Kennedy Town", 2, "urgent", 8, 2),
    ("maya.lam@example.com", "Teach me one Cantonese home dish", "I want to learn a simple dinner dish in my kitchen.", "daily life", "Kennedy Town", 6, "flexible", 10, 3),
    ("jason.wong@example.com", "Set up my new Wi-Fi router", "New router still in the box, need it configured and tested.", "technology", "Sai Ying Pun", 3, "soon", 10, 2),
    ("priya.sharma@example.com", "Photograph my bakery menu items", "About 12 pastries, natural light, 1 hour at my shop.", "creative", "Sheung Wan", 4, "soon", 14, 3),
    ("uncle.wong@example.com", "Fix a wobbly dining chair", "One wooden chair leg is loose, needs tightening or gluing.", "repair & diy", "Central", 7, "flexible", 6, 2),
    ("chloe.cheung@example.com", "Babysit my 5-year-old on Saturday", "Saturday 2-5pm, she likes picture books and puzzles.", "family & kids", "Wan Chai", 1, "urgent", 18, 3),
    ("marcus.lee@example.com", "Walk my corgi twice this week", "30 minutes each walk, he is friendly and used to strangers.", "pets & animals", "Causeway Bay", 5, "soon", 9, 2),
    ("anna.kwan@example.com", "DSE math practice session", "Need someone to go through one past paper with me, 90 minutes.", "education", "North Point", 6, "flexible", 12, 2),
    ("tom.lau@example.com", "Pick up a parcel from the MTR station", "Small parcel at Quarry Bay station locker, bring to my lobby.", "transport", "Quarry Bay", 2, "urgent", 5, 1),
    ("grace.ho@example.com", "Help plan our block party", "Need a second brain for the run sheet and decoration list.", "community", "Tai Koo", 10, "flexible", 15, 4),
    ("kelvin.yuen@example.com", "Assemble an IKEA wardrobe", "PAX 150cm, all parts ready, bring your own Allen keys if possible.", "repair & diy", "Pok Fu Lam", 4, "soon", 16, 3),
]

MOCK_BALANCES = [22, 35, 18, 50, 12, 27, 40, 15, 33, 20]


def main() -> None:
    init_db()
    with db() as connection:
        if connection.execute(
            "SELECT 1 FROM users WHERE email='maya.lam@example.com'"
        ).fetchone():
            print("Mock neighbours already exist.")
            return
        user_ids = {}
        for index, ((name, email, district, address, age, skills), balance) in enumerate(zip(NEIGHBOURS, MOCK_BALANCES)):
            hkid = f"Z{100000 + index}({index % 10})"
            cursor = connection.execute(
                """INSERT INTO users(name,email,password,balance,is_moderator,language,
                   address,district,hkid,address_id,age,skills,created_at)
                   VALUES (?,?,?,?,0,'en',?,?,?,?,?,?,?)""",
                (name, email, hash_password("demo123"), balance, address,
                 district, hkid, district, age, skills, now()),
            )
            user_ids[email] = cursor.lastrowid
        for email, title, description, category, location, days, urgency, value, buffer in MOCK_REQUESTS:
            effort, complexity = infer_task_attributes(title, description, category)
            needed_by = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
            connection.execute(
                """INSERT INTO requests(
                    title,description,category,location,needed_by,urgency,requester_id,
                    requester_value,requester_buffer,effort_minutes,complexity,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (title, description, category, location, needed_by, urgency,
                 user_ids[email], value, buffer, effort, complexity, now()),
            )
        print(f"Created {len(NEIGHBOURS)} mock neighbours and {len(MOCK_REQUESTS)} open requests.")


if __name__ == "__main__":
    main()

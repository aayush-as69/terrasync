"""
TerraSync — dummy data seeder

Populates departments, wards, issue_categories, staff, citizens, and issues
with realistic-looking data that matches schema.sql AND the values the
frontend (terrasync-admin-app.html) already knows how to render — same
category ids, ward names, department names, and status vocabulary used by
CATS / CAT_DEPT / WARD_SLA / STAGES in the app's JS.

Usage:
  cp env.example .env       # fill in real DB_* values (same as app.py)
  pip install -r requirements.txt
  mysql -u root -p < schema.sql
  python seed_data.py                # default: 15 staff, 120 citizens, 40 issues
  python seed_data.py --issues 500 --citizens 200 --staff 20
  python seed_data.py --reset         # TRUNCATEs issues/citizens/staff/etc. first
  python seed_data.py --export-xlsx seed_logins.xlsx   # also write plaintext logins
                                                         # for the accounts just created
                                                         # (requires: pip install openpyxl)

Idempotency: re-running without --reset is safe for reference tables
(departments/wards/issue_categories use INSERT ... ON DUPLICATE KEY UPDATE)
but will add MORE citizens/staff/issues each run, since those don't have a
natural unique business key to de-dupe on. Use --reset for a clean slate.
"""

import argparse
import os
import random
import string
from datetime import datetime, timedelta, timezone

import bcrypt
import pymysql
import pymysql.cursors
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    port=int(os.environ.get("DB_PORT", 3306)),
    user=os.environ.get("DB_USER", "terrasync_app"),
    password=os.environ.get("DB_PASSWORD", ""),
    database=os.environ.get("DB_NAME", "terrasync"),
    cursorclass=pymysql.cursors.DictCursor,
    autocommit=False,
)

# ---------------------------------------------------------------------------
# Reference data — mirrors what terrasync-admin-app.html already expects
# (CATS / CAT_DEPT / WARD_SLA / DEPTS / STAGES). Keep these in sync if the
# frontend's reference data ever changes.
# ---------------------------------------------------------------------------
DEPARTMENTS = ["Sanitation", "Roads", "Electrical", "Water", "Parks", "Health & Enforcement"]

WARDS = [
    ("Sector 18, Noida", 48),
    ("Sector 62, Noida", 48),
    ("Sector 137, Noida", 48),
    ("Pari Chowk, Greater Noida", 48),
    ("Alpha 1, Greater Noida", 72),
]

# (category_id, category_name) — ids match CATS[].id in the frontend
CATEGORIES = [
    ("road", "Roads & potholes"),
    ("waste", "Garbage & waste"),
    ("water", "Water leakage"),
    ("light", "Streetlights"),
    ("drain", "Drainage"),
    ("green", "Green cover"),
    ("air", "Air/noise"),
    ("dump", "Illegal dumping"),
]

# category_id -> department name, matches CAT_DEPT in the frontend
CAT_DEPT = {
    "road": "Roads", "drain": "Roads",
    "waste": "Sanitation", "dump": "Sanitation",
    "water": "Water",
    "light": "Electrical",
    "green": "Parks",
    "air": "Health & Enforcement",
}

STATUSES = ["Reported", "Acknowledged", "InProgress", "Resolved", "Verified"]
# Weighted so most issues aren't sitting fresh as "Reported" — gives the
# dashboard KPIs / SLA badges / priority queue something realistic to show.
STATUS_WEIGHTS = [0.30, 0.20, 0.20, 0.18, 0.12]

SEVERITIES = ["high", "med", "low"]
SEVERITY_WEIGHTS = [0.25, 0.45, 0.30]

STAFF_ROLES = ["field_officer", "dept_head", "super_admin"]
STAFF_ROLE_WEIGHTS = [0.75, 0.20, 0.05]
DUTY_STATUSES = ["available", "onsite", "offduty"]
DUTY_STATUS_WEIGHTS = [0.5, 0.3, 0.2]

ISSUE_TITLES = {
    "road": [
        "Large pothole on {loc}", "Road surface collapsing near {loc}",
        "Cracked pavement outside {loc}", "Uneven road patch near {loc}",
    ],
    "waste": [
        "Overflowing bin near {loc}", "Garbage not collected at {loc}",
        "Waste pileup outside {loc}",
    ],
    "water": [
        "Broken water pipe near {loc}", "Water leakage flooding {loc}",
        "Continuous pipe leak at {loc}",
    ],
    "light": [
        "Streetlight out near {loc}", "Flickering streetlight at {loc}",
        "Dark stretch near {loc}, no working lights",
    ],
    "drain": [
        "Choked drain near {loc}", "Drain backflow risk at {loc}",
        "Blocked stormwater drain outside {loc}",
    ],
    "green": [
        "Dying trees on median near {loc}", "Overgrown vegetation blocking path at {loc}",
        "Fallen branch near {loc}",
    ],
    "air": [
        "Loud generator noise near {loc}", "Construction noise past permitted hours at {loc}",
        "Smoke/dust nuisance near {loc}",
    ],
    "dump": [
        "Illegal debris dumping behind {loc}", "Construction waste dumped near {loc}",
        "Unauthorized dumping spotted at {loc}",
    ],
}

LOCATIONS = [
    "Sector 12 market", "DPS crossing", "the park gate", "Lane 3", "the community hall",
    "the bus stand", "Sector 62 market", "the school compound", "the ring road junction",
    "the metro station exit", "the water tank", "Sector 21 residential block",
]

DESCRIPTIONS = [
    "Reported by multiple residents, getting worse by the day.",
    "Ongoing issue for over a week, needs urgent attention.",
    "Noticed after last night's rain, blocking part of the road.",
    "Safety hazard for pedestrians and two-wheelers alike.",
    "Photo evidence attached, please assign a team soon.",
    "Same spot flagged before, seems to have recurred.",
    "Affecting nearby shops and daily commuters.",
    "Minor for now but likely to worsen without action.",
]

FIRST_NAMES = [
    "Aayush", "Priya", "Rohan", "Fatima", "Karan", "Meera", "Ibrahim", "Sneha",
    "Aditi", "Vikram", "Zara", "Arjun", "Kavya", "Rahul", "Ananya", "Farhan",
    "Divya", "Sameer", "Neha", "Aman", "Pooja", "Rajesh", "Sunita", "Manoj",
    "Deepak", "Anita", "Ramesh", "Kavita", "Suresh", "Nisha",
]
LAST_NAMES = [
    "Kumar", "Sharma", "Verma", "Singh", "Gupta", "Yadav", "Tiwari", "Chauhan",
    "Mehta", "Desai", "Pal", "Ali", "Khan", "Reddy", "Nair", "Iyer", "Joshi",
    "Malhotra", "Kapoor", "Bansal",
]

# Approximate real-world centers for each ward. The frontend maps are
# centered on Sector 62, Noida (28.6229, 77.3640), so seeded issue
# coordinates need to fall in and around Noida / Greater Noida — not the
# previous 23.30-23.40N/85.25-85.35E box, which is actually Ranchi.
WARD_COORDS = {
    "Sector 18, Noida": (28.5701, 77.3260),
    "Sector 62, Noida": (28.6229, 77.3640),
    "Sector 137, Noida": (28.5355, 77.3910),
    "Pari Chowk, Greater Noida": (28.4744, 77.5040),
    "Alpha 1, Greater Noida": (28.4780, 77.4980),
}
# Jitter radius (in degrees) so points scatter naturally around each
# ward's center instead of stacking on a single point. ~0.008 deg is
# roughly under 1km, keeping points within the named ward.
WARD_COORD_JITTER = 0.008


def rand_name():
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def rand_phone():
    # Indian mobile numbers must start with 6-9 to pass normalize_phone()'s
    # INDIAN_MOBILE_RE check — a fully random first digit can produce
    # numbers (e.g. +910123456789) that the app's own OTP validator rejects.
    first_digit = random.choice("6789")
    rest = "".join(random.choices(string.digits, k=9))
    return "+91" + first_digit + rest


def weighted_choice(options, weights):
    return random.choices(options, weights=weights, k=1)[0]


def balanced_pool(options, weights, count):
    """Return `count` values drawn from `options` in proportion to `weights`
    (largest-remainder rounding), shuffled. Unlike weighted_choice() this
    guarantees every option shows up at the expected rate even for a small
    dataset, so a 40-issue demo reliably covers every category/status."""
    total = float(sum(weights))
    exact = [count * w / total for w in weights]
    counts = [int(x) for x in exact]
    leftovers = sorted(range(len(options)), key=lambda i: exact[i] - counts[i], reverse=True)
    for i in leftovers[: count - sum(counts)]:
        counts[i] += 1
    pool = [o for o, n in zip(options, counts) for _ in range(n)]
    random.shuffle(pool)
    return pool


def hash_pw(plain):
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def rand_past_datetime(max_days_ago=45):
    delta = timedelta(
        days=random.randint(0, max_days_ago),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )
    return datetime.now(timezone.utc).replace(tzinfo=None) - delta


# ---------------------------------------------------------------------------
# Seeding steps
# ---------------------------------------------------------------------------
def seed_reference_data(cur):
    for name in DEPARTMENTS:
        cur.execute(
            "INSERT INTO departments (dept_name) VALUES (%s) "
            "ON DUPLICATE KEY UPDATE dept_name = dept_name",
            (name,),
        )
    for name, sla in WARDS:
        cur.execute(
            "INSERT INTO wards (ward_name, sla_hours) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE sla_hours = VALUES(sla_hours)",
            (name, sla),
        )
    for cat_id, cat_name in CATEGORIES:
        cur.execute(
            "INSERT INTO issue_categories (category_id, category_name) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE category_name = VALUES(category_name)",
            (cat_id, cat_name),
        )


def fetch_lookup_maps(cur):
    cur.execute("SELECT dept_id, dept_name FROM departments")
    dept_by_name = {r["dept_name"]: r["dept_id"] for r in cur.fetchall()}
    cur.execute("SELECT ward_id, ward_name FROM wards")
    ward_by_name = {r["ward_name"]: r["ward_id"] for r in cur.fetchall()}
    return dept_by_name, ward_by_name


def seed_staff(cur, count, dept_by_name, ward_by_name, credentials_out):
    staff_ids = []
    used_codes = set()
    for i in range(count):
        name = rand_name()
        while True:
            code = f"EMP-{random.randint(1000, 9999)}"
            if code not in used_codes:
                used_codes.add(code)
                break
        role = weighted_choice(STAFF_ROLES, STAFF_ROLE_WEIGHTS)
        dept_name = random.choice(DEPARTMENTS)
        ward_name = random.choice(WARDS)[0]
        duty_status = weighted_choice(DUTY_STATUSES, DUTY_STATUS_WEIGHTS)
        phone = rand_phone()
        password = "password123"
        cur.execute(
            """INSERT INTO staff
               (full_name, employee_code, password_hash, role, phone, dept_id, ward_id, duty_status, is_active)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)""",
            (
                name, code, hash_pw(password), role, phone,
                dept_by_name[dept_name], ward_by_name[ward_name], duty_status,
            ),
        )
        staff_ids.append(cur.lastrowid)
        credentials_out["staff"].append({
            "staff_id": cur.lastrowid,
            "full_name": name,
            "employee_code": code,
            "password": password,
            "role": role,
            "department": dept_name,
            "ward": ward_name,
            "duty_status": duty_status,
            "phone": phone,
        })
    return staff_ids


def seed_citizens(cur, count, ward_by_name, credentials_out):
    citizen_ids = []
    for i in range(count):
        name = rand_name()
        phone = rand_phone()
        email = f"{name.lower().replace(' ', '.')}{random.randint(1,999)}@example.com"
        ward_name = random.choice(list(ward_by_name.keys()))
        ward_id = ward_by_name[ward_name]
        password = "password123"
        cur.execute(
            """INSERT INTO citizens
               (display_name, phone, email, password_hash, is_verified, home_ward_id, points)
               VALUES (%s, %s, %s, %s, TRUE, %s, %s)""",
            (name, phone, email, hash_pw(password), ward_id, random.randint(0, 500)),
        )
        citizen_id = cur.lastrowid
        cur.execute(
            "INSERT INTO citizen_notification_prefs (citizen_id) VALUES (%s)",
            (citizen_id,),
        )
        citizen_ids.append(citizen_id)
        credentials_out["citizens"].append({
            "citizen_id": citizen_id,
            "display_name": name,
            "phone": phone,
            "email": email,
            "password": password,
            "home_ward": ward_name,
        })
    return citizen_ids


def seed_issues(cur, count, citizen_ids, staff_ids, ward_by_name, dept_by_name):
    cur.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTRING(issue_code, 4) AS UNSIGNED)), 2500) AS max_num FROM issues"
    )
    next_num = cur.fetchone()["max_num"] + 1

    # Staff grouped by department so "assigned" officers make sense for
    # the issue's category (mirrors CAT_DEPT-based eligibility filtering
    # in the Work Queue tab's assignment dropdown).
    cur.execute("SELECT staff_id, dept_id FROM staff WHERE staff_id IN %s", (tuple(staff_ids),))
    staff_by_dept = {}
    for row in cur.fetchall():
        staff_by_dept.setdefault(row["dept_id"], []).append(row["staff_id"])

    # Balanced (not purely random) so a small demo dataset still shows every
    # category, ward, status and severity.
    cat_ids = list(CAT_DEPT.keys())
    cat_pool = balanced_pool(cat_ids, [1] * len(cat_ids), count)
    ward_pool = balanced_pool([w for w, _ in WARDS], [1] * len(WARDS), count)
    status_pool = balanced_pool(STATUSES, STATUS_WEIGHTS, count)
    severity_pool = balanced_pool(SEVERITIES, SEVERITY_WEIGHTS, count)

    for i in range(count):
        cat_id = cat_pool[i]
        dept_name = CAT_DEPT[cat_id]
        dept_id = dept_by_name[dept_name]
        ward_name = ward_pool[i]
        ward_id = ward_by_name[ward_name]

        title = random.choice(ISSUE_TITLES[cat_id]).format(loc=random.choice(LOCATIONS))
        description = random.choice(DESCRIPTIONS)
        severity = severity_pool[i]
        status = status_pool[i]
        citizen_id = random.choice(citizen_ids)
        created_at = rand_past_datetime()

        assigned_staff_id = None
        candidates = staff_by_dept.get(dept_id)
        # Only assign an officer once the issue has moved past "Reported",
        # matching how the frontend uses officer=null for fresh reports.
        if status != "Reported" and candidates:
            assigned_staff_id = random.choice(candidates)

        ward_lat, ward_lng = WARD_COORDS[ward_name]
        lat = round(ward_lat + random.uniform(-WARD_COORD_JITTER, WARD_COORD_JITTER), 6)
        lng = round(ward_lng + random.uniform(-WARD_COORD_JITTER, WARD_COORD_JITTER), 6)
        issue_code = f"TS-{next_num}"
        next_num += 1

        cur.execute(
            """INSERT INTO issues
               (issue_code, title, description, category_id, ward_id, severity,
                status, assigned_staff_id, citizen_id, latitude, longitude,
                location_text, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                issue_code, title, description, cat_id, ward_id, severity,
                status, assigned_staff_id, citizen_id, lat, lng,
                random.choice(LOCATIONS), created_at,
            ),
        )
        issue_id = cur.lastrowid

        # Build a status history trail consistent with the final status,
        # each entry timestamped after created_at (so ordering makes sense
        # if you ever query issue_status_history directly).
        stage_order = STATUSES[: STATUSES.index(status) + 1]
        ts = created_at
        for stage in stage_order:
            ts = ts + timedelta(hours=random.randint(1, 20))
            cur.execute(
                "INSERT INTO issue_status_history (issue_id, status, changed_at) VALUES (%s, %s, %s)",
                (issue_id, stage, min(ts, datetime.now(timezone.utc).replace(tzinfo=None))),
            )


def reset_tables(cur):
    # Order matters: children before parents. Reference tables
    # (departments/wards/issue_categories) are left intact since other
    # code may depend on stable ids across runs.
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    for table in [
        "sms_log", "issue_status_history", "issues",
        "citizen_notification_prefs", "citizens",
        "staff_otp_codes", "citizen_otp_codes", "sessions",
        "staff",
    ]:
        cur.execute(f"TRUNCATE TABLE {table}")
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")


def export_credentials_xlsx(path, credentials):
    """Write the plaintext login credentials generated during this seeding
    run to an Excel workbook. Must be called with the SAME `credentials`
    dict populated during seed_staff/seed_citizens — passwords are hashed
    before they ever reach the database, so this is the only place they're
    recoverable after a run."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = openpyxl.Workbook()
    header_font = Font(name="Arial", bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="0F766E", end_color="0F766E", fill_type="solid")
    body_font = Font(name="Arial")

    def write_sheet(ws, title, rows, columns):
        ws.title = title
        ws.append([label for _, label in columns])
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")
        for row in rows:
            ws.append([row[key] for key, _ in columns])
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.font = body_font
        for col_cells in ws.columns:
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col_cells) + 3
            ws.column_dimensions[col_cells[0].column_letter].width = min(width, 40)
        ws.freeze_panes = "A2"

    ws_staff = wb.active
    write_sheet(
        ws_staff, "Staff Logins", credentials["staff"],
        [
            ("employee_code", "Employee Code"), ("password", "Password"),
            ("full_name", "Full Name"), ("role", "Role"),
            ("department", "Department"), ("ward", "Ward"),
            ("duty_status", "Duty Status"), ("phone", "Phone"),
            ("staff_id", "staff_id (DB)"),
        ],
    )

    ws_citizens = wb.create_sheet("Citizen Logins")
    write_sheet(
        ws_citizens, "Citizen Logins", credentials["citizens"],
        [
            ("phone", "Phone"), ("email", "Email"), ("password", "Password"),
            ("display_name", "Display Name"), ("home_ward", "Home Ward"),
            ("citizen_id", "citizen_id (DB)"),
        ],
    )

    ws_notes = wb.create_sheet("How to log in")
    notes = [
        ["TerraSync — seeded login credentials", ""],
        ["", ""],
        ["Staff (admin console)", ""],
        ["1. Go to terrasync-admin-login.html", ""],
        ["2. Enter the Employee Code + Password from 'Staff Logins'", ""],
        ["3. An OTP is then required. In dev mode (FLASK_DEBUG=1), the OTP", ""],
        ["   is printed to the Flask console and returned as dev_otp in", ""],
        ["   the login response — check there instead of a real SMS.", ""],
        ["", ""],
        ["Citizen (citizen app)", ""],
        ["Option A — Phone + OTP (default tab):", ""],
        ["  Enter any phone number from 'Citizen Logins'. Development OTPs", ""],
        ["  are generated per request and returned as dev_otp in the", ""],
        ["  send-otp response — check there instead of a real SMS.", ""],
        ["Option B — Email + Password:", ""],
        ["  Use the Email + Password columns from 'Citizen Logins'.", ""],
        ["", ""],
        ["All seeded passwords are 'password123' — change before any real deployment.", ""],
    ]
    for r in notes:
        ws_notes.append(r)
    for cell in ws_notes["A"]:
        cell.font = body_font
    ws_notes["A1"].font = Font(name="Arial", bold=True, size=13)
    ws_notes.column_dimensions["A"].width = 70

    wb.save(path)


def main():
    parser = argparse.ArgumentParser(description="Seed TerraSync with dummy data.")
    parser.add_argument("--staff", type=int, default=15, help="number of staff/officers to create")
    parser.add_argument("--citizens", type=int, default=120, help="number of citizens to create")
    parser.add_argument("--issues", type=int, default=40, help="number of issues to create")
    parser.add_argument("--reset", action="store_true", help="truncate existing data first")
    parser.add_argument(
        "--export-xlsx", metavar="PATH", default=None,
        help="write an Excel workbook of the plaintext login credentials generated "
             "this run (e.g. --export-xlsx seed_logins.xlsx). Requires openpyxl.",
    )
    args = parser.parse_args()

    conn = pymysql.connect(**DB_CONFIG)
    credentials = {"staff": [], "citizens": []}
    try:
        with conn.cursor() as cur:
            if args.reset:
                print("Resetting existing data...")
                reset_tables(cur)

            print("Seeding reference data (departments, wards, categories)...")
            seed_reference_data(cur)
            dept_by_name, ward_by_name = fetch_lookup_maps(cur)

            print(f"Seeding {args.staff} staff...")
            staff_ids = seed_staff(cur, args.staff, dept_by_name, ward_by_name, credentials)

            print(f"Seeding {args.citizens} citizens...")
            citizen_ids = seed_citizens(cur, args.citizens, ward_by_name, credentials)

            print(f"Seeding {args.issues} issues...")
            seed_issues(cur, args.issues, citizen_ids, staff_ids, ward_by_name, dept_by_name)

        conn.commit()
        print("Done. All changes committed.")

        if args.export_xlsx:
            export_credentials_xlsx(args.export_xlsx, credentials)
            print(f"Login credentials written to {args.export_xlsx}")
    except Exception:
        conn.rollback()
        print("Error — rolled back, no changes were saved.")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()

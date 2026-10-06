"""
TerraSync — Flask backend
Wires the four HTML prototypes (citizen login/app, admin login/app) to a
real MySQL database for authentication and session handling.

Auth flows implemented:
  Citizen  -> phone number + OTP (matches the default "Phone · OTP" tab
              in terrasync-citizen-login.html)
  Staff    -> employee code + password, then OTP sent to registered number
              (matches terrasync-admin-login.html)

Run:
  cp .env.example .env      # fill in real values
  pip install -r requirements.txt
  mysql -u root -p < schema.sql
  flask --app app run --debug
"""

import os
import math
import re
import uuid
import json
import logging
import random
import string
import hmac
import hashlib
import base64
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import wraps

import bcrypt
import pymysql
import pymysql.cursors
from dotenv import load_dotenv
from flask import Flask, request, jsonify, g, send_from_directory

load_dotenv()

import gemini_service  # noqa: E402  (after load_dotenv so GEMINI_* env vars are visible)

# In dev, print the OTP to the console instead of sending a real SMS.
# Swap send_sms() for a real provider (Twilio, MSG91, etc.) before going live.
# This also drives the SECRET_KEY / COOKIE_SECURE defaults below, so it's
# resolved before anything else touches the environment.
DEV_MODE = os.environ.get("FLASK_DEBUG", "0") == "1" or os.environ.get("FLASK_ENV") == "development"

_DEFAULT_DEV_SECRET = "dev-secret-change-me"
SECRET_KEY = os.environ.get("SECRET_KEY")

if not SECRET_KEY:
    if DEV_MODE:
        SECRET_KEY = _DEFAULT_DEV_SECRET
    else:
        # Refuse to boot with an unset/guessable secret outside of dev —
        # a fallback here would silently make every session forgeable.
        raise RuntimeError(
            "SECRET_KEY is not set. Set a long random SECRET_KEY in your "
            "environment before running outside of development "
            "(FLASK_DEBUG=1 / FLASK_ENV=development)."
        )
elif SECRET_KEY == _DEFAULT_DEV_SECRET and not DEV_MODE:
    raise RuntimeError(
        "SECRET_KEY is still set to the placeholder dev value. Set a real, "
        "random SECRET_KEY before running outside of development."
    )

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = SECRET_KEY

DB_CONFIG = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    port=int(os.environ.get("DB_PORT", 3306)),
    user=os.environ.get("DB_USER", "terrasync_app"),
    password=os.environ.get("DB_PASSWORD", ""),
    database=os.environ.get("DB_NAME", "terrasync"),
    cursorclass=pymysql.cursors.DictCursor,
    autocommit=True,
)

CITIZEN_COOKIE = "terrasync_session"
STAFF_COOKIE = "terrasync_admin_session"
CITIZEN_SESSION_DAYS = 30
STAFF_SESSION_HOURS = 12
OTP_TTL_MINUTES = 5
OTP_RESEND_COOLDOWN_SECONDS = 30    # min gap between two sends to the same phone/staff
OTP_MAX_ATTEMPTS = 5                # wrong codes allowed against one outstanding OTP
OTP_MAX_SENDS_PER_WINDOW = 5        # sends per phone/staff within OTP_RATE_WINDOW_MINUTES
OTP_MAX_SENDS_PER_IP_WINDOW = 10    # sends per IP within OTP_RATE_WINDOW_MINUTES
OTP_RATE_WINDOW_MINUTES = 15

# Issue photo uploads — saved under static/uploads/issues/ so the existing
# catch-all static_files() route below can serve them back out at
# /uploads/issues/<filename> with no extra route needed.
ALLOWED_PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB
UPLOAD_SUBDIR = "uploads/issues"
UPLOAD_DIR = os.path.join(app.static_folder, "uploads", "issues")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Secure cookies require HTTPS. Default to "secure" everywhere except when
# we can tell we're in local dev (FLASK_DEBUG=1 / FLASK_ENV=development),
# which is normally plain http://127.0.0.1. COOKIE_SECURE in the
# environment always wins if it's explicitly set, so a real deployment
# that forgets to flip DEV_MODE off can still force this via .env.
_cookie_secure_env = os.environ.get("COOKIE_SECURE")
if _cookie_secure_env is not None:
    COOKIE_SECURE = _cookie_secure_env.lower() == "true"
else:
    COOKIE_SECURE = not DEV_MODE

# Gemini AI configuration. The API key, model names and timeout are read by
# gemini_service.py from GEMINI_API_KEY / GEMINI_MODEL / GEMINI_FALLBACK_MODEL /
# GEMINI_TIMEOUT_SECONDS. The key is server-side only and never reaches the browser.
GEMINI_ENABLED = os.environ.get("GEMINI_ENABLED", "true").lower() == "true"
# Auto-assign only confident, non-duplicate AI tickets; everything else goes to a human.
AI_AUTO_ASSIGN = os.environ.get("AI_AUTO_ASSIGN", "true").lower() == "true"
# Cost guard: max Gemini-analysed reports per citizen per hour (extra reports are still filed).
AI_MAX_PER_CITIZEN_PER_HOUR = int(os.environ.get("AI_MAX_PER_CITIZEN_PER_HOUR", 10))
# Duplicate check: only open issues within this radius count as candidates.
DUPLICATE_RADIUS_METERS = int(os.environ.get("DUPLICATE_RADIUS_METERS", 300))

# SMS. SMS_PROVIDER = "console" (prints to the server log; default in dev) or
# "twilio" (REST API over HTTPS, no SDK needed). Used for OTPs and for
# complaint-tracking notifications.
SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "console" if DEV_MODE else "").strip().lower()
SMS_NOTIFICATIONS_ENABLED = os.environ.get("SMS_NOTIFICATIONS_ENABLED", "true").lower() == "true"
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_FROM = os.environ.get("TWILIO_FROM", "").strip()  # sender number, or leave blank and use the service SID
TWILIO_MESSAGING_SERVICE_SID = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "").strip()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("terrasync")
_gemini_client = None


# ---------------------------------------------------------------------------
# DB connection (one per request, closed automatically)
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = pymysql.connect(**DB_CONFIG)
        # TerraSync stores authentication/session timestamps as UTC.
        # Force this MySQL connection to UTC so NOW(), CURRENT_TIMESTAMP,
        # cleanup queries, OTP expiry checks, and session expiry checks all
        # use the same clock regardless of the server's local timezone.
        with g.db.cursor() as cur:
            cur.execute("SET time_zone = '+00:00'")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def gen_otp(length=6):
    return "".join(random.choices(string.digits, k=length))


def hash_otp(otp):
    """HMAC-SHA256 of the code, keyed with SECRET_KEY.

    We never store the raw code — only this hash — so a DB dump doesn't
    hand over usable OTPs. Comparison is done with hmac.compare_digest to
    avoid timing side-channels.
    """
    return hmac.new(SECRET_KEY.encode(), otp.encode(), hashlib.sha256).hexdigest()


def cleanup_expired_otps(cur):
    """Best-effort delete of rows that can no longer be used.

    Called opportunistically on every send-otp request so the tables don't
    grow unbounded even without a cron job wired up. Cheap (indexed on
    otp_id/phone or staff_id) and safe to run concurrently.
    """
    cur.execute("DELETE FROM citizen_otp_codes WHERE expires_at < NOW()")
    cur.execute("DELETE FROM staff_otp_codes WHERE expires_at < NOW()")


def _send_twilio(phone, message):
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not (TWILIO_FROM or TWILIO_MESSAGING_SERVICE_SID):
        raise RuntimeError("Twilio is not configured (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM)")
    fields = {"To": phone, "Body": message}
    if TWILIO_MESSAGING_SERVICE_SID:
        fields["MessagingServiceSid"] = TWILIO_MESSAGING_SERVICE_SID
    else:
        fields["From"] = TWILIO_FROM
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
        data=urllib.parse.urlencode(fields).encode(),
        method="POST",
    )
    token = base64.b64encode(f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Twilio HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")


def send_sms(phone, message):
    """Send one SMS through the configured provider. Raises on failure."""
    if SMS_PROVIDER == "console":
        print(f"[DEV SMS to {phone}]: {message}")
    elif SMS_PROVIDER == "twilio":
        _send_twilio(phone, message)
    else:
        raise NotImplementedError("Set SMS_PROVIDER=twilio (or console for local dev)")


# ---------------------------------------------------------------------------
# Complaint-tracking SMS notifications
#
# Citizens get a text when their complaint is received, assigned to a
# department, or changes status. Sending never blocks or breaks the request
# that triggered it: the message is logged to sms_log, then delivered on a
# background thread, which records sent/failed. Citizens who switched off
# "status changes" (citizen_notification_prefs) are skipped.
# ---------------------------------------------------------------------------
SEVERITY_LABEL = {"high": "High", "med": "Medium", "low": "Low"}
STATUS_LABEL = {"Acknowledged": "Acknowledged", "InProgress": "In Progress",
                "Resolved": "Resolved", "Verified": "Verified"}


def build_sms_text(event, code, category, severity, detail):
    ref = f"#{code}"
    if event == "received":
        msg = f"TerraSync: Your complaint {ref} has been received."
        if category:
            msg += f" Category: {category}."
        msg += f" Priority: {SEVERITY_LABEL.get(severity, 'Medium')}."
        if detail:
            msg += f" Assigned to {detail}."
        return msg
    if event == "assigned":
        return f"TerraSync: Complaint {ref} has been assigned to {detail or 'the relevant department'}."
    label = STATUS_LABEL.get(detail, detail)
    if detail == "Resolved":
        return f"TerraSync: Complaint {ref} has been marked Resolved. Please confirm in the TerraSync app if it is fixed."
    if detail == "Acknowledged":
        return f"TerraSync: Complaint {ref} has been Acknowledged by the city team."
    return f"TerraSync: Complaint {ref} is now {label}."


def _deliver_sms_async(log_id, phone, message):
    def run():
        status, err = "sent", None
        try:
            send_sms(phone, message)
        except Exception as e:  # never let a provider problem escape the thread
            status, err = "failed", str(e)[:500]
            logger.warning("SMS to %s failed: %s", phone, err)
        try:
            conn = pymysql.connect(**DB_CONFIG)
            try:
                with conn.cursor() as cur:
                    cur.execute("UPDATE sms_log SET status = %s, error = %s WHERE log_id = %s",
                                (status, err, log_id))
            finally:
                conn.close()
        except Exception:
            logger.exception("Could not update sms_log %s", log_id)
    threading.Thread(target=run, daemon=True).start()


def notify_citizen(cur, issue_id, event, detail=None):
    """event: 'received' | 'assigned' | 'status'. For 'received'/'assigned',
    detail is a department name; for 'status' it is the new status.
    Best-effort: any error is logged and swallowed."""
    if not SMS_NOTIFICATIONS_ENABLED:
        return
    try:
        cur.execute(
            """SELECT i.issue_code, i.severity, c.citizen_id, c.phone, cat.category_name,
                      COALESCE(p.notify_status_changes, TRUE) AS wants_sms
               FROM issues i
               JOIN citizens c ON c.citizen_id = i.citizen_id
               LEFT JOIN issue_categories cat ON cat.category_id = i.category_id
               LEFT JOIN citizen_notification_prefs p ON p.citizen_id = c.citizen_id
               WHERE i.issue_id = %s""",
            (issue_id,),
        )
        row = cur.fetchone()
        if not row:
            return
        log_event = detail if event == "status" else event
        message = build_sms_text(event, row["issue_code"], row["category_name"], row["severity"], detail)
        skip = None
        if not row["phone"]:
            skip = "citizen has no phone number"
        elif not row["wants_sms"]:
            skip = "citizen turned off status notifications"
        cur.execute(
            """INSERT INTO sms_log (issue_id, citizen_id, event, phone, message, status, error)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (issue_id, row["citizen_id"], log_event, row["phone"], message,
             "skipped" if skip else "queued", skip),
        )
        if not skip:
            _deliver_sms_async(cur.lastrowid, row["phone"], message)
    except Exception:
        logger.exception("SMS notification failed for issue %s (%s)", issue_id, event)


INDIAN_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")


def normalize_phone(raw):
    """Normalize to +91XXXXXXXXXX for a valid 10-digit Indian mobile number.

    Returns None for anything that isn't a valid Indian mobile number
    (wrong length, wrong leading digit, junk input) instead of silently
    producing a malformed value like "+12345".
    """
    digits = re.sub(r"\D", "", raw or "")

    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]

    if not INDIAN_MOBILE_RE.match(digits):
        return None

    return "+91" + digits


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PhotoUploadError(ValueError):
    """Raised for any invalid-upload case; message is safe to show the user."""


def save_issue_photo(file_storage):
    """Validate and persist an uploaded issue photo.

    Returns the URL to store in issues.photo_url (e.g.
    "/uploads/issues/<uuid>.jpg"), or None if no file was actually
    provided (an empty <input type=file> still shows up in request.files
    with an empty filename, which isn't an error — it just means "no
    photo attached").
    """
    if not file_storage or not file_storage.filename:
        return None

    if "." not in file_storage.filename:
        raise PhotoUploadError("Photo must be a JPG, PNG, or WEBP file")
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_PHOTO_EXTENSIONS:
        raise PhotoUploadError("Photo must be a JPG, PNG, or WEBP file")

    # Size check without trusting Content-Length: seek to the end of the
    # stream Werkzeug already buffered, measure, then rewind before saving.
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_PHOTO_BYTES:
        raise PhotoUploadError("Photo must be under 5MB")
    if size == 0:
        raise PhotoUploadError("Uploaded photo is empty")

    # Random filename — never trust the client's original filename for a
    # path, and this also avoids collisions between citizens.
    filename = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(UPLOAD_DIR, filename))
    return f"/{UPLOAD_SUBDIR}/{filename}"


def set_session_cookie(resp, name, session_id, expires, samesite):
    resp.set_cookie(
        name,
        session_id,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=samesite,
        expires=expires,
    )


# ---------------------------------------------------------------------------
# Gemini civic intelligence
# ---------------------------------------------------------------------------
def gemini_ready():
    return bool(GEMINI_ENABLED and gemini_service.is_configured())


DEPT_BY_CATEGORY = {
    "road": "Roads", "drain": "Roads", "waste": "Sanitation", "dump": "Sanitation",
    "water": "Water", "light": "Electrical", "green": "Parks", "air": "Health & Enforcement",
}


def local_triage_fallback(title, description, selected_category, selected_severity, categories, departments):
    """Safe fallback so civic reporting never depends on an external API."""
    cat_ids = {cid for cid, _ in categories}
    dept_by_cat = DEPT_BY_CATEGORY
    category_id = selected_category if selected_category in cat_ids else "road"
    severity = selected_severity if selected_severity in {"high", "med", "low"} else "med"
    department = dept_by_cat.get(category_id, departments[0] if departments else "Roads")
    return {
        "category_id": category_id,
        "severity": severity,
        "summary": (description or title).strip()[:240],
        "suggested_title": title[:90],
        "department": department,
        "risk": "none",
        "detected_language": "English / unknown",
        "duplicate_signal": "none",
        "similar_issue_code": "",
        "duplicate_reason": "",
        "confidence": 0.0,
    }


def get_ai_context(cur, ward_id):
    cur.execute("SELECT category_id, category_name FROM issue_categories ORDER BY category_id")
    categories = [(r["category_id"], r["category_name"]) for r in cur.fetchall()]
    cur.execute("SELECT dept_name FROM departments ORDER BY dept_name")
    departments = [r["dept_name"] for r in cur.fetchall()]
    cur.execute(
        """SELECT issue_code, category_id, severity, title, description, location_text,
                  latitude, longitude
           FROM issues
           WHERE ward_id = %s AND status NOT IN ('Resolved', 'Verified')
           ORDER BY created_at DESC LIMIT 20""",
        (ward_id,),
    )
    similar_issues = cur.fetchall()
    return categories, departments, similar_issues


def haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def parse_coord(value, lo, hi):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def duplicate_candidates(similar_issues, lat, lng):
    """Open ward issues that could be the same physical problem.

    With a GPS fix: only issues within DUPLICATE_RADIUS_METERS, nearest first.
    Without one: the 5 most recent ward issues (no distance), since location
    can't rule anything out. Max 5 either way."""
    out = []
    for r in similar_issues:
        cand = {
            "code": r["issue_code"], "title": r["title"],
            "description": (r["description"] or "")[:200], "category": r["category_id"],
            "distance_m": None,
        }
        if lat is not None and lng is not None:
            rlat = parse_coord(r.get("latitude"), -90, 90)
            rlng = parse_coord(r.get("longitude"), -180, 180)
            if rlat is None or rlng is None:
                continue
            d = haversine_m(lat, lng, rlat, rlng)
            if d > DUPLICATE_RADIUS_METERS:
                continue
            cand["distance_m"] = int(d)
        out.append(cand)
    if lat is not None and lng is not None:
        out.sort(key=lambda c: c["distance_m"])
    return out[:5]


def ai_over_hourly_limit(cur, citizen_id):
    """Per-citizen AI cost guard: past the limit the report is still filed,
    just without Gemini."""
    cur.execute(
        """SELECT COUNT(*) AS n FROM issues
           WHERE citizen_id = %s AND ai_status = 'done'
             AND ai_processed_at > (NOW() - INTERVAL 1 HOUR)""",
        (citizen_id,),
    )
    return cur.fetchone()["n"] >= AI_MAX_PER_CITIZEN_PER_HOUR


def auto_route_to_department(cur, department, ward_id):
    """Assign to an available/on-site officer in the recommended department.

    If no officer is available, the issue remains unassigned but keeps the AI
    department recommendation for the admin work queue.
    """
    if not department:
        return None
    cur.execute(
        """SELECT s.staff_id
           FROM staff s
           JOIN departments d ON d.dept_id = s.dept_id
           WHERE d.dept_name = %s
             AND s.ward_id = %s
             AND s.is_active = TRUE
           ORDER BY CASE s.duty_status WHEN 'available' THEN 0 WHEN 'onsite' THEN 1 ELSE 2 END,
                    s.staff_id
           LIMIT 1""",
        (department, ward_id),
    )
    row = cur.fetchone()
    return row["staff_id"] if row else None


# ---------------------------------------------------------------------------
# Auth guards
# ---------------------------------------------------------------------------
def require_citizen(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        session_id = request.cookies.get(CITIZEN_COOKIE)
        if not session_id:
            return jsonify({"error": "Not logged in"}), 401

        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """SELECT c.citizen_id, c.display_name, c.phone, c.home_ward_id, c.points
                   FROM sessions s JOIN citizens c ON c.citizen_id = s.citizen_id
                   WHERE s.session_id = %s AND s.actor_type = 'citizen' AND s.expires_at > NOW()""",
                (session_id,),
            )
            citizen = cur.fetchone()

        if not citizen:
            return jsonify({"error": "Session expired"}), 401

        g.citizen = citizen
        return f(*args, **kwargs)

    return wrapper


def require_staff(roles=None):
    """roles: optional list, e.g. ['super_admin', 'dept_head']"""

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            session_id = request.cookies.get(STAFF_COOKIE)
            if not session_id:
                return jsonify({"error": "Not logged in"}), 401

            db = get_db()
            with db.cursor() as cur:
                cur.execute(
                    """SELECT s.staff_id, s.full_name, s.role, s.dept_id, s.ward_id, s.employee_code
                       FROM sessions ses JOIN staff s ON s.staff_id = ses.staff_id
                       WHERE ses.session_id = %s AND ses.actor_type = 'staff' AND ses.expires_at > NOW()""",
                    (session_id,),
                )
                staff = cur.fetchone()

            if not staff:
                return jsonify({"error": "Session expired"}), 401
            if roles and staff["role"] not in roles:
                return jsonify({"error": "Forbidden"}), 403

            g.staff = staff
            return f(*args, **kwargs)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Citizen auth — phone + OTP (default flow on terrasync-citizen-login.html)
#
# Real per-request OTPs, generated and stored in citizen_otp_codes and
# validated on verify — mirrors the staff OTP flow below (same TTL,
# same "consume on use" pattern) instead of a hardcoded shortcut.
# ---------------------------------------------------------------------------
@app.post("/api/citizen/send-otp")
def citizen_send_otp():
    data = request.get_json(force=True)
    phone = normalize_phone(data.get("phone", ""))

    if not phone:
        return jsonify({"error": "Enter a valid 10-digit Indian mobile number"}), 400

    ip = request.remote_addr
    now = utcnow()
    window_start = now - timedelta(minutes=OTP_RATE_WINDOW_MINUTES)

    db = get_db()
    with db.cursor() as cur:
        cleanup_expired_otps(cur)

        cur.execute(
            "SELECT created_at FROM citizen_otp_codes WHERE phone = %s ORDER BY otp_id DESC LIMIT 1",
            (phone,),
        )
        last = cur.fetchone()
        if last and (now - last["created_at"]).total_seconds() < OTP_RESEND_COOLDOWN_SECONDS:
            wait = OTP_RESEND_COOLDOWN_SECONDS - int((now - last["created_at"]).total_seconds())
            return jsonify({"error": f"Please wait {wait}s before requesting another code"}), 429

        cur.execute(
            "SELECT COUNT(*) AS n FROM citizen_otp_codes WHERE phone = %s AND created_at > %s",
            (phone, window_start),
        )
        if cur.fetchone()["n"] >= OTP_MAX_SENDS_PER_WINDOW:
            return jsonify({"error": "Too many codes requested for this number. Try again later."}), 429

        if ip:
            cur.execute(
                "SELECT COUNT(*) AS n FROM citizen_otp_codes WHERE ip_address = %s AND created_at > %s",
                (ip, window_start),
            )
            if cur.fetchone()["n"] >= OTP_MAX_SENDS_PER_IP_WINDOW:
                return jsonify({"error": "Too many codes requested. Try again later."}), 429

        otp = gen_otp()
        expires = now + timedelta(minutes=OTP_TTL_MINUTES)
        cur.execute(
            "INSERT INTO citizen_otp_codes (phone, otp_hash, ip_address, expires_at) VALUES (%s, %s, %s, %s)",
            (phone, hash_otp(otp), ip, expires),
        )

    send_sms(phone, f"Your TerraSync verification code is {otp}. It expires in {OTP_TTL_MINUTES} minutes.")

    masked = phone[:6] + "••••" + phone[-2:]
    resp = {"phone_masked": masked}
    if DEV_MODE:
        resp["dev_otp"] = otp  # only present in dev, same as the staff flow
    return jsonify(resp), 200


@app.post("/api/citizen/verify-otp")
def citizen_verify_otp():
    data = request.get_json(force=True)
    phone = normalize_phone(data.get("phone", ""))
    otp = data.get("otp", "")
    display_name = data.get("display_name")  # optional, used on first signup

    if not phone:
        return jsonify({"error": "Enter a valid 10-digit Indian mobile number"}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """SELECT otp_id, otp_hash, attempts FROM citizen_otp_codes
               WHERE phone = %s AND consumed_at IS NULL AND expires_at > NOW()
               ORDER BY otp_id DESC LIMIT 1""",
            (phone,),
        )
        row = cur.fetchone()

        if not row:
            return jsonify({"error": "Invalid or expired code"}), 400

        if row["attempts"] >= OTP_MAX_ATTEMPTS:
            # Lock this code out rather than let it be brute-forced forever;
            # the user has to request a fresh one.
            cur.execute("UPDATE citizen_otp_codes SET consumed_at = NOW() WHERE otp_id = %s", (row["otp_id"],))
            return jsonify({"error": "Too many incorrect attempts. Request a new code."}), 429

        if not hmac.compare_digest(hash_otp(otp), row["otp_hash"]):
            cur.execute("UPDATE citizen_otp_codes SET attempts = attempts + 1 WHERE otp_id = %s", (row["otp_id"],))
            return jsonify({"error": "Invalid or expired code"}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute("UPDATE citizen_otp_codes SET consumed_at = NOW() WHERE otp_id = %s", (row["otp_id"],))
        # find or create the citizen — OTP login doubles as signup on first use
        cur.execute("SELECT citizen_id FROM citizens WHERE phone = %s", (phone,))
        citizen = cur.fetchone()
        if not citizen:
            cur.execute(
                "INSERT INTO citizens (display_name, phone, is_verified) VALUES (%s, %s, TRUE)",
                (display_name or "Citizen", phone),
            )
            citizen_id = cur.lastrowid
            cur.execute(
                "INSERT INTO citizen_notification_prefs (citizen_id) VALUES (%s)", (citizen_id,)
            )
        else:
            citizen_id = citizen["citizen_id"]
            cur.execute("UPDATE citizens SET is_verified = TRUE WHERE citizen_id = %s", (citizen_id,))

        session_id = str(uuid.uuid4())
        expires = utcnow() + timedelta(days=CITIZEN_SESSION_DAYS)
        cur.execute(
            """INSERT INTO sessions (session_id, actor_type, citizen_id, ip_address, user_agent, expires_at)
               VALUES (%s, 'citizen', %s, %s, %s, %s)""",
            (session_id, citizen_id, request.remote_addr, request.headers.get("User-Agent", ""), expires),
        )

    resp = jsonify({"success": True, "citizen_id": citizen_id})
    set_session_cookie(resp, CITIZEN_COOKIE, session_id, expires, "Lax")
    return resp


@app.post("/api/citizen/register")
def citizen_register():
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    email = data.get("email", "").strip()
    raw_phone = data.get("phone", "").strip()
    # Run through the same normalize_phone() used by OTP login so a citizen
    # who registers with email+password and later logs in with phone OTP
    # (or vice versa) resolves to one consistent +91XXXXXXXXXX row instead
    # of silently creating a second citizen record.
    phone = normalize_phone(raw_phone) if raw_phone else None
    password = data.get("password", "")

    if not name or not email or not password:
        return jsonify({"error": "Name, email, and password are required"}), 400

    if raw_phone and not phone:
        # Phone is optional, but if one was supplied it must be a real
        # 10-digit Indian mobile number — don't silently drop it and let
        # this citizen end up with no phone on file.
        return jsonify({"error": "Enter a valid 10-digit Indian mobile number, or leave phone blank"}), 400

    hashed_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO citizens (display_name, email, phone, password_hash, is_verified) VALUES (%s, %s, %s, %s, TRUE)",
                (name, email, phone, hashed_pw)
            )
            citizen_id = cur.lastrowid

            # Setup notification preferences
            cur.execute("INSERT INTO citizen_notification_prefs (citizen_id) VALUES (%s)", (citizen_id,))

        return jsonify({"ok": True, "message": "Account created successfully"}), 201
    except pymysql.err.IntegrityError:
        return jsonify({"error": "An account with this email or phone already exists"}), 409


@app.post("/api/citizen/login-email")
def citizen_login_email():
    data = request.get_json(force=True)
    email = data.get("email", "").strip()
    password = data.get("password", "")

    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT citizen_id, password_hash FROM citizens WHERE email = %s", (email,))
        citizen = cur.fetchone()

    # Verify user exists and password matches
    if not citizen or not citizen.get("password_hash") or not bcrypt.checkpw(password.encode('utf-8'), citizen["password_hash"].encode('utf-8')):
        return jsonify({"error": "Invalid email or password"}), 401

    # Create session
    session_id = str(uuid.uuid4())
    expires = utcnow() + timedelta(days=CITIZEN_SESSION_DAYS)
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO sessions (session_id, actor_type, citizen_id, ip_address, user_agent, expires_at)
               VALUES (%s, 'citizen', %s, %s, %s, %s)""",
            (session_id, citizen["citizen_id"], request.remote_addr, request.headers.get("User-Agent", ""), expires),
        )

    resp = jsonify({"ok": True, "citizen_id": citizen["citizen_id"]})
    set_session_cookie(resp, CITIZEN_COOKIE, session_id, expires, "Lax")
    return resp


@app.post("/api/citizen/logout")
def citizen_logout():
    session_id = request.cookies.get(CITIZEN_COOKIE)
    if session_id:
        db = get_db()
        with db.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
    resp = jsonify({"ok": True})
    resp.delete_cookie(CITIZEN_COOKIE)
    return resp


@app.get("/api/citizen/me")
@require_citizen
def citizen_me():
    return jsonify(g.citizen)


@app.patch("/api/citizen/me")
@require_citizen
def citizen_update_me():
    data = request.get_json(force=True)
    db = get_db()

    fields = []
    values = []

    if "display_name" in data:
        display_name = (data.get("display_name") or "").strip()
        if not display_name:
            return jsonify({"error": "Display name cannot be empty"}), 400
        fields.append("display_name = %s")
        values.append(display_name)

    if "home_ward_id" in data:
        home_ward_id = data.get("home_ward_id")
        if home_ward_id is not None:
            with db.cursor() as cur:
                cur.execute("SELECT ward_id FROM wards WHERE ward_id = %s", (home_ward_id,))
                if not cur.fetchone():
                    return jsonify({"error": "Invalid ward"}), 400
        fields.append("home_ward_id = %s")
        values.append(home_ward_id)

    if not fields:
        return jsonify({"error": "Nothing to update"}), 400

    values.append(g.citizen["citizen_id"])
    with db.cursor() as cur:
        cur.execute(f"UPDATE citizens SET {', '.join(fields)} WHERE citizen_id = %s", values)
        cur.execute(
            "SELECT citizen_id, display_name, phone, home_ward_id, points FROM citizens WHERE citizen_id = %s",
            (g.citizen["citizen_id"],),
        )
        updated = cur.fetchone()

    return jsonify(updated)


@app.get("/api/wards")
def list_wards():
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT ward_id, ward_name FROM wards ORDER BY ward_name")
        wards = cur.fetchall()
    return jsonify(wards)


# ---------------------------------------------------------------------------
# Staff auth — employee code + password, then OTP (terrasync-admin-login.html)
# ---------------------------------------------------------------------------
@app.post("/api/staff/login")
def staff_login():
    data = request.get_json(force=True)
    employee_code = data.get("employee_code", "").strip()
    password = data.get("password", "")

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """SELECT staff_id, password_hash, full_name, role, phone
               FROM staff WHERE employee_code = %s AND is_active = TRUE""",
            (employee_code,),
        )
        staff = cur.fetchone()

    if not staff or not staff["password_hash"] or not bcrypt.checkpw(
        password.encode(), staff["password_hash"].encode()
    ):
        return jsonify({"error": "Invalid employee ID or password"}), 401

    ip = request.remote_addr
    now = utcnow()
    window_start = now - timedelta(minutes=OTP_RATE_WINDOW_MINUTES)
    staff_id = staff["staff_id"]

    with db.cursor() as cur:
        cleanup_expired_otps(cur)

        cur.execute(
            "SELECT created_at FROM staff_otp_codes WHERE staff_id = %s ORDER BY otp_id DESC LIMIT 1",
            (staff_id,),
        )
        last = cur.fetchone()
        if last and (now - last["created_at"]).total_seconds() < OTP_RESEND_COOLDOWN_SECONDS:
            wait = OTP_RESEND_COOLDOWN_SECONDS - int((now - last["created_at"]).total_seconds())
            return jsonify({"error": f"Please wait {wait}s before requesting another code"}), 429

        cur.execute(
            "SELECT COUNT(*) AS n FROM staff_otp_codes WHERE staff_id = %s AND created_at > %s",
            (staff_id, window_start),
        )
        if cur.fetchone()["n"] >= OTP_MAX_SENDS_PER_WINDOW:
            return jsonify({"error": "Too many codes requested for this account. Try again later."}), 429

        if ip:
            cur.execute(
                "SELECT COUNT(*) AS n FROM staff_otp_codes WHERE ip_address = %s AND created_at > %s",
                (ip, window_start),
            )
            if cur.fetchone()["n"] >= OTP_MAX_SENDS_PER_IP_WINDOW:
                return jsonify({"error": "Too many codes requested. Try again later."}), 429

        otp = gen_otp()
        expires = now + timedelta(minutes=OTP_TTL_MINUTES)
        cur.execute(
            "INSERT INTO staff_otp_codes (staff_id, otp_hash, ip_address, expires_at) VALUES (%s, %s, %s, %s)",
            (staff_id, hash_otp(otp), ip, expires),
        )

    send_sms(staff["phone"], f"Your TerraSync admin code is {otp}. It expires in {OTP_TTL_MINUTES} minutes.")

    resp = {"staff_id": staff["staff_id"], "requires_otp": True}
    if DEV_MODE:
        resp["dev_otp"] = otp
    return jsonify(resp)


@app.post("/api/staff/verify-otp")
def staff_verify_otp():
    data = request.get_json(force=True)
    staff_id = data.get("staff_id")
    otp = data.get("otp", "")

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """SELECT otp_id, otp_hash, attempts FROM staff_otp_codes
               WHERE staff_id = %s AND consumed_at IS NULL AND expires_at > NOW()
               ORDER BY otp_id DESC LIMIT 1""",
            (staff_id,),
        )
        row = cur.fetchone()

        if not row:
            return jsonify({"error": "Invalid or expired code"}), 401

        if row["attempts"] >= OTP_MAX_ATTEMPTS:
            cur.execute("UPDATE staff_otp_codes SET consumed_at = NOW() WHERE otp_id = %s", (row["otp_id"],))
            return jsonify({"error": "Too many incorrect attempts. Request a new code."}), 429

        if not hmac.compare_digest(hash_otp(otp), row["otp_hash"]):
            cur.execute("UPDATE staff_otp_codes SET attempts = attempts + 1 WHERE otp_id = %s", (row["otp_id"],))
            return jsonify({"error": "Invalid or expired code"}), 401

    with db.cursor() as cur:
        cur.execute("UPDATE staff_otp_codes SET consumed_at = NOW() WHERE otp_id = %s", (row["otp_id"],))

        session_id = str(uuid.uuid4())
        expires = utcnow() + timedelta(hours=STAFF_SESSION_HOURS)
        cur.execute(
            """INSERT INTO sessions (session_id, actor_type, staff_id, ip_address, user_agent, expires_at)
               VALUES (%s, 'staff', %s, %s, %s, %s)""",
            (session_id, staff_id, request.remote_addr, request.headers.get("User-Agent", ""), expires),
        )

    resp = jsonify({"ok": True})
    set_session_cookie(resp, STAFF_COOKIE, session_id, expires, "Strict")
    return resp


@app.post("/api/staff/logout")
def staff_logout():
    session_id = request.cookies.get(STAFF_COOKIE)
    if session_id:
        db = get_db()
        with db.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
    resp = jsonify({"ok": True})
    resp.delete_cookie(STAFF_COOKIE)
    return resp


@app.get("/api/staff/me")
@require_staff()
def staff_me():
    return jsonify(g.staff)


# ---------------------------------------------------------------------------
# Example protected app routes — shows how the rest of the app should read
# the logged-in user instead of trusting anything from the request body
# ---------------------------------------------------------------------------
@app.post("/api/issues")
@require_citizen
def create_issue():
    # The citizen app submits multipart/form-data when a photo is attached
    # (needed to send the file), and falls back to a JSON body otherwise.
    is_multipart = (request.content_type or "").startswith("multipart/form-data")
    if is_multipart:
        data = request.form
        photo_file = request.files.get("photo")
    else:
        data = request.get_json(force=True)
        photo_file = None

    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()

    try:
        photo_url = save_issue_photo(photo_file)
    except PhotoUploadError as e:
        return jsonify({"error": str(e)}), 400

    # A photo alone is enough: Gemini writes the title and description from
    # it. Without a photo the citizen must still describe the problem.
    if not photo_url and not (title or description):
        return jsonify({"error": "Attach a photo or describe the problem"}), 400

    # Read the saved photo back into memory for Gemini multimodal analysis.
    # This is bounded by MAX_PHOTO_BYTES, so the request stays predictable.
    photo_bytes = None
    if photo_url:
        local_photo = os.path.join(app.static_folder, photo_url.lstrip("/"))
        try:
            with open(local_photo, "rb") as fh:
                photo_bytes = fh.read()
        except OSError:
            logger.warning("Could not read saved issue photo for Gemini: %s", local_photo)

    db = get_db()
    with db.cursor() as cur:
        categories, departments, similar_issues = get_ai_context(cur, g.citizen["home_ward_id"])
        over_limit = ai_over_hourly_limit(cur, g.citizen["citizen_id"])
        cur.execute("SELECT ward_name FROM wards WHERE ward_id = %s", (g.citizen["home_ward_id"],))
        ward_row = cur.fetchone()

    selected_category = (data.get("category_id") or "").strip()
    selected_severity = (data.get("severity") or "med").strip()
    latitude = parse_coord(data.get("latitude"), -90, 90)
    longitude = parse_coord(data.get("longitude"), -180, 180)
    if latitude is None or longitude is None:
        latitude = longitude = None
    location_text = (data.get("location_text") or "").strip()
    category_map = dict(categories)
    candidates = duplicate_candidates(similar_issues, latitude, longitude)

    # --- Gemini advisory analysis -------------------------------------------
    # gemini_service never raises and never touches the DB. Its output is
    # re-validated against our own category/department/candidate lists, then
    # gemini_service.decide() applies the trust policy (confidence threshold,
    # citizen override on low confidence, human-review flag).
    if not GEMINI_ENABLED:
        res = gemini_service.AnalysisResult(status="skipped", error="GEMINI_ENABLED=false")
    elif over_limit:
        res = gemini_service.AnalysisResult(status="skipped", error="AI hourly limit reached for this account")
    else:
        res = gemini_service.analyze_report(
            title=title,
            description=description,
            location_text=location_text,
            ward_name=ward_row["ward_name"] if ward_row else None,
            lat=latitude,
            lng=longitude,
            categories=category_map,
            departments=departments,
            image_bytes=photo_bytes,
            image_ext=photo_url.rsplit(".", 1)[-1] if photo_url else None,
            candidates=candidates,
        )

    policy = gemini_service.decide(
        res,
        category_ids=set(category_map),
        citizen_category=selected_category,
        citizen_severity=selected_severity,
    )
    # Local deterministic fallback = last layer: used for anything the AI
    # didn't (or wasn't trusted to) decide, so a complaint always survives.
    local = local_triage_fallback(
        title, description, selected_category, selected_severity, categories, departments
    )
    # Photo-only report: fill the blanks from Gemini's ticket. If Gemini is
    # down, use a plain placeholder so the report is still filed (and the
    # ticket is already flagged for human review by decide()).
    if not title:
        title = (res.title if policy["civic"] and res.title else "") or "Civic issue reported with photo"
    if not description:
        description = (
            res.summary if policy["civic"] and res.summary
            else "Photo report submitted without a description. Needs staff review."
        )
    if not local["summary"]:
        local["summary"] = description[:240]
    category_id = policy["category"] or local["category_id"]
    severity = policy["severity"] or local["severity"]
    department = res.department if policy["civic"] and res.department in departments else None
    if not department:
        department = DEPT_BY_CATEGORY.get(category_id)
        if department not in departments:
            department = departments[0] if departments else "Roads"

    done = policy["done"]
    confident = policy["confident"]
    needs_review = policy["needs_review"]
    ai_summary = (res.summary if policy["civic"] and res.summary else local["summary"])[:1000]
    ai_title = (res.title if confident and res.title else title)[:255]
    risk = ((res.risk if policy["civic"] else "") or "none")[:64]
    language = ((res.detected_language if done else "") or "unknown")[:80]
    confidence = res.confidence if done and res.confidence is not None else 0.0

    # Duplicate signal: only a code that really was one of the candidates.
    dup_code = res.duplicate_code if done else None
    dup_confidence = res.duplicate_confidence if dup_code else None
    if dup_code:
        duplicate_signal = "likely" if (dup_confidence or 0) >= 0.75 else "possible"
    else:
        duplicate_signal = "none"

    ai_status = res.status  # done | failed | skipped
    ai_model = res.model if done else "fallback"

    with db.cursor() as cur:
        placeholder_code = f"TS-PENDING-{uuid.uuid4().hex[:12]}"
        # Auto-route only a confident, non-duplicate ticket. Anything else
        # lands in the department queue (flagged for review) for a human.
        routed_staff_id = None
        if AI_AUTO_ASSIGN and confident and not dup_code:
            routed_staff_id = auto_route_to_department(cur, department, g.citizen["home_ward_id"])
        cur.execute(
            """INSERT INTO issues
               (issue_code, title, description, category_id, ward_id, severity,
                citizen_id, latitude, longitude, location_text, photo_url,
                ai_category_id, ai_severity, ai_summary, ai_department, ai_risk,
                ai_detected_language, ai_duplicate_signal, ai_similar_issue_code,
                ai_duplicate_reason, ai_confidence, ai_model, ai_processed_at,
                ai_status, ai_needs_review, ai_duplicate_confidence,
                assigned_staff_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(),
                       %s, %s, %s, %s)""",
            (
                placeholder_code,
                ai_title,
                description,
                category_id,
                g.citizen["home_ward_id"],
                severity,
                g.citizen["citizen_id"],
                latitude,
                longitude,
                location_text,
                photo_url,
                category_id if done else None,
                severity if done else None,
                ai_summary,
                department,
                risk,
                language,
                duplicate_signal,
                dup_code or "",
                "",
                confidence,
                ai_model,
                ai_status,
                needs_review,
                dup_confidence,
                routed_staff_id,
            ),
        )
        issue_id = cur.lastrowid
        issue_code = f"TS-{2500 + issue_id}"
        cur.execute("UPDATE issues SET issue_code = %s WHERE issue_id = %s", (issue_code, issue_id))
        cur.execute("INSERT INTO issue_status_history (issue_id, status) VALUES (%s, 'Reported')", (issue_id,))

        routed_name = None
        routed_dept = None
        if routed_staff_id:
            cur.execute(
                """SELECT s.full_name, d.dept_name FROM staff s
                   LEFT JOIN departments d ON d.dept_id = s.dept_id WHERE s.staff_id = %s""",
                (routed_staff_id,),
            )
            routed = cur.fetchone()
            routed_name = routed["full_name"] if routed else None
            routed_dept = routed["dept_name"] if routed else None

        notify_citizen(cur, issue_id, "received", routed_dept)

    return jsonify({
        "issue_id": issue_id,
        "issue_code": issue_code,
        "ai": {
            "enabled": gemini_ready(),
            # "ok" when Gemini produced a validated ticket; the citizen app
            # only shows the Gemini line for "ok".
            "status": "ok" if done else ai_status,
            "model": ai_model,
            "category": category_id,
            "severity": severity,
            "title": ai_title,
            "summary": ai_summary,
            "department": department,
            "risk": risk,
            "language": language,
            "duplicate_signal": duplicate_signal,
            "similar_issue_code": dup_code or "",
            "confidence": confidence,
            "needs_review": needs_review,
            "routed_to": routed_name,
        },
    }), 201


@app.get("/api/issues")
@require_citizen
def citizen_list_issues():
    """
    'My reports' feed for the citizen app: every issue the logged-in
    citizen has filed, most recent first, with the assigned officer's
    name and department so the frontend can show status + who's on it
    without a second round trip.
    """
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """SELECT i.issue_id AS id, i.issue_code, i.title, i.description AS desc_text,
                      i.severity AS sev, i.status, i.latitude AS lat, i.longitude AS lng,
                      i.location_text, i.photo_url, i.created_at,
                      w.ward_name AS ward, cat.category_id AS cat, cat.category_name AS cat_label,
                      ast.full_name AS officer_name, d.dept_name AS officer_dept,
                      i.ai_summary, i.ai_department, i.ai_risk, i.ai_detected_language,
                      i.ai_duplicate_signal, i.ai_similar_issue_code, i.ai_confidence, i.ai_model, i.ai_needs_review,
                      r.resolved_at
               FROM issues i
               LEFT JOIN wards w ON i.ward_id = w.ward_id
               LEFT JOIN issue_categories cat ON i.category_id = cat.category_id
               LEFT JOIN staff ast ON i.assigned_staff_id = ast.staff_id
               LEFT JOIN departments d ON ast.dept_id = d.dept_id
               LEFT JOIN v_issue_resolved_at r ON r.issue_id = i.issue_id
               WHERE i.citizen_id = %s
               ORDER BY i.created_at DESC""",
            (g.citizen["citizen_id"],),
        )
        issues = cur.fetchall()
    return jsonify(issues)


@app.get("/api/issues/feed")
@require_citizen
def citizen_feed_issues():
    """
    City-wide feed for the citizen app's Community/Home/Map tabs — every
    issue from every citizen (not just the logged-in one), most recent
    first. This is distinct from GET /api/issues, which is scoped to only
    the logged-in citizen's own reports for the "My Reports" tab.
    """
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """SELECT i.issue_id AS id, i.issue_code, i.title, i.description AS desc_text,
                      i.severity AS sev, i.status, i.latitude AS lat, i.longitude AS lng,
                      i.location_text, i.photo_url, i.created_at, i.citizen_id,
                      w.ward_name AS ward, cat.category_id AS cat, cat.category_name AS cat_label,
                      c.display_name AS reporter_name, i.ai_summary, i.ai_department, i.ai_risk,
                      i.ai_model
               FROM issues i
               LEFT JOIN wards w ON i.ward_id = w.ward_id
               LEFT JOIN issue_categories cat ON i.category_id = cat.category_id
               LEFT JOIN citizens c ON i.citizen_id = c.citizen_id
               ORDER BY i.created_at DESC
               LIMIT 300""",
        )
        issues = cur.fetchall()
    return jsonify(issues)


@app.post("/api/issues/<int:issue_id>/verify")
@require_citizen
def citizen_verify_issue(issue_id):
    """
    The reporting citizen confirms a Resolved fix ("Yes, fixed"), moving the
    issue to the final 'Verified' status. Only the citizen who filed the
    issue can do this, and only while it is Resolved.
    """
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT citizen_id, status FROM issues WHERE issue_id = %s", (issue_id,))
        issue = cur.fetchone()
        if not issue:
            return jsonify({"error": "Issue not found"}), 404
        if issue["citizen_id"] != g.citizen["citizen_id"]:
            return jsonify({"error": "You can only verify your own reports"}), 403
        if issue["status"] == "Verified":
            return jsonify({"success": True, "status": "Verified"})
        if issue["status"] != "Resolved":
            return jsonify({"error": "Only a Resolved issue can be verified"}), 400
        cur.execute("UPDATE issues SET status = 'Verified' WHERE issue_id = %s", (issue_id,))
        cur.execute("INSERT INTO issue_status_history (issue_id, status) VALUES (%s, 'Verified')", (issue_id,))
    return jsonify({"success": True, "status": "Verified"})


@app.get("/api/issues/<int:issue_id>/history")
def issue_history(issue_id):
    """
    Status-history timeline for one issue. Available to:
      - the citizen who filed it (via the citizen session cookie), or
      - any authenticated staff member (via the staff session cookie).
    Neither require_citizen nor require_staff fits alone since either
    session type is acceptable here, so both cookies are checked manually
    and the issue's owner is looked up to authorize the citizen case.
    """
    db = get_db()

    citizen_session_id = request.cookies.get(CITIZEN_COOKIE)
    staff_session_id = request.cookies.get(STAFF_COOKIE)
    authorized = False

    with db.cursor() as cur:
        cur.execute("SELECT issue_id, citizen_id FROM issues WHERE issue_id = %s", (issue_id,))
        issue = cur.fetchone()
        if not issue:
            return jsonify({"error": "Issue not found"}), 404

        if citizen_session_id:
            cur.execute(
                """SELECT c.citizen_id FROM sessions s JOIN citizens c ON c.citizen_id = s.citizen_id
                   WHERE s.session_id = %s AND s.actor_type = 'citizen' AND s.expires_at > NOW()""",
                (citizen_session_id,),
            )
            citizen = cur.fetchone()
            if citizen and citizen["citizen_id"] == issue["citizen_id"]:
                authorized = True

        if not authorized and staff_session_id:
            cur.execute(
                """SELECT s.staff_id FROM sessions ses JOIN staff s ON s.staff_id = ses.staff_id
                   WHERE ses.session_id = %s AND ses.actor_type = 'staff' AND ses.expires_at > NOW()""",
                (staff_session_id,),
            )
            if cur.fetchone():
                authorized = True

        if not authorized:
            return jsonify({"error": "Not authorized to view this issue"}), 403

        cur.execute(
            """SELECT id, status, changed_at FROM issue_status_history
               WHERE issue_id = %s ORDER BY changed_at ASC, id ASC""",
            (issue_id,),
        )
        history = cur.fetchall()

    return jsonify(history)


@app.get("/api/staff/dashboard-kpis")
@require_staff()
def staff_dashboard_kpis():
    db = get_db()
    with db.cursor() as cur:
        # City-wide totals by stage, plus overall SLA performance across
        # all resolved issues (join wards for each issue's sla_hours).
        cur.execute("""
            SELECT
                COUNT(i.issue_id) AS total_issues,
                SUM(CASE WHEN i.status = 'Reported' THEN 1 ELSE 0 END) AS reported,
                SUM(CASE WHEN i.status = 'Acknowledged' THEN 1 ELSE 0 END) AS acknowledged,
                SUM(CASE WHEN i.status = 'InProgress' THEN 1 ELSE 0 END) AS in_progress,
                SUM(CASE WHEN i.status IN ('Resolved', 'Verified') THEN 1 ELSE 0 END) AS resolved,
                SUM(CASE WHEN i.status = 'Verified' THEN 1 ELSE 0 END) AS verified,
                ROUND(
                    100 * SUM(CASE WHEN i.status IN ('Resolved', 'Verified') AND r.resolved_at IS NOT NULL
                                         AND TIMESTAMPDIFF(HOUR, i.created_at, r.resolved_at) <= w.sla_hours
                                    THEN 1 ELSE 0 END)
                    / NULLIF(SUM(CASE WHEN i.status IN ('Resolved', 'Verified') THEN 1 ELSE 0 END), 0),
                1) AS sla_performance_pct
            FROM issues i
            LEFT JOIN wards w ON i.ward_id = w.ward_id
            LEFT JOIN v_issue_resolved_at r ON r.issue_id = i.issue_id
        """)
        totals = cur.fetchone()

        cur.execute("SELECT * FROM v_ward_stats")
        ward_stats = cur.fetchall()
        cur.execute("SELECT * FROM v_department_stats")
        dept_stats = cur.fetchall()

    return jsonify({"totals": totals, "wards": ward_stats, "departments": dept_stats})


# Valid values for issues.status — mirrors the STAGES array in
# terrasync-admin-app.html so the API rejects anything the frontend
# (or a stray client) wouldn't recognize.
VALID_ISSUE_STATUSES = {"Reported", "Acknowledged", "InProgress", "Resolved", "Verified"}
# "Verified" is the final stage: the fix has been confirmed (by the citizen
# who reported it, or by staff). It counts as closed everywhere "Resolved"
# does, and can only be reached from "Resolved".
CLOSED_STATUSES = ("Resolved", "Verified")


@app.get("/api/staff/issues")
@require_staff()
def staff_get_issues():
    db = get_db()
    with db.cursor() as cur:
        cur.execute("""
            SELECT i.issue_id AS id, i.issue_code, i.title, i.description AS desc_text,
                   i.severity AS sev, i.status, i.latitude AS lat, i.longitude AS lng,
                   i.photo_url, i.created_at, i.assigned_staff_id, c.display_name AS citizen_name,
                   w.ward_name AS ward, cat.category_id AS cat, cat.category_name AS cat_label,
                   ast.full_name AS officer_name, i.ai_summary, i.ai_department, i.ai_risk,
                   i.ai_detected_language, i.ai_duplicate_signal, i.ai_similar_issue_code,
                   i.ai_duplicate_reason, i.ai_confidence, i.ai_model, i.ai_needs_review, i.ai_status
            FROM issues i
            LEFT JOIN citizens c ON i.citizen_id = c.citizen_id
            LEFT JOIN wards w ON i.ward_id = w.ward_id
            LEFT JOIN issue_categories cat ON i.category_id = cat.category_id
            LEFT JOIN staff ast ON i.assigned_staff_id = ast.staff_id
            ORDER BY i.created_at DESC
        """)
        issues = cur.fetchall()
    return jsonify(issues)


@app.patch("/api/staff/issues/<int:issue_id>")
@require_staff()
def staff_update_issue(issue_id):
    data = request.get_json(force=True)
    status = data.get("status")
    # Distinguish "field omitted" (leave assignment untouched) from
    # "field explicitly sent as null" (unassign). data.get(...) alone
    # can't tell these apart since both return None — use key presence
    # instead so the admin's "Unassigned" option actually clears it.
    has_assignment_update = "assigned_staff_id" in data
    assigned_staff_id = data.get("assigned_staff_id")

    if status is not None and status not in VALID_ISSUE_STATUSES:
        return jsonify({"error": "Invalid status"}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """SELECT i.issue_id, i.status, i.assigned_staff_id, ast.dept_id AS assigned_dept_id
               FROM issues i LEFT JOIN staff ast ON i.assigned_staff_id = ast.staff_id
               WHERE i.issue_id = %s""",
            (issue_id,),
        )
        issue = cur.fetchone()
        if not issue:
            return jsonify({"error": "Issue not found"}), 404

        # Scope what each role is allowed to touch, per the role model in
        # the README (field_officer / dept_head / super_admin). Without
        # this, @require_staff() alone let any authenticated staff member
        # edit or reassign any issue in the city.
        role = g.staff["role"]

        if role == "field_officer":
            # Can only update the status of issues assigned to themself,
            # and can never reassign an issue to someone else — including
            # unassigning it (assigned_staff_id: null is still a change
            # away from "assigned to me", so it's covered by the != check).
            if issue["assigned_staff_id"] != g.staff["staff_id"]:
                return jsonify({"error": "You can only update issues assigned to you"}), 403
            if has_assignment_update and assigned_staff_id != g.staff["staff_id"]:
                return jsonify({"error": "Field officers cannot reassign issues"}), 403

        elif role == "dept_head":
            # Can act on issues already assigned within their own
            # department, or unassigned issues (e.g. to claim/assign them).
            if issue["assigned_staff_id"] is not None and issue["assigned_dept_id"] != g.staff["dept_id"]:
                return jsonify({"error": "This issue belongs to another department"}), 403
            # Can only hand it off to an officer inside their own department.
            # (assigned_staff_id: null just unassigns — nothing to validate.)
            if has_assignment_update and assigned_staff_id is not None:
                cur.execute("SELECT dept_id FROM staff WHERE staff_id = %s", (assigned_staff_id,))
                target = cur.fetchone()
                if not target or target["dept_id"] != g.staff["dept_id"]:
                    return jsonify({"error": "Can only assign officers within your department"}), 403

        # super_admin: no additional restriction.

        if status == "Verified" and issue["status"] not in ("Resolved", "Verified"):
            return jsonify({"error": "Only a Resolved issue can be Verified"}), 400

        if status:
            cur.execute("UPDATE issues SET status = %s WHERE issue_id = %s", (status, issue_id))
            cur.execute("INSERT INTO issue_status_history (issue_id, status) VALUES (%s, %s)", (issue_id, status))
        if has_assignment_update:
            # assigned_staff_id may legitimately be None here — that's the
            # "Unassigned" case, and NULL is a valid value for the column.
            cur.execute("UPDATE issues SET assigned_staff_id = %s WHERE issue_id = %s", (assigned_staff_id, issue_id))

        # Texts to the reporter. Only on real changes, never for a move back
        # to "Reported", and the reporter's own verification isn't texted back.
        if status and status != issue["status"] and status != "Reported":
            notify_citizen(cur, issue_id, "status", status)
        if has_assignment_update and assigned_staff_id is not None \
                and assigned_staff_id != issue["assigned_staff_id"]:
            cur.execute(
                """SELECT d.dept_name FROM staff s
                   LEFT JOIN departments d ON d.dept_id = s.dept_id WHERE s.staff_id = %s""",
                (assigned_staff_id,),
            )
            dept_row = cur.fetchone()
            notify_citizen(cur, issue_id, "assigned", dept_row["dept_name"] if dept_row else None)

    return jsonify({"success": True})


@app.get("/api/staff/sms-log")
@require_staff(roles=["super_admin", "dept_head"])
def staff_sms_log():
    """Latest SMS notifications (phone numbers masked) so staff can confirm
    citizens were actually told about each update."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """SELECT l.log_id, i.issue_code, l.event, l.phone, l.message, l.status, l.error, l.created_at
               FROM sms_log l JOIN issues i ON i.issue_id = l.issue_id
               ORDER BY l.log_id DESC LIMIT 100"""
        )
        rows = cur.fetchall()
    for r in rows:
        ph = r["phone"] or ""
        r["phone"] = (ph[:6] + "****" + ph[-2:]) if len(ph) > 8 else "****"
    return jsonify(rows)


# ---------------------------------------------------------------------------
# Field officers (Teams tab)
#
# ASSUMED SCHEMA — adjust table/column names below to match schema.sql:
#   departments(dept_id, dept_name)          -- staff.dept_id -> departments.dept_id
#   staff.duty_status ENUM('available','onsite','offduty')
#     If you don't have a duty_status column yet, either add one or replace
#     the COALESCE(s.duty_status, 'available') expression with a fixed value
#     / a different source of truth (e.g. a live location/heartbeat table).
#   issues.assigned_staff_id
#     "Resolved" count and avg resolution hours are computed from issues
#     assigned to each officer. issues has no updated_at column, so
#     resolution time is derived from issue_status_history instead
#     (MAX(changed_at) WHERE status='Resolved'), via the v_issue_resolved_at
#     view defined in schema.sql.
# ---------------------------------------------------------------------------
@app.get("/api/staff/officers")
@require_staff()
def staff_get_officers():
    db = get_db()
    with db.cursor() as cur:
        cur.execute("""
            SELECT s.staff_id AS id, s.full_name AS name, s.role,
                   d.dept_name AS dept, w.ward_name AS ward,
                   COALESCE(s.duty_status, 'available') AS status,
                   COALESCE(res.resolved_count, 0) AS resolved,
                   COALESCE(ROUND(res.avg_hrs), 0) AS avgHrs
            FROM staff s
            LEFT JOIN departments d ON s.dept_id = d.dept_id
            LEFT JOIN wards w ON s.ward_id = w.ward_id
            LEFT JOIN (
                SELECT i.assigned_staff_id,
                       COUNT(*) AS resolved_count,
                       AVG(TIMESTAMPDIFF(HOUR, i.created_at, r.resolved_at)) AS avg_hrs
                FROM issues i
                JOIN v_issue_resolved_at r ON r.issue_id = i.issue_id
                WHERE i.status IN ('Resolved', 'Verified') AND i.assigned_staff_id IS NOT NULL
                GROUP BY i.assigned_staff_id
            ) res ON res.assigned_staff_id = s.staff_id
            WHERE s.is_active = TRUE
            ORDER BY s.full_name
        """)
        officers = cur.fetchall()
    return jsonify(officers)


# ---------------------------------------------------------------------------
# Serve the HTML prototypes as static files
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "terrasync-opening.html")


@app.get("/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


@app.cli.command("cleanup-otps")
def cleanup_otps_cli():
    """Delete expired OTP rows. Also run opportunistically on every
    send-otp request, but wiring this into a nightly cron job (see
    README) keeps the tables tidy even on a quiet server.

    Usage: flask --app app cleanup-otps
    """
    db = get_db()
    with db.cursor() as cur:
        cleanup_expired_otps(cur)
    print("Expired citizen_otp_codes and staff_otp_codes rows removed.")


if __name__ == "__main__":
    app.run(debug=True)

from flask import Flask, request, jsonify
from flask_cors import CORS
import hashlib
import hmac
import requests
import random
import os
import json
import sqlite3

app = Flask(__name__)
CORS(app)

# ============================
# CONFIGURATION
# ============================
PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "sk_test_YOUR_KEY_HERE")
PAYSTACK_WEBHOOK_SECRET = os.environ.get("PAYSTACK_WEBHOOK_SECRET", "your_webhook_secret")
PAYSTACK_PUBLIC_KEY = os.environ.get("PAYSTACK_PUBLIC_KEY", "pk_test_YOUR_KEY_HERE")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "admin123")
EXPECTED_AMOUNT = 80000
DB_PATH = os.environ.get("DB_PATH", "myschool_yearly_master.db")

# ============================
# PERSISTENT FILE STORAGE
# ============================
CODES_FILE = "codes.json"

def load_codes():
    if os.path.exists(CODES_FILE):
        try:
            with open(CODES_FILE, 'r') as f:
                data = json.load(f)
                return data.get("codes", {}), data.get("emails", {})
        except:
            return {}, {}
    return {}, {}

def save_codes(codes, emails):
    try:
        with open(CODES_FILE, 'w') as f:
            json.dump({"codes": codes, "emails": emails}, f)
    except Exception as e:
        print(f"⚠️ Could not save codes: {e}")

activation_codes, paid_emails = load_codes()
print(f"📦 Loaded {len(activation_codes)} existing codes from storage.")

# ============================
# DATABASE CONNECTION
# ============================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ============================
# GENERATE CODE
# ============================
def generate_code():
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(random.choices(chars, k=8))
        if code not in activation_codes:
            return code

# ============================
# ROUTE: Health Check
# ============================
@app.route("/")
def home():
    return jsonify({
        "status": "Get Prepared backend is running ✅",
        "total_codes": len(activation_codes)
    })

# ============================
# ROUTE: Config
# ============================
@app.route("/config")
def config():
    return jsonify({"public_key": PAYSTACK_PUBLIC_KEY})

# ============================
# ROUTE: Get All Subjects
# ============================
@app.route("/subjects")
def get_subjects():
    exam_type = request.args.get("exam", "WAEC")
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT subject FROM exam_vault
            WHERE exam_type = ?
            UNION
            SELECT DISTINCT subject FROM theory_vault
            WHERE exam_type = ?
            ORDER BY subject
        ''', (exam_type, exam_type))
        subjects = [row[0] for row in cursor.fetchall()]
        conn.close()
        return jsonify({"exam": exam_type, "subjects": subjects})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================
# ROUTE: Get Years for Subject
# ============================
@app.route("/years")
def get_years():
    exam_type = request.args.get("exam", "WAEC")
    subject = request.args.get("subject", "")
    if not subject:
        return jsonify({"error": "subject required"}), 400
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT year FROM exam_vault
            WHERE exam_type = ? AND subject = ?
            UNION
            SELECT DISTINCT year FROM theory_vault
            WHERE exam_type = ? AND subject = ?
            ORDER BY year DESC
        ''', (exam_type, subject, exam_type, subject))
        years = [str(row[0]) for row in cursor.fetchall()]
        conn.close()
        return jsonify({"exam": exam_type, "subject": subject, "years": years})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================
# ROUTE: Get Questions
# ============================
@app.route("/questions")
def get_questions():
    exam_type = request.args.get("exam", "WAEC")
    subject = request.args.get("subject", "")
    year = request.args.get("year", "")
    q_type = request.args.get("type", "all")

    if not subject:
        return jsonify({"error": "subject required"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        questions = []

        if q_type in ("all", "objective"):
            query = '''
                SELECT id, question_text, option_a, option_b,
                       option_c, option_d, correct_option
                FROM exam_vault
                WHERE exam_type = ? AND subject = ?
            '''
            params = [exam_type, subject]
            if year:
                query += " AND year = ?"
                params.append(year)
            query += " ORDER BY id"
            cursor.execute(query, params)
            for i, row in enumerate(cursor.fetchall(), 1):
                questions.append({
                    "id": f"{exam_type}_{subject}_{year}_O{i}",
                    "type": "objective",
                    "number": i,
                    "text": (row[1] or "").strip(),
                    "options": {
                        "A": (row[2] or "").strip(),
                        "B": (row[3] or "").strip(),
                        "C": (row[4] or "").strip(),
                        "D": (row[5] or "").strip(),
                    },
                    "answer": (row[6] or "").strip(),
                    "topic": "Objectives",
                    "subject": subject,
                    "exam": exam_type,
                    "year": year
                })

        if q_type in ("all", "theory"):
            query = '''
                SELECT id, question_text, solution_text, content_type
                FROM theory_vault
                WHERE exam_type = ? AND subject = ?
            '''
            params = [exam_type, subject]
            if year:
                query += " AND year = ?"
                params.append(year)
            query += " ORDER BY id"
            cursor.execute(query, params)
            for i, row in enumerate(cursor.fetchall(), 1):
                questions.append({
                    "id": f"{exam_type}_{subject}_{year}_T{i}",
                    "type": "theory",
                    "number": i,
                    "text": (row[1] or "").strip(),
                    "solution": (row[2] or "").strip(),
                    "topic": "Theory",
                    "subject": subject,
                    "exam": exam_type,
                    "year": year
                })

        conn.close()
        return jsonify({
            "exam": exam_type,
            "subject": subject,
            "year": year,
            "count": len(questions),
            "questions": questions
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================
# ROUTE: Verify Payment (GET)
# ============================
@app.route("/verify-payment")
def verify_payment_get():
    reference = request.args.get("reference")
    if not reference:
        return jsonify({"success": False, "message": "No reference provided"}), 400
    return process_payment(reference)

# ============================
# ROUTE: Verify Payment (POST)
# ============================
@app.route("/verify-payment", methods=["POST"])
def verify_payment_post():
    body = request.get_json() or {}
    reference = body.get("reference")
    if not reference:
        return jsonify({"success": False, "message": "No reference provided"}), 400
    return process_payment(reference)

def process_payment(reference):
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.get(
            f"https://api.paystack.co/transaction/verify/{reference}",
            headers=headers
        )
        data = response.json()
    except Exception as e:
        return jsonify({"success": False, "message": "Could not reach Paystack"}), 500

    if not data.get("status") or data["data"]["status"] != "success":
        return jsonify({"success": False, "message": "Payment not successful"}), 400

    if data["data"]["amount"] < EXPECTED_AMOUNT:
        return jsonify({"success": False, "message": "Incorrect payment amount"}), 400

    email = data["data"]["customer"]["email"]

    # Return existing code if already paid
    if email in paid_emails:
        code = paid_emails[email]
        # Reset used status so student can re-activate on new device
        if code in activation_codes:
            activation_codes[code]["used"] = False
            save_codes(activation_codes, paid_emails)
        return jsonify({"success": True, "code": code, "email": email})

    code = generate_code()
    activation_codes[code] = {"used": False, "email": email}
    paid_emails[email] = code
    save_codes(activation_codes, paid_emails)

    print(f"✅ New activation: {email} → {code}")
    return jsonify({"success": True, "code": code, "email": email})

# ============================
# ROUTE: Validate Code
# Single use but re-usable via payment verification
# ============================
@app.route("/validate-code", methods=["POST"])
def validate_code():
    body = request.get_json()
    if not body:
        return jsonify({"valid": False, "message": "No data provided"}), 400
    code = body.get("code", "").strip().upper()
    if not code:
        return jsonify({"valid": False, "message": "No code provided"}), 400
    if code in activation_codes:
        if activation_codes[code].get("used"):
            return jsonify({
                "valid": False,
                "message": "Code already used! If you paid, verify your payment to get your code again."
            }), 400
        activation_codes[code]["used"] = True
        save_codes(activation_codes, paid_emails)
        return jsonify({"valid": True, "message": "Access granted!"})
    return jsonify({"valid": False, "message": "Invalid code"}), 400

# ============================
# ROUTE: Manual Code Generator
# ============================
@app.route("/generate-code")
def generate_code_manual():
    secret = request.args.get("secret", "")
    if secret != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    email = request.args.get("email", "manual@getprepared.com")
    if email in paid_emails:
        code = paid_emails[email]
        if code in activation_codes:
            activation_codes[code]["used"] = False
            save_codes(activation_codes, paid_emails)
        return jsonify({"code": code, "email": email})
    code = generate_code()
    activation_codes[code] = {"used": False, "email": email}
    paid_emails[email] = code
    save_codes(activation_codes, paid_emails)
    return jsonify({"code": code, "email": email})

# ============================
# ROUTE: Admin Stats
# ============================
@app.route("/api/admin/stats")
def admin_stats():
    secret = request.args.get("secret", "")
    if secret != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM exam_vault")
        obj_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM theory_vault")
        theory_count = cursor.fetchone()[0]
        conn.close()
    except:
        obj_count = 0
        theory_count = 0
    return jsonify({
        "total_codes": len(activation_codes),
        "total_emails": len(paid_emails),
        "objective_questions": obj_count,
        "theory_questions": theory_count,
        "codes": activation_codes,
        "emails": paid_emails
    })

# ============================
# ROUTE: Admin Issue Code
# ============================
@app.route("/api/admin/issue-code", methods=["POST"])
def admin_issue_code():
    body = request.get_json()
    secret = body.get("secret", "")
    if secret != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    email = body.get("email", "manual@getprepared.com")
    if email in paid_emails:
        code = paid_emails[email]
        if code in activation_codes:
            activation_codes[code]["used"] = False
            save_codes(activation_codes, paid_emails)
        return jsonify({"code": code, "email": email})
    code = generate_code()
    activation_codes[code] = {"used": False, "email": email}
    paid_emails[email] = code
    save_codes(activation_codes, paid_emails)
    return jsonify({"code": code, "email": email})

# ============================
# ROUTE: Admin Mark Paid
# ============================
@app.route("/api/admin/mark-paid", methods=["POST"])
def admin_mark_paid():
    body = request.get_json()
    secret = body.get("secret", "")
    if secret != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    email = body.get("email", "")
    if not email:
        return jsonify({"error": "email required"}), 400
    if email in paid_emails:
        code = paid_emails[email]
        if code in activation_codes:
            activation_codes[code]["used"] = False
            save_codes(activation_codes, paid_emails)
        return jsonify({"code": code, "email": email, "note": "existing"})
    code = generate_code()
    activation_codes[code] = {"used": False, "email": email}
    paid_emails[email] = code
    save_codes(activation_codes, paid_emails)
    return jsonify({"code": code, "email": email, "note": "new"})

# ============================
# ROUTE: Admin Set Price
# ============================
@app.route("/api/admin/set-price", methods=["POST"])
def admin_set_price():
    body = request.get_json()
    secret = body.get("secret", "")
    if secret != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"message": "Price noted"})

# ============================
# ROUTE: Payment Status
# ============================
@app.route("/api/payment/status/<reference>")
def payment_status(reference):
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.get(
            f"https://api.paystack.co/transaction/verify/{reference}",
            headers=headers
        )
        data = response.json()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================
# ROUTE: Admin Codes List
# ============================
@app.route("/admin/codes")
def list_codes():
    secret = request.args.get("secret", "")
    if secret != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({
        "total_codes": len(activation_codes),
        "codes": activation_codes,
        "emails": paid_emails
    })

# ============================
# ROUTE: Paystack Webhook
# ============================
@app.route("/webhook/paystack", methods=["POST"])
def paystack_webhook():
    payload = request.get_data()
    signature = request.headers.get("x-paystack-signature", "")
    expected = hmac.new(
        PAYSTACK_WEBHOOK_SECRET.encode("utf-8"),
        payload,
        hashlib.sha512
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return "Unauthorized", 401
    event = request.get_json()
    if event.get("event") == "charge.success":
        email = event["data"]["customer"]["email"]
        amount = event["data"]["amount"]
        print(f"💰 Webhook: {email} paid ₦{amount // 100}")
    return "OK", 200

# ============================
# START
# ============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)

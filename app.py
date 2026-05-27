from flask import Flask, request, jsonify
from flask_cors import CORS
import hashlib
import hmac
import requests
import random
import os
import json

app = Flask(__name__)
CORS(app)

# ============================
# CONFIGURATION
# ============================
PAYSTACK_SECRET_KEY = os.environ.get("sk_live_5c757b451f0a616b7f0f462b54feb0d9a116d090", "sk_test_YOUR_KEY_HERE")
PAYSTACK_WEBHOOK_SECRET = os.environ.get("sk_live_5c757b451f0a616b7f0f462b54feb0d9a116d090", "your_webhook_secret")
PAYSTACK_PUBLIC_KEY = os.environ.get("pk_live_10facb7256c431e6120390bc7c6a18a7cca7663f", "pk_test_YOUR_KEY_HERE")
ADMIN_SECRET = os.environ.get("getprepared2024admin", "admin123")
EXPECTED_AMOUNT = 80000

# ============================
# PERSISTENT FILE STORAGE
# Survives Railway restarts
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
# GENERATE CODE
# 8 random characters
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
# ROUTE: Config (frontend fetches public key)
# ============================
@app.route("/config")
def config():
    return jsonify({"public_key": PAYSTACK_PUBLIC_KEY})

# ============================
# ROUTE: Verify Payment
# Called from frontend after Paystack payment
# ============================
@app.route("/verify-payment")
def verify_payment():
    reference = request.args.get("reference")
    if not reference:
        return jsonify({"success": False, "message": "No reference provided"}), 400

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
        return jsonify({
            "success": True,
            "code": code,
            "email": email,
            "message": "Payment verified!"
        })

    # Generate new code
    code = generate_code()
    activation_codes[code] = {"used": False, "email": email}
    paid_emails[email] = code
    save_codes(activation_codes, paid_emails)

    print(f"✅ New activation: {email} → {code}")

    return jsonify({
        "success": True,
        "code": code,
        "email": email,
        "message": "Payment verified! Here is your activation code."
    })

# ============================
# ROUTE: Validate Code
# Called from app.js when student enters code
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
        activation_codes[code]["used"] = True
        save_codes(activation_codes, paid_emails)
        print(f"✅ Code validated: {code}")
        return jsonify({
            "valid": True,
            "message": "Code is valid! Access granted.",
            "email": activation_codes[code].get("email", "")
        })

    print(f"❌ Invalid code attempt: {code}")
    return jsonify({"valid": False, "message": "Invalid code. Please check and try again."}), 400

# ============================
# ROUTE: Manual Code Generator
# For students who pay via transfer
# Usage: /generate-code?secret=YOUR_ADMIN_SECRET&email=student@gmail.com
# ============================
@app.route("/generate-code")
def generate_code_manual():
    secret = request.args.get("secret", "")
    if secret != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    email = request.args.get("email", "manual@getprepared.com")

    # Return existing code if email already has one
    if email in paid_emails:
        return jsonify({
            "code": paid_emails[email],
            "email": email,
            "note": "Existing code returned"
        })

    code = generate_code()
    activation_codes[code] = {"used": False, "email": email}
    paid_emails[email] = code
    save_codes(activation_codes, paid_emails)

    print(f"🔑 Manual code generated: {email} → {code}")

    return jsonify({
        "code": code,
        "email": email,
        "note": "New code generated"
    })

# ============================
# ROUTE: List All Codes (Admin)
# Usage: /admin/codes?secret=YOUR_ADMIN_SECRET
# ============================
@app.route("/admin/codes")
def list_codes():
    secret = request.args.get("secret", "")
    if secret != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    return jsonify({
        "total_codes": len(activation_codes),
        "total_emails": len(paid_emails),
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
        ref = event["data"]["reference"]
        print(f"💰 Webhook: {email} paid ₦{amount // 100} — Ref: {ref}")

    return "OK", 200

# ============================
# START
# ============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)

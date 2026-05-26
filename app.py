from flask import Flask, request, jsonify
from flask_cors import CORS
import hashlib
import hmac
import requests
import random
import os

app = Flask(__name__)
CORS(app)

PAYSTACK_SECRET_KEY = os.environ.get("sk_live_5c757b451f0a616b7f0f462b54feb0d9a116d090", "sk_test_YOUR_KEY_HERE")
PAYSTACK_WEBHOOK_SECRET = os.environ.get("getprepared2024admin", "your_webhook_secret")
PAYSTACK_PUBLIC_KEY = os.environ.get("pk_live_10facb7256c431e6120390bc7c6a18a7cca7663f", "pk_test_YOUR_KEY_HERE")
EXPECTED_AMOUNT = 80000

activation_codes = {}
paid_emails = {}

def generate_code():
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(random.choices(chars, k=8))
        if code not in activation_codes:
            return code

@app.route("/")
def home():
    return jsonify({"status": "Get Prepared backend is running ✅"})

@app.route("/config")
def config():
    return jsonify({"public_key": PAYSTACK_PUBLIC_KEY})

@app.route("/verify-payment")
def verify_payment():
    reference = request.args.get("reference")
    if not reference:
        return jsonify({"success": False, "message": "No reference provided"}), 400

    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    response = requests.get(
        f"https://api.paystack.co/transaction/verify/{reference}",
        headers=headers
    )
    data = response.json()

    if not data.get("status") or data["data"]["status"] != "success":
        return jsonify({"success": False, "message": "Payment not successful"}), 400

    if data["data"]["amount"] < EXPECTED_AMOUNT:
        return jsonify({"success": False, "message": "Incorrect payment amount"}), 400

    email = data["data"]["customer"]["email"]

    if email in paid_emails:
        code = paid_emails[email]
        return jsonify({"success": True, "code": code, "email": email})

    code = generate_code()
    activation_codes[code] = {"used": False, "email": email}
    paid_emails[email] = code

    return jsonify({"success": True, "code": code, "email": email})

@app.route("/validate-code", methods=["POST"])
def validate_code():
    body = request.get_json()
    code = body.get("code", "").strip().upper()
    if not code:
        return jsonify({"valid": False, "message": "No code provided"}), 400

    if code in activation_codes:
        activation_codes[code]["used"] = True
        return jsonify({"valid": True, "message": "Access granted!"})

    return jsonify({"valid": False, "message": "Invalid code"}), 400

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
        print(f"Payment received: {email}")

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)

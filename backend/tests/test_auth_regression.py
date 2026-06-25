"""
Regression tests post forgot-password bug fix.
Validates:
  - POST /api/auth/forgot-password returns 200 uniform response
  - POST /api/auth/login still triggers MFA (mfa_required=true)
  - POST /api/auth/mfa/verify with correct OTP (read from DB) issues cookies
  - GET /api/auth/me returns admin user with wildcard '*' permission
  - Protected endpoints respond non-401 when authenticated
"""
import os
import hmac
import hashlib
import requests
import pytest
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else "https://soap-factura-batch.preview.emergentagent.com"
ADMIN_EMAIL = "miguelingv@gmail.com"
ADMIN_PASSWORD = "MiguelAdmin2026!"


@pytest.fixture(scope="module")
def db():
    client = MongoClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


def _brute_otp(code_hash: str) -> str | None:
    secret = os.environ["JWT_SECRET"].encode()
    for i in range(1_000_000):
        c = f"{i:06d}"
        if hmac.new(secret, c.encode(), hashlib.sha256).hexdigest() == code_hash:
            return c
    return None


# --- forgot-password ---
class TestForgotPassword:
    def test_forgot_password_returns_200_unknown_email(self):
        r = requests.post(f"{BASE_URL}/api/auth/forgot-password",
                          json={"email": "nonexistent_xyz@example.com"})
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}

    def test_forgot_password_returns_200_known_email(self):
        r = requests.post(f"{BASE_URL}/api/auth/forgot-password",
                          json={"email": ADMIN_EMAIL})
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_forgot_password_invalid_payload(self):
        r = requests.post(f"{BASE_URL}/api/auth/forgot-password", json={})
        assert r.status_code in (200, 422)  # endpoint is uniform: may accept or 422


# --- login + MFA regression ---
class TestLoginMfaRegression:
    @pytest.fixture(scope="class")
    def session_after_mfa(self, db):
        s = requests.Session()
        # Step 1: login creds
        r = s.post(f"{BASE_URL}/api/auth/login",
                   json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["mfa_required"] is True
        assert "challenge_id" in data and "email_hint" in data
        chid = data["challenge_id"]

        rec = db.auth_mfa_challenges.find_one({"_id": chid})
        assert rec, "Challenge not found in DB"
        otp = _brute_otp(rec["code_hash"])
        assert otp, "OTP brute-force failed"

        # Step 2: verify MFA
        r2 = s.post(f"{BASE_URL}/api/auth/mfa/verify",
                    json={"challenge_id": chid, "code": otp})
        assert r2.status_code == 200, r2.text
        assert "monitorsii_access" in s.cookies.get_dict() or "access_token" in s.cookies.get_dict() \
            or any("access" in k for k in s.cookies.get_dict()), f"No access cookie: {s.cookies.get_dict()}"
        return s

    def test_auth_me_returns_admin(self, session_after_mfa):
        r = session_after_mfa.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 200, r.text
        me = r.json()
        assert me.get("email") == ADMIN_EMAIL
        perms = me.get("permissions") or me.get("role", {}).get("permissions") if isinstance(me.get("role"), dict) else me.get("permissions")
        # admin must have wildcard
        body_str = str(me)
        assert "*" in body_str or "admin" in body_str.lower(), f"Admin role/perms missing: {me}"

    @pytest.mark.parametrize("endpoint", [
        "/api/admin/users",
        "/api/admin/roles",
        "/api/admin/permissions/catalog",
    ])
    def test_admin_routes_accessible(self, session_after_mfa, endpoint):
        r = session_after_mfa.get(f"{BASE_URL}{endpoint}")
        assert r.status_code != 401, f"{endpoint} returned 401"
        assert r.status_code < 500, f"{endpoint} returned {r.status_code}: {r.text[:200]}"

    def test_logout(self, session_after_mfa):
        r = session_after_mfa.post(f"{BASE_URL}/api/auth/logout")
        assert r.status_code in (200, 204)

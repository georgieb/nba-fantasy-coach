import time

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from app import auth, config, crypto

URL = "https://proj.supabase.co"


def claims(**over):
    now = int(time.time())
    return {"sub": "11111111-1111-1111-1111-111111111111", "email": "a@b.co", "role": "authenticated",
            "aud": "authenticated", "iss": f"{URL}/auth/v1", "iat": now, "exp": now + 3600, **over}


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", URL)
    monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", "x" * 40)
    monkeypatch.setattr(auth, "_jwks", None)


def test_hs256_valid():
    tok = jwt.encode(claims(), "x" * 40, algorithm="HS256")
    ident = auth.identity(authorization=f"Bearer {tok}", x_session_id=None)
    assert ident.is_user and ident.key == "user:11111111-1111-1111-1111-111111111111" and ident.email == "a@b.co"


@pytest.mark.parametrize("bad", [
    {"exp": int(time.time()) - 120},         # expired (beyond the clock-skew leeway)
    {"aud": "anon"},                          # wrong audience
    {"iss": "https://evil.co/auth/v1"},       # wrong issuer
    {"role": "anon"},                         # not a signed-in user
])
def test_hs256_rejected(bad):
    tok = jwt.encode(claims(**bad), "x" * 40, algorithm="HS256")
    with pytest.raises(HTTPException) as e:
        auth.identity(authorization=f"Bearer {tok}", x_session_id=None)
    assert e.value.status_code == 401


def test_wrong_secret_rejected():
    tok = jwt.encode(claims(), "y" * 40, algorithm="HS256")
    with pytest.raises(HTTPException):
        auth.identity(authorization=f"Bearer {tok}", x_session_id=None)


def test_es256_via_jwks(monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())

    class FakeJWKS:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": key.public_key()})()

    monkeypatch.setattr(auth, "_jwks", FakeJWKS())
    tok = jwt.encode(claims(), key, algorithm="ES256", headers={"kid": "k1"})
    assert auth.identity(authorization=f"Bearer {tok}", x_session_id=None).is_user
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(HTTPException):
        auth.identity(authorization=f"Bearer {jwt.encode(claims(), other, algorithm='ES256')}", x_session_id=None)


def test_guest_and_missing():
    assert auth.identity(authorization=None, x_session_id="guest-session-1").key == "guest:guest-session-1"
    with pytest.raises(HTTPException):
        auth.identity(authorization=None, x_session_id=None)
    with pytest.raises(HTTPException):
        auth.require_user(auth.Identity(key="guest:x"))


def test_crypto_roundtrip(monkeypatch):
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(crypto, "_fernet", None)
    enc = crypto.encrypt("AEBsecret{SWID}")
    assert enc != "AEBsecret{SWID}" and crypto.decrypt(enc) == "AEBsecret{SWID}"
    assert crypto.encrypt(None) is None and crypto.decrypt("garbage") is None


def test_token_issued_slightly_in_the_future_is_accepted():
    """Regression: Supabase's clock can run ~1s ahead; a fresh token must not be rejected as 'not yet valid'."""
    tok = jwt.encode(claims(iat=int(time.time()) + 2), "x" * 40, algorithm="HS256")
    assert auth.identity(authorization=f"Bearer {tok}", x_session_id=None).is_user
    far = jwt.encode(claims(iat=int(time.time()) + 600), "x" * 40, algorithm="HS256")
    with pytest.raises(HTTPException):
        auth.identity(authorization=f"Bearer {far}", x_session_id=None)

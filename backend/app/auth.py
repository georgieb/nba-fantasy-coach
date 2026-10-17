"""Request identity: a verified Supabase user (Authorization: Bearer) or a guest (X-Session-Id)."""
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException

from app import config

_jwks: jwt.PyJWKClient | None = None
CLOCK_SKEW = 30  # seconds; Supabase stamps iat in whole seconds and its clock can run ahead of ours


@dataclass(frozen=True)
class Identity:
    key: str                 # session key: "user:<uuid>" or "guest:<sid>"
    user_id: str | None = None
    email: str | None = None

    @property
    def is_user(self) -> bool:
        return self.user_id is not None


def verify_token(token: str) -> dict:
    """Validate a Supabase access token. Asymmetric projects use JWKS; legacy/local use the HS256 secret."""
    if not config.SUPABASE_URL:
        raise HTTPException(503, "Accounts are not configured on this server")
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        opts = {"audience": "authenticated", "issuer": f"{config.SUPABASE_URL}/auth/v1", "leeway": CLOCK_SKEW}
        if alg == "HS256":
            if not config.SUPABASE_JWT_SECRET:
                raise HTTPException(503, "SUPABASE_JWT_SECRET is required for HS256 tokens")
            claims = jwt.decode(token, config.SUPABASE_JWT_SECRET, algorithms=["HS256"], **opts)
        else:
            global _jwks
            if _jwks is None:
                _jwks = jwt.PyJWKClient(f"{config.SUPABASE_URL}/auth/v1/.well-known/jwks.json", cache_keys=True)
            key = _jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["ES256", "RS256", "EdDSA"], **opts)
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired - sign in again")
    except (jwt.InvalidTokenError, jwt.PyJWKClientError):
        raise HTTPException(401, "Invalid session - sign in again")
    if claims.get("role") != "authenticated" or not claims.get("sub"):
        raise HTTPException(401, "Invalid session - sign in again")
    return claims


def identity(authorization: str | None = Header(None),
             x_session_id: str | None = Header(None, min_length=8, max_length=64)) -> Identity:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "Malformed Authorization header")
        claims = verify_token(token)
        return Identity(key=f"user:{claims['sub']}", user_id=claims["sub"], email=claims.get("email"))
    if x_session_id:
        return Identity(key=f"guest:{x_session_id}")
    raise HTTPException(401, "Missing session")


def require_user(ident: Identity = Depends(identity)) -> Identity:
    if not ident.is_user:
        raise HTTPException(401, "Sign in to use this feature")
    return ident

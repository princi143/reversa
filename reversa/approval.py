"""Signed, single-use approval tokens.

A token is   base64url(JSON claims) "." hex(HMAC-SHA256(secret, base64url(JSON claims)))
with claims  {txn, amount (paise), supplier, action, exp (unix seconds), nonce}.

The secret never leaves the backend process: it comes from $REVERSA_SECRET or a
local `.reversa_secret` file (git-ignored) created on first run. The browser and
any MCP client only ever see finished tokens, so they can carry one but never
mint or alter one. Single use is enforced by the `approvals` table (nonce status).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

TOKEN_TTL_SECONDS = 120


class ApprovalError(Exception):
    pass


def load_secret(path: str | None = None) -> bytes:
    env = os.environ.get("REVERSA_SECRET")
    if env:
        return env.encode()
    p = Path(path or os.environ.get("REVERSA_SECRET_FILE", Path(__file__).resolve().parent.parent / ".reversa_secret"))
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        try:
            p.chmod(0o600)
        except OSError:
            pass
    return p.read_text().strip().encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(secret: bytes, claims: dict) -> str:
    body = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    return body + "." + hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()


def verify_signature(secret: bytes, token: str) -> dict:
    """Return the claims if the signature is genuine, else raise. Does NOT check expiry/binding."""
    try:
        body, sig = token.split(".")
        claims = json.loads(_unb64(body))
    except Exception:
        raise ApprovalError("Malformed approval token.")
    good = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        raise ApprovalError("Approval token signature is invalid (forged or altered).")
    return claims


def new_claims(txn: dict, action: str, amount: int, now: float) -> dict:
    return {"txn": txn["txn_id"], "amount": amount, "supplier": txn["supplier"], "action": action,
            "exp": int(now + TOKEN_TTL_SECONDS), "nonce": secrets.token_hex(8)}


def describe(token: str) -> dict:
    """Decode claims for display (no trust implied)."""
    try:
        return json.loads(_unb64(token.split(".")[0]))
    except Exception:
        return {}

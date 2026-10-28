"""Codes d'accès Premium signés (HMAC), sans base de données.

Un code ressemble à FP-20261128-AB12-9F3C01DE : date d'expiration, identifiant aléatoire, signature.
Le serveur vérifie la signature et la date : aucun stockage n'est nécessaire, les codes survivent
donc aux redémarrages de Render tant que ACCESS_SECRET reste identique.
"""
import hashlib
import hmac
import os
import secrets
from datetime import date, datetime, timedelta, timezone

SECRET_IS_FIXED = bool(os.getenv("ACCESS_SECRET"))
_SECRET = os.getenv("ACCESS_SECRET") or secrets.token_hex(16)


def _sig(body: str) -> str:
    return hmac.new(_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:8].upper()


def _today() -> date:
    return datetime.now(timezone.utc).date()


def make_code(days: int) -> tuple[str, date]:
    """Crée un code valable `days` jours (jour d'expiration inclus)."""
    expires = _today() + timedelta(days=days)
    body = f"FP-{expires:%Y%m%d}-{secrets.token_hex(2).upper()}"
    return f"{body}-{_sig(body)}", expires


def check_code(code: str) -> date | None:
    """Renvoie la date d'expiration si le code est authentique et non expiré, sinon None."""
    parts = (code or "").strip().upper().split("-")
    if len(parts) != 4 or parts[0] != "FP":
        return None
    if not hmac.compare_digest(_sig("-".join(parts[:3])), parts[3]):
        return None
    try:
        expires = datetime.strptime(parts[1], "%Y%m%d").date()
    except ValueError:
        return None
    return expires if expires >= _today() else None

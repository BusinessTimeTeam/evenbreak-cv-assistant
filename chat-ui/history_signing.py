"""Sign and verify conversation history.

The browser holds the conversation and sends it back each turn, so the app
stays stateless. That would let a user fake earlier assistant turns, so the
server signs the history it produced (HMAC-SHA256, bound to the CV session and
an issue time) and rejects history that was edited, replayed against another
session, signed with another key, or is older than 24 hours.

Token format: ``"<issued_at int>.<hex HMAC>"``.
"""

import hashlib
import hmac
import json
import time

MAX_AGE_SECONDS = 86400


class HistoryTokenError(Exception):
    """The history token is missing, malformed, forged, mismatched or expired."""


def _mac(key: bytes, history: list[dict], session_id: str | None, issued_at: int) -> str:
    payload = json.dumps(
        {"h": history, "s": session_id, "t": issued_at},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def sign_history(
    key: bytes,
    history: list[dict],
    session_id: str | None,
    *,
    now: float | None = None,
) -> str:
    issued_at = int(time.time() if now is None else now)
    return f"{issued_at}.{_mac(key, history, session_id, issued_at)}"


def verify_history(
    key: bytes,
    history: list[dict],
    session_id: str | None,
    token: str | None,
    *,
    now: float | None = None,
) -> None:
    # Only an empty list skips verification (a conversation's first turn has
    # no history to protect). Anything else malformed must fail, not pass.
    if history == [] and isinstance(history, list):
        return
    if not isinstance(history, list) or not all(isinstance(t, dict) for t in history):
        raise HistoryTokenError("malformed history")
    if not isinstance(token, str) or "." not in token:
        raise HistoryTokenError("missing or malformed history token")
    issued, _, mac = token.partition(".")
    try:
        issued_at = int(issued)
        expected = _mac(key, history, session_id, issued_at)
        matches = hmac.compare_digest(expected.encode(), mac.encode())
    except (ValueError, TypeError, RecursionError):
        # Includes UnicodeEncodeError (lone surrogates) and over-long integers.
        raise HistoryTokenError("malformed history or token") from None
    if not matches:
        raise HistoryTokenError("history token does not match")
    current = time.time() if now is None else now
    if current - issued_at > MAX_AGE_SECONDS:
        raise HistoryTokenError("history token expired")

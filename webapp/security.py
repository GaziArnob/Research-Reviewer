import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    if len(password) < 10:
        return "Use at least 10 characters."
    if password.lower() in {"password123", "1234567890", "qwertyuiop"} or len(set(password)) < 4:
        return "That password is too easy to guess."
    return None


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


def csrf_ok(request: Request, token: str | None) -> bool:
    expected = request.session.get("csrf")
    return bool(expected and token and hmac.compare_digest(expected, token))


class LoginThrottle:
    """Blocks brute force: 6 failed logins per 15 minutes for the same address + email."""

    def __init__(self, limit: int = 6, window: int = 900):
        self.limit, self.window, self.fails = limit, window, defaultdict(deque)

    def _trim(self, key):
        q = self.fails[key]
        while q and q[0] < time.time() - self.window:
            q.popleft()
        return q

    def blocked(self, key) -> bool:
        return len(self._trim(key)) >= self.limit

    def fail(self, key) -> None:
        self._trim(key).append(time.time())

    def clear(self, key) -> None:
        self.fails.pop(key, None)


throttle = LoginThrottle()


class SecurityHeaders(BaseHTTPMiddleware):
    """No inline scripts or styles are used, so the policy can be strict."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy",
                                    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith("/app") or request.url.path in ("/login", "/signup"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

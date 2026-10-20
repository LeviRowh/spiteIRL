import base64, hashlib, hmac, os
from pathlib import Path
from fastapi import HTTPException, Request

_f = Path(__file__).with_name(".session_secret")
if not _f.exists():
    _f.write_text(base64.b64encode(os.urandom(32)).decode())
SECRET = _f.read_text().encode()


def _sig(v: str) -> str:
    return hmac.new(SECRET, v.encode(), hashlib.sha256).hexdigest()


def verify(token: str) -> str:
    if not token or "." not in token:
        return ""
    user, sig = token.rsplit(".", 1)
    return user if hmac.compare_digest(sig, _sig(user)) else ""


def current_user(request: Request) -> str:
    return verify(request.cookies.get("session", ""))


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return user


def set_session(response, user: str) -> None:
    response.set_cookie("session", f"{user}.{_sig(user)}", httponly=True,
                        samesite="lax", max_age=7 * 86400)

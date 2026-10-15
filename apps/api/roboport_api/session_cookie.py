"""Cookie policy for a future session HTTP handler.

Cookie is issued only by explicitly gated synthetic localhost auth.
"""
from fastapi import Response


def set_session_cookie(response: Response, token: str, *, tls: bool = True) -> None:
    if not tls:
        raise ValueError("insecure cookies are disabled outside a dedicated loopback adapter")
    response.set_cookie(
        key="rp_session", value=token, max_age=12 * 60 * 60,
        httponly=True, secure=True, samesite="lax", path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key="rp_session", path="/", secure=True, httponly=True, samesite="lax")

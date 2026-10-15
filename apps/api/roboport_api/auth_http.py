"""Explicit loopback-only synthetic authentication adapter. Not for production."""
import os
import hashlib
from urllib.parse import urlsplit
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .passwords import verify_password
from .session_service import issue_session, revoke_session, verify_csrf
from .session_lookup import session_subject
from .session_cookie import set_session_cookie, clear_session_cookie

router=APIRouter(include_in_schema=False)


class Credentials(BaseModel):
    login: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def _dsn(request: Request) -> str | None:
    raw=os.getenv("ROBOport_DATABASE_URL","")
    host=urlsplit(raw).hostname
    if (os.getenv("ROBOport_SYNTHETIC_AUTH_API") != "1"
        or host != "127.0.0.1" or request.url.hostname not in ("127.0.0.1","localhost")):
        return None
    return raw.replace("postgresql+psycopg://","postgresql://",1)


def _error(request: Request, status: int):
    return JSONResponse(status_code=status,content={"code":"UNAUTHENTICATED" if status==401 else "FORBIDDEN","message":"Доступ запрещён","requestId":request.state.request_id},headers={"Cache-Control":"no-store"})


def _origin_ok(request: Request) -> bool:
    origin=request.headers.get("origin")
    return origin is not None and origin==str(request.base_url).rstrip("/")


@router.post("/api/v1/auth/login")
def login(credentials: Credentials, request: Request):
    dsn=_dsn(request)
    if dsn is None:
        return _error(request,403)
    if not _origin_ok(request):
        return _error(request,403)
    normalized=credentials.login.strip().lower()
    login_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    with psycopg.connect(dsn,autocommit=True) as conn:
        failures=conn.execute("""
            INSERT INTO auth_login_throttle(login_hash,failures) VALUES (%s,1)
            ON CONFLICT(login_hash) DO UPDATE SET
              failures=CASE WHEN auth_login_throttle.window_started_at < now()-interval '15 minutes'
                THEN 1 ELSE auth_login_throttle.failures+1 END,
              window_started_at=CASE WHEN auth_login_throttle.window_started_at < now()-interval '15 minutes'
                THEN now() ELSE auth_login_throttle.window_started_at END
            RETURNING failures
        """,(login_hash,)).fetchone()[0]
        if failures>5:
            return JSONResponse(status_code=429,content={"code":"RATE_LIMITED","message":"Повторите позже","requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
        row=conn.execute("""SELECT u.id,c.password_hash FROM app_user u
            JOIN local_credential c ON c.user_id=u.id
            WHERE u.normalized_login=%s AND u.status='ACTIVE' AND c.must_change_password=false""",
            (normalized,)).fetchone()
        if row is None or not verify_password(credentials.password,row[1]):
            return _error(request,401)
        tokens=issue_session(conn,row[0])
        if tokens is not None:
            conn.execute("DELETE FROM auth_login_throttle WHERE login_hash=%s",(login_hash,))
    if tokens is None:
        return _error(request,401)
    response=JSONResponse(content={"csrfToken":tokens[1]},headers={"Cache-Control":"no-store"})
    set_session_cookie(response,tokens[0])
    return response


@router.post("/api/v1/auth/logout")
def logout(request: Request):
    dsn=_dsn(request)
    if dsn is None or not _origin_ok(request):
        return _error(request,403)
    token=request.cookies.get("roboport_session","")
    csrf=request.headers.get("x-csrf-token","")
    with psycopg.connect(dsn,autocommit=True) as conn:
        if not verify_csrf(conn,token,csrf):
            return _error(request,403)
        revoke_session(conn,token)
    response=JSONResponse(content={"loggedOut":True},headers={"Cache-Control":"no-store"})
    clear_session_cookie(response)
    return response


@router.get("/api/v1/auth/me")
def me(request: Request):
    dsn=_dsn(request)
    if dsn is None:
        return _error(request,403)
    with psycopg.connect(dsn,autocommit=True) as conn:
        subject=session_subject(conn,request.cookies.get("roboport_session",""))
    if subject is None:
        return _error(request,401)
    return JSONResponse(content={"subjectId":str(subject)},headers={"Cache-Control":"no-store"})

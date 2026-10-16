"""Explicit loopback-only synthetic authentication adapter. Not for production."""
import os
import hashlib
from urllib.parse import urlsplit
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from .passwords import verify_password, hash_password
from .session_service import issue_session, revoke_session, verify_csrf
from .session_lookup import session_subject
from .session_cookie import set_session_cookie, clear_session_cookie

# Fixed Argon2id sentinel for absent accounts; never a usable credential.
_DUMMY_PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$SeTItVGGw+BtNGFsMzzl9A$/JMANrplfkaKb911s04/mF1zlsBuSozi3Vg46p9it20"

router=APIRouter(include_in_schema=False)


class Credentials(BaseModel):
    login: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def _dsn(request: Request) -> str | None:
    raw=os.getenv("ROBOport_DATABASE_URL","")
    # A bad DSN must fail closed, without leaking an exception through HTTP.
    try:
        parsed=urlsplit(raw)
        host=parsed.hostname
        port=parsed.port
    except ValueError:
        return None
    if (os.getenv("ROBOport_SYNTHETIC_AUTH_API") != "1"
        or parsed.scheme not in ("postgresql", "postgresql+psycopg")
        or host != "127.0.0.1" or port is None
        or request.url.hostname not in ("127.0.0.1","localhost")
        or request.url.scheme != "https"):
        return None
    return raw.replace("postgresql+psycopg://","postgresql://",1)


def _error(request: Request, status: int):
    return JSONResponse(status_code=status,content={"code":"UNAUTHENTICATED" if status==401 else "FORBIDDEN","message":"Доступ запрещён","requestId":request.state.request_id},headers={"Cache-Control":"no-store"})


def _origin_ok(request: Request) -> bool:
    origin=request.headers.get("origin")
    return origin is not None and origin==str(request.base_url).rstrip("/")


def _mutation_ok(request: Request, conn: psycopg.Connection) -> bool:
    """Shared same-origin + session-bound CSRF gate for authenticated mutations."""
    if not _origin_ok(request):
        return False
    return verify_csrf(conn, request.cookies.get("rp_session", ""),
                       request.headers.get("x-csrf-token", ""))


@router.post("/api/v1/auth/login")
async def login(request: Request):
    dsn=_dsn(request)
    if dsn is None:
        return _error(request,403)
    if not _origin_ok(request):
        return _error(request,403)
    try:
        raw_payload=await request.json()
        credentials=Credentials.model_validate(raw_payload)
    except (ValueError, ValidationError, UnicodeDecodeError, TypeError):
        return JSONResponse(status_code=422,content={"code":"INVALID_INPUT","message":"Некорректный запрос","requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
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
        # Argon2id cost is paid even when the username does not exist.
        password_ok=verify_password(credentials.password,row[1] if row else _DUMMY_PASSWORD_HASH)
        if row is None or not password_ok:
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
    if dsn is None:
        return _error(request,403)
    token=request.cookies.get("rp_session","")
    with psycopg.connect(dsn,autocommit=True) as conn:
        if not _mutation_ok(request,conn):
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
        subject=session_subject(conn,request.cookies.get("rp_session",""))
    if subject is None:
        return _error(request,401)
    return JSONResponse(content={"subjectId":str(subject)},headers={"Cache-Control":"no-store"})


class PasswordChange(BaseModel):
    oldPassword: str = Field(min_length=1, max_length=1024)
    newPassword: str = Field(min_length=12, max_length=1024)


@router.post("/api/v1/auth/change-password")
async def change_password(request: Request):
    dsn=_dsn(request)
    if dsn is None:
        return _error(request,403)
    with psycopg.connect(dsn,autocommit=True) as conn:
        if not _mutation_ok(request,conn):
            return _error(request,403)
        try:
            payload=PasswordChange.model_validate(await request.json())
        except (ValueError, ValidationError):
            return JSONResponse(status_code=422,content={"code":"INVALID_INPUT","message":"Некорректный запрос","requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
        token=request.cookies.get("rp_session","")
        with conn.transaction():
            subject=session_subject(conn,token)
            if subject is None:
                return _error(request,401)
            row=conn.execute("SELECT password_hash FROM local_credential WHERE user_id=%s FOR UPDATE",(subject,)).fetchone()
            if row is None or not verify_password(payload.oldPassword,row[0]) or payload.oldPassword==payload.newPassword:
                return _error(request,403)
            conn.execute("UPDATE local_credential SET password_hash=%s,must_change_password=false,updated_at=now() WHERE user_id=%s",(hash_password(payload.newPassword),subject))
            conn.execute("UPDATE app_user SET password_changed_at=now() WHERE id=%s",(subject,))
            conn.execute("UPDATE user_session SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL",(subject,))
    response=JSONResponse(content={"changed":True},headers={"Cache-Control":"no-store"})
    clear_session_cookie(response)
    return response


@router.post("/api/v1/auth/csrf")
def csrf_bootstrap(request: Request):
    """Same-origin cookie-authenticated CSRF rotation for SPA after reload.

    The only effect is replacing the old CSRF verifier; there is no business mutation.
    No cross-origin caller can read the replacement token.
    """
    from .session_tokens import new_token,token_digest
    dsn=_dsn(request)
    if dsn is None or not _origin_ok(request):
        return _error(request,403)
    raw=request.cookies.get("rp_session","")
    if not isinstance(raw,str) or len(raw)!=43:
        return _error(request,401)
    csrf=new_token()
    with psycopg.connect(dsn,autocommit=True) as conn:
        row=conn.execute("""SELECT s.id FROM user_session s
             JOIN app_user u ON u.id=s.user_id AND u.status='ACTIVE'
             WHERE s.token_hash=%s AND s.revoked_at IS NULL
             AND s.idle_expires_at>now() AND s.absolute_expires_at>now()
             FOR UPDATE OF s""",(token_digest(raw),)).fetchone()
        if row is not None:
            conn.execute("""INSERT INTO user_session_csrf_rotation(session_id,csrf_hash,rotated_at)
                VALUES(%s,%s,now()) ON CONFLICT(session_id) DO UPDATE
                SET csrf_hash=excluded.csrf_hash,rotated_at=now()""",
                (row[0],token_digest(csrf)))
    if row is None:
        return _error(request,401)
    return JSONResponse(content={"csrfToken":csrf},
        headers={"Cache-Control":"no-store"})

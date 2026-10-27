"""Синтетический API. Реальные заявки и приватные маршруты отключены."""
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="ROBOport development API", version="0.1.0")

from .auth_http import router as synthetic_auth_router
app.include_router(synthetic_auth_router)
from .oidc_http import router as oidc_router
app.include_router(oidc_router)
from .guest_claim_http import router as guest_claim_router
app.include_router(guest_claim_router)
from .auto_vehicle_http import router as auto_vehicle_router
app.include_router(auto_vehicle_router)
from .auto_vehicle_claim_http import router as auto_vehicle_claim_router
app.include_router(auto_vehicle_claim_router)
from .auto_intake_delegation_http import router as auto_intake_delegation_router
app.include_router(auto_intake_delegation_router)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    # Идентификатор создаётся сервером; клиентский X-Request-ID не доверенный.
    request.state.request_id = str(uuid4())
    # All synthetic dev endpoints must fail before DB/storage access on unsafe transports.
    # Apply to the exact /dev root and slash-delimited descendants, not lookalike paths.
    path = request.url.path
    # Never infer a trusted socket/TLS peer from caller-controlled proxy headers.
    # There is no approved reverse-proxy ingress for synthetic auto yet.
    # Reject these before API/database activity, even when ASGI scope already
    # claims HTTPS (e.g. proxy middleware may have rewritten the scope).
    if path == "/api/v1/dev/auto" or path.startswith("/api/v1/dev/auto/"):
        from .auto_proxy_boundary import has_untrusted_proxy_headers
        if has_untrusted_proxy_headers(request.headers):
            return not_found(request)
    if path == "/api/v1/dev" or path.startswith("/api/v1/dev/"):
        if (request.url.scheme != "https"
                or request.url.hostname not in ("127.0.0.1", "localhost")):
            return not_found(request)
    # This is a strict opt-in, direct-socket-only dev pilot, not a trusted
    # proxy implementation. Rate-limit *before* session/CSRF and business
    # operations; the separate PG transaction persists even for 403/422.
    if request.method == "POST" and path.startswith("/api/v1/dev/auto/"):
        import os
        flag = os.getenv("ROBOport_SYNTHETIC_AUTO_PEER_LIMIT", "0")
        if flag != "0":
            if flag != "1" or os.getenv("ROBOport_SYNTHETIC_AUTO_API") != "1":
                return not_found(request)
            from .auth_http import _dsn
            from .auto_peer_throttle import (
                peer_fingerprint, consume_peer_request, WINDOW_SECONDS,
            )
            from starlette.concurrency import run_in_threadpool
            import psycopg
            dsn = _dsn(request)
            fingerprint = peer_fingerprint(
                request.scope.get("client"),
                os.getenv("ROBOport_SYNTHETIC_AUTO_PEER_HMAC_KEY", ""),
            )
            if dsn is None or fingerprint is None:
                return not_found(request)
            try:
                permitted = await run_in_threadpool(
                    consume_peer_request, dsn, fingerprint,
                )
            except psycopg.Error:
                return JSONResponse(
                    status_code=503,
                    content={"code": "UNAVAILABLE", "requestId": request.state.request_id},
                    headers={"Cache-Control": "no-store", "Retry-After": "60"},
                )
            if not permitted:
                return JSONResponse(
                    status_code=429,
                    content={"code": "RATE_LIMITED", "requestId": request.state.request_id},
                    headers={
                        "Cache-Control": "no-store",
                        "Retry-After": str(WINDOW_SECONDS),
                    },
                )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def not_found(request: Request) -> JSONResponse:
    request_id = request.state.request_id
    return JSONResponse(
        status_code=404,
        content={"code": "NOT_FOUND", "message": "Модель не найдена", "requestId": request_id},
        headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
    )


@app.get("/health/live")
def live():
    return {"status": "alive"}


@app.get("/health/ready")
def ready():
    return JSONResponse(status_code=503, content={"status": "not_ready"})


@app.get("/api/v1/meta")
def meta():
    return {"service": "ROBOport", "environment": "development"}


@app.get("/api/v1/catalog/brands")
def brands():
    return {"items": []}


@app.get("/api/v1/catalog/models")
def models():
    return {"items": []}


@app.get("/api/v1/catalog/models/{modelId}")
def model(modelId: str, request: Request):
    return not_found(request)


@app.get("/api/v1/dev/tickets/keyset", include_in_schema=False)
def dev_ticket_keyset(request: Request, limit: int = 20, state: str | None = None,
                      before: str | None = None):
    """Opt-in keyset listing; opaque cursor is a timestamp and UUID, not a user ID."""
    import os
    import base64
    import json
    from datetime import datetime
    from urllib.parse import urlsplit
    from uuid import UUID
    import psycopg
    from .ticket_read import TICKET_STATES, owned_ticket_keyset_page
    from .keyset_cursor import decode_cursor, encode_cursor

    if os.getenv("ROBOport_SYNTHETIC_ACCESS_API") != "1":
        return not_found(request)
    raw = os.getenv("ROBOport_DATABASE_URL", "")
    try:
        db = urlsplit(raw)
        if (db.scheme not in ("postgresql", "postgresql+psycopg")
                or db.hostname != "127.0.0.1" or db.port is None):
            return not_found(request)
    except ValueError:
        return not_found(request)
    invalid = lambda: JSONResponse(status_code=422, content={
        "code":"INVALID_INPUT","requestId":request.state.request_id
    }, headers={"Cache-Control":"no-store"})
    if not 1 <= limit <= 50 or (state is not None and state not in TICKET_STATES):
        return invalid()
    token = request.cookies.get("rp_session", "")
    if not token:
        return JSONResponse(status_code=401, content={
            "code":"UNAUTHENTICATED","requestId":request.state.request_id
        },headers={"Cache-Control":"no-store"})
    stamp = ident = None
    if before is not None:
        try:
            stamp, ident = decode_cursor(before, state, token)
        except ValueError:
            return invalid()
    with psycopg.connect(raw.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        result=owned_ticket_keyset_page(conn,token,limit=limit,state=state,
                                        before_created_at=stamp,before_id=ident)
    if result is None:
        return JSONResponse(status_code=401,content={
            "code":"UNAUTHENTICATED","requestId":request.state.request_id
        },headers={"Cache-Control":"no-store"})
    items,following=result
    cursor=encode_cursor(following[0],following[1],state,token) if following else None
    return JSONResponse(content={"items":items,"nextCursor":cursor},
                        headers={"Cache-Control":"no-store"})


@app.get("/api/v1/dev/tickets/{ticket_id}/access", include_in_schema=False)
def dev_ticket_access(ticket_id: str, request: Request):
    """Synthetic-only read probe, disabled without explicit opt-in and disposable DB."""
    import os
    from urllib.parse import urlsplit
    from uuid import UUID
    import psycopg
    from .session_lookup import session_can_read_ticket

    if os.getenv("ROBOport_SYNTHETIC_ACCESS_API") != "1":
        return not_found(request)
    raw_dsn = os.getenv("ROBOport_DATABASE_URL", "")
    try:
        parsed_dsn = urlsplit(raw_dsn)
        valid_dsn = (
            parsed_dsn.scheme in ("postgresql", "postgresql+psycopg")
            and parsed_dsn.hostname == "127.0.0.1"
            and parsed_dsn.port is not None
        )
    except ValueError:
        valid_dsn = False
    if not valid_dsn:
        return not_found(request)
    token = request.cookies.get("rp_session")
    if not token:
        return JSONResponse(status_code=401, content={
            "code": "UNAUTHENTICATED", "message": "Требуется вход",
            "requestId": request.state.request_id,
        }, headers={"Cache-Control": "no-store"})
    try:
        parsed = UUID(ticket_id)
    except ValueError:
        return not_found(request)
    dsn = raw_dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as conn:
        permitted = session_can_read_ticket(conn, token, parsed)
    if not permitted:
        return not_found(request)
    return JSONResponse(
        content={"id": str(parsed), "accessible": True},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/v1/dev/tickets/{ticket_id}", include_in_schema=False)
def dev_ticket_minimal_read(ticket_id: str, request: Request):
    """Synthetic only; return strictly the UUID after DB ownership validation."""
    import os
    from urllib.parse import urlsplit
    from uuid import UUID
    import psycopg
    from .session_lookup import session_can_read_ticket

    if os.getenv("ROBOport_SYNTHETIC_ACCESS_API") != "1":
        return not_found(request)
    dsn = os.getenv("ROBOport_DATABASE_URL", "")
    try:
        parsed_dsn = urlsplit(dsn)
        if (parsed_dsn.scheme not in ("postgresql", "postgresql+psycopg")
            or parsed_dsn.hostname != "127.0.0.1"
            or parsed_dsn.port is None):
            return not_found(request)
        ticket = UUID(ticket_id)
    except ValueError:
        return not_found(request)
    token = request.cookies.get("rp_session", "")
    if not token:
        return JSONResponse(
            status_code=401,
            content={"code": "UNAUTHENTICATED", "message": "Требуется вход",
                     "requestId": request.state.request_id},
            headers={"Cache-Control": "no-store"},
        )
    with psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://", 1)) as conn:
        if not session_can_read_ticket(conn, token, ticket):
            return not_found(request)
    return JSONResponse(content={"id": str(ticket)}, headers={"Cache-Control": "no-store"})


@app.get("/api/v1/dev/tickets/{ticket_id}/state", include_in_schema=False)
def dev_ticket_state(ticket_id: str, request: Request):
    """Opt-in read-only status: no PII and no production route."""
    import os
    from urllib.parse import urlsplit
    from uuid import UUID
    import psycopg
    from .ticket_read import owned_ticket_state

    if os.getenv("ROBOport_SYNTHETIC_ACCESS_API") != "1":
        return not_found(request)
    raw = os.getenv("ROBOport_DATABASE_URL", "")
    try:
        url = urlsplit(raw)
        if (url.scheme not in ("postgresql", "postgresql+psycopg")
                or url.hostname != "127.0.0.1" or url.port is None):
            return not_found(request)
        ticket = UUID(ticket_id)
    except ValueError:
        return not_found(request)
    token = request.cookies.get("rp_session", "")
    if not token:
        return JSONResponse(status_code=401, content={
            "code": "UNAUTHENTICATED", "message": "Требуется вход",
            "requestId": request.state.request_id,
        }, headers={"Cache-Control": "no-store"})
    with psycopg.connect(raw.replace("postgresql+psycopg://", "postgresql://", 1)) as conn:
        result = owned_ticket_state(conn, token, ticket)
    if result is None:
        return not_found(request)
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


@app.get("/api/v1/dev/tickets", include_in_schema=False)
def dev_owned_tickets(request: Request, limit: int = 20, offset: int = 0, state: str | None = None):
    """Synthetic-only paginated list, disabled by default."""
    import os
    from urllib.parse import urlsplit
    import psycopg
    from .ticket_read import owned_ticket_list, TICKET_STATES

    if os.getenv("ROBOport_SYNTHETIC_ACCESS_API") != "1":
        return not_found(request)
    raw = os.getenv("ROBOport_DATABASE_URL", "")
    try:
        target = urlsplit(raw)
        if (target.scheme not in ("postgresql", "postgresql+psycopg")
                or target.hostname != "127.0.0.1" or target.port is None):
            return not_found(request)
    except ValueError:
        return not_found(request)
    if (not 1 <= limit <= 50 or not 0 <= offset <= 10000
            or (state is not None and state not in TICKET_STATES)):
        return JSONResponse(status_code=422, content={"code":"INVALID_INPUT","requestId":request.state.request_id}, headers={"Cache-Control":"no-store"})
    token = request.cookies.get("rp_session", "")
    if not token:
        return JSONResponse(status_code=401, content={"code":"UNAUTHENTICATED","requestId":request.state.request_id}, headers={"Cache-Control":"no-store"})
    with psycopg.connect(raw.replace("postgresql+psycopg://", "postgresql://", 1)) as conn:
        result = owned_ticket_list(conn, token, limit=limit, offset=offset, state=state)
    if result is None:
        return JSONResponse(status_code=401, content={"code":"UNAUTHENTICATED","requestId":request.state.request_id}, headers={"Cache-Control":"no-store"})
    return JSONResponse(content={"items":result,"limit":limit,"offset":offset}, headers={"Cache-Control":"no-store"})




@app.get("/api/v1/dev/authorized/tickets/{ticket_id}", include_in_schema=False)
def dev_authorized_ticket(ticket_id: str, request: Request):
    """Experimental RBAC/ACL read with durable decision audit, loopback only."""
    import os
    from urllib.parse import urlsplit
    from uuid import UUID
    import psycopg
    from .authorization import authorized_ticket_read_audited

    if os.getenv("ROBOport_SYNTHETIC_ACCESS_API") != "1":
        return not_found(request)
    if request.url.scheme != "https" or request.url.hostname not in ("127.0.0.1", "localhost"):
        return not_found(request)
    raw = os.getenv("ROBOport_DATABASE_URL", "")
    try:
        dsn = urlsplit(raw)
        if (dsn.scheme not in ("postgresql", "postgresql+psycopg")
                or dsn.hostname != "127.0.0.1" or dsn.port is None):
            return not_found(request)
        ident = UUID(ticket_id)
    except ValueError:
        return not_found(request)
    token = request.cookies.get("rp_session", "")
    if not token:
        return JSONResponse(status_code=401,content={
            "code":"UNAUTHENTICATED","requestId":request.state.request_id,
        }, headers={"Cache-Control":"no-store"})
    with psycopg.connect(raw.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        allowed=authorized_ticket_read_audited(conn,token,ident)
    if not allowed:
        return not_found(request)
    return JSONResponse(content={"id":str(ident)},headers={"Cache-Control":"no-store"})


@app.get("/api/v1/dev/attachments/{attachment_id}/content", include_in_schema=False)
def dev_attachment_content(attachment_id: str, request: Request):
    """Synthetic private read: always check PostgreSQL ACL before MinIO."""
    import hashlib
    import os
    from urllib.parse import urlsplit
    from uuid import UUID
    import psycopg
    from fastapi.responses import Response
    from .document_access import audited_attachment_gate
    from .storage import MinioFileStorage

    if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API") != "1"
            or request.url.scheme != "https"
            or request.url.hostname not in ("127.0.0.1","localhost")):
        return not_found(request)
    dsn=os.getenv("ROBOport_DATABASE_URL","")
    try:
        target=urlsplit(dsn)
        if (target.scheme not in ("postgresql","postgresql+psycopg")
                or target.hostname != "127.0.0.1" or target.port is None):
            return not_found(request)
        ident=UUID(attachment_id)
    except ValueError:
        return not_found(request)
    token=request.cookies.get("rp_session","")
    if not token:
        return JSONResponse(status_code=401,content={
            "code":"UNAUTHENTICATED","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    with psycopg.connect(dsn.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        meta=audited_attachment_gate(conn,token,ident)
    if meta is None:
        return not_found(request)
    try:
        data=MinioFileStorage.from_environment().get(meta[0])
    except Exception:
        # Never expose storage secrets, bucket names or opaque object keys.
        return JSONResponse(status_code=503,content={
            "code":"STORAGE_UNAVAILABLE","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    if len(data)!=meta[3] or hashlib.sha256(data).hexdigest()!=meta[2]:
        return JSONResponse(status_code=503,content={
            "code":"STORAGE_UNAVAILABLE","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    return Response(data,media_type=meta[1],headers={
        "Cache-Control":"no-store","X-Content-Type-Options":"nosniff",
        "Content-Disposition":"attachment",
    })


@app.post("/api/v1/dev/tickets/{ticket_id}/attachments", include_in_schema=False)
async def dev_upload_attachment(ticket_id: str, request: Request):
    """Synthetic-only binary upload with current ACL and mandatory Origin/CSRF."""
    import os
    from urllib.parse import urlsplit
    from uuid import UUID
    import psycopg
    from .auth_http import _mutation_ok
    from .document_upload import create_attachment
    from .document_access import authorize_attachment_upload
    from .storage import MinioFileStorage, MAX_SIZE, MIME

    if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API")!="1"
            or request.url.scheme!="https"
            or request.url.hostname not in ("127.0.0.1","localhost")):
        return not_found(request)
    raw=os.getenv("ROBOport_DATABASE_URL","")
    try:
        parsed=urlsplit(raw)
        if (parsed.scheme not in ("postgresql","postgresql+psycopg")
                or parsed.hostname!="127.0.0.1" or parsed.port is None):
            return not_found(request)
        ticket=UUID(ticket_id)
    except ValueError:
        return not_found(request)
    mime=request.headers.get("content-type","").split(";")[0].strip().lower()
    if mime not in MIME:
        return JSONResponse(status_code=422,content={
            "code":"INVALID_INPUT","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    if request.headers.get("content-length","").isdigit() and int(
            request.headers["content-length"])>MAX_SIZE:
        return JSONResponse(status_code=413,content={
            "code":"PAYLOAD_TOO_LARGE","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    payload=await request.body()
    if not 0<len(payload)<=MAX_SIZE:
        return JSONResponse(status_code=413,content={
            "code":"PAYLOAD_TOO_LARGE","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    with psycopg.connect(raw.replace("postgresql+psycopg://","postgresql://",1),autocommit=True) as conn:
        if not _mutation_ok(request,conn):
            return JSONResponse(status_code=403,content={
                "code":"FORBIDDEN","requestId":request.state.request_id,
            },headers={"Cache-Control":"no-store"})
        actor=authorize_attachment_upload(conn,request.cookies.get("rp_session",""),ticket)
        from uuid import uuid4
        conn.execute(
            "INSERT INTO authorization_audit(id,actor_id,action,outcome) VALUES(%s,%s,'attachment.write',%s)",
            (uuid4(),actor,'ALLOW' if actor else 'DENY'),
        )
        if actor is None:
            return not_found(request)
        retry_key=request.headers.get("idempotency-key")
        if retry_key is not None and not 1<=len(retry_key)<=128:
            return JSONResponse(status_code=422,content={
                "code":"INVALID_INPUT","requestId":request.state.request_id,
            },headers={"Cache-Control":"no-store"})
        try:
            attachment_id=create_attachment(conn,MinioFileStorage.from_environment(),
                                            request.cookies.get("rp_session",""),
                                            ticket,payload,mime,retry_key)
        except ValueError:
            return JSONResponse(status_code=409,content={
                "code":"CONFLICT","requestId":request.state.request_id,
            },headers={"Cache-Control":"no-store"})
        except Exception:
            return JSONResponse(status_code=503,content={
                "code":"STORAGE_UNAVAILABLE","requestId":request.state.request_id,
            },headers={"Cache-Control":"no-store"})
    if attachment_id is None:
        return not_found(request)
    return JSONResponse(status_code=201,content={"id":str(attachment_id)},
                        headers={"Cache-Control":"no-store"})


@app.get("/api/v1/dev/estimates/{estimate_id}/access", include_in_schema=False)
def dev_estimate_access(estimate_id: str, request: Request):
    """Synthetic estimate access probe with current role, ACL and state."""
    import os
    from urllib.parse import urlsplit
    from uuid import UUID,uuid4
    import psycopg
    from .authorization import authorize_estimate
    from .session_lookup import session_subject

    if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API")!="1"
            or request.url.scheme!="https"
            or request.url.hostname not in ("127.0.0.1","localhost")):
        return not_found(request)
    raw=os.getenv("ROBOport_DATABASE_URL","")
    try:
        db=urlsplit(raw)
        if (db.scheme not in ("postgresql","postgresql+psycopg")
                or db.hostname!="127.0.0.1" or db.port is None):
            return not_found(request)
        ident=UUID(estimate_id)
    except ValueError:
        return not_found(request)
    token=request.cookies.get("rp_session","")
    if not token:
        return JSONResponse(status_code=401,content={
            "code":"UNAUTHENTICATED","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    with psycopg.connect(raw.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        actor=session_subject(conn,token)
        allowed=authorize_estimate(conn,token,"estimate.read",ident)
        conn.execute("""INSERT INTO authorization_audit(id,actor_id,action,outcome)
                        VALUES(%s,%s,'estimate.read',%s)""",
                     (uuid4(),actor,"ALLOW" if allowed else "DENY"))
    if not allowed:
        return not_found(request)
    return JSONResponse(content={"id":str(ident),"accessible":True},
                        headers={"Cache-Control":"no-store"})


@app.post("/api/v1/dev/tickets/{ticket_id}/transition", include_in_schema=False)
async def dev_ticket_transition(ticket_id: str, request: Request):
    """Explicit guarded FSM mutation; never accepts an arbitrary status PATCH."""
    import os
    from urllib.parse import urlsplit
    from uuid import UUID
    import psycopg
    from .auth_http import _origin_ok
    from .session_service import verify_csrf
    from .ticket_fsm import transition_ticket, TransitionConflict
    from .ticket_idempotency import execute_ticket_mutation

    if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API") != "1"
            or request.url.scheme != "https"
            or request.url.hostname not in ("127.0.0.1","localhost")):
        return not_found(request)
    raw=os.getenv("ROBOport_DATABASE_URL","")
    try:
        target=urlsplit(raw)
        if (target.scheme not in ("postgresql","postgresql+psycopg")
                or target.hostname != "127.0.0.1" or target.port is None):
            return not_found(request)
        ticket=UUID(ticket_id)
    except ValueError:
        return not_found(request)
    token=request.cookies.get("rp_session","")
    if not token:
        return JSONResponse(status_code=401,content={
            "code":"UNAUTHENTICATED","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    if not _origin_ok(request):
        return JSONResponse(status_code=403,content={
            "code":"FORBIDDEN","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    try:
        payload=await request.json()
        if (not isinstance(payload,dict) or
                not isinstance(payload.get("toState"),str) or
                type(payload.get("expectedVersion")) is not int):
            raise ValueError("invalid payload")
    except (ValueError,TypeError):
        return JSONResponse(status_code=422,content={
            "code":"INVALID_INPUT","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    idem=request.headers.get("Idempotency-Key")
    if idem is not None and (not 1 <= len(idem) <= 128 or not idem.isascii() or not idem.isprintable()):
        return JSONResponse(status_code=422,content={
            "code":"INVALID_INPUT","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    with psycopg.connect(raw.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        if not verify_csrf(conn,token,request.headers.get("x-csrf-token","")):
            return JSONResponse(status_code=403,content={
                "code":"FORBIDDEN","requestId":request.state.request_id,
            },headers={"Cache-Control":"no-store"})
        try:
            result=execute_ticket_mutation(
                conn,token,ticket,"transition",payload,
                request.headers.get("Idempotency-Key"),
                lambda: transition_ticket(conn,token,ticket,
                                          payload["toState"],payload["expectedVersion"]))
        except TransitionConflict:
            return JSONResponse(status_code=409,content={
                "code":"CONFLICT","requestId":request.state.request_id,
            },headers={"Cache-Control":"no-store"})
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})



async def _safety_mutation(request: Request, ticket_id: str, operation: str):
    """Private synthetic reviewer workflow, same session/CSRF/Origin guarantees."""
    import os
    from urllib.parse import urlsplit
    from uuid import UUID
    import psycopg
    from .auth_http import _origin_ok
    from .session_service import verify_csrf
    from .ticket_fsm import record_safety_review, release_safety_hold, TransitionConflict
    from .ticket_idempotency import execute_ticket_mutation

    if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API") != "1"
            or request.url.scheme != "https"
            or request.url.hostname not in ("127.0.0.1","localhost")):
        return not_found(request)
    raw=os.getenv("ROBOport_DATABASE_URL","")
    try:
        dsn=urlsplit(raw)
        if (dsn.scheme not in ("postgresql","postgresql+psycopg")
                or dsn.hostname != "127.0.0.1" or dsn.port is None):
            return not_found(request)
        ticket=UUID(ticket_id)
    except ValueError:
        return not_found(request)
    token=request.cookies.get("rp_session","")
    if not token:
        return JSONResponse(status_code=401,content={
            "code":"UNAUTHENTICATED","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    if not _origin_ok(request):
        return JSONResponse(status_code=403,content={
            "code":"FORBIDDEN","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    try:
        payload=await request.json()
        if (not isinstance(payload,dict) or
                type(payload.get("expectedVersion")) is not int or
                (operation=="review" and type(payload.get("approved")) is not bool)):
            raise ValueError("invalid request")
    except (ValueError,TypeError):
        return JSONResponse(status_code=422,content={
            "code":"INVALID_INPUT","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    with psycopg.connect(raw.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        if not verify_csrf(conn,token,request.headers.get("x-csrf-token","")):
            return JSONResponse(status_code=403,content={
                "code":"FORBIDDEN","requestId":request.state.request_id,
            },headers={"Cache-Control":"no-store"})
        try:
            def effect():
                if operation=="review":
                    reviewer_role=record_safety_review(
                        conn,token,ticket,payload["expectedVersion"],payload["approved"])
                    return {"recorded":True,"role":reviewer_role}
                return release_safety_hold(conn,token,ticket,payload["expectedVersion"])
            result=execute_ticket_mutation(
                conn,token,ticket,operation,payload,
                request.headers.get("Idempotency-Key"),effect)
        except (TransitionConflict,psycopg.errors.UniqueViolation):
            conn.rollback()
            return JSONResponse(status_code=409,content={
                "code":"CONFLICT","requestId":request.state.request_id,
            },headers={"Cache-Control":"no-store"})
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@app.post("/api/v1/dev/tickets/{ticket_id}/safety-review", include_in_schema=False)
async def dev_ticket_safety_review(ticket_id: str, request: Request):
    return await _safety_mutation(request,ticket_id,"review")


@app.post("/api/v1/dev/tickets/{ticket_id}/safety-release", include_in_schema=False)
async def dev_ticket_safety_release(ticket_id: str, request: Request):
    return await _safety_mutation(request,ticket_id,"release")


@app.post("/api/v1/dev/estimates/{estimate_id}/decision", include_in_schema=False)
async def dev_estimate_decision(estimate_id: str, request: Request, public_decision=None):
    """Synthetic-only owner approval; durable idempotency with live authorization."""
    import os
    from urllib.parse import urlsplit
    from uuid import UUID
    import psycopg
    from .auth_http import _origin_ok
    from .session_service import verify_csrf
    from .estimate_decision import decide_estimate, EstimateConflict, EstimateForbidden

    if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API") != "1"
            or request.url.scheme != "https"
            or request.url.hostname not in ("127.0.0.1", "localhost")):
        return not_found(request)
    dsn = os.getenv("ROBOport_DATABASE_URL", "")
    try:
        parsed = urlsplit(dsn)
        if (parsed.scheme not in ("postgresql", "postgresql+psycopg")
                or parsed.hostname != "127.0.0.1" or parsed.port is None):
            return not_found(request)
        ident = UUID(estimate_id)
    except ValueError:
        return not_found(request)
    token = request.cookies.get("rp_session", "")
    if not token:
        return JSONResponse(status_code=401,content={
            "code":"UNAUTHENTICATED","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    if not _origin_ok(request):
        return JSONResponse(status_code=403,content={
            "code":"FORBIDDEN","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    try:
        payload = await request.json()
        if public_decision is not None:
            if (not isinstance(payload,dict)
                    or type(payload.get("estimateVersion")) is not int
                    or (public_decision=="APPROVED"
                        and payload.get("approvalMethod")!="verified_account")):
                raise ValueError()
            if public_decision=="DECLINED":
                reason=payload.get("reason")
                if reason is not None and (not isinstance(reason,str) or len(reason)>1000):
                    raise ValueError()
            else:
                reason=None
            payload={"expectedVersion":payload["estimateVersion"],
                     "decision":public_decision,"reason":reason}
        key = request.headers.get("Idempotency-Key")
        if (not isinstance(payload,dict)
                or type(payload.get("expectedVersion")) is not int
                or payload.get("decision") not in ("APPROVED","DECLINED")
                or not isinstance(key,str) or not 1 <= len(key) <= 128
                or not key.isascii() or not key.isprintable()):
            raise ValueError()
    except (ValueError,TypeError):
        return JSONResponse(status_code=422,content={
            "code":"INVALID_INPUT","requestId":request.state.request_id,
        },headers={"Cache-Control":"no-store"})
    with psycopg.connect(dsn.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        if not verify_csrf(conn,token,request.headers.get("x-csrf-token","")):
            return JSONResponse(status_code=403,content={
                "code":"FORBIDDEN","requestId":request.state.request_id,
            },headers={"Cache-Control":"no-store"})
        try:
            result=decide_estimate(conn,token,ident,payload["expectedVersion"],payload["decision"],key,
                                   reason=payload.get("reason"))
            if public_decision is not None:
                quote=conn.execute("SELECT ticket_id,total_minor FROM estimate WHERE id=%s",(ident,)).fetchone()
                result={"id":str(ident),"ticketId":str(quote[0]),"version":result["version"],
                        "status":result["state"],"total":f"{quote[1]//100}.{quote[1]%100:02d}"}
        except EstimateForbidden:
            return not_found(request)
        except (EstimateConflict,psycopg.errors.RaiseException,psycopg.errors.UniqueViolation):
            conn.rollback()
            return JSONResponse(status_code=409,content={
                "code":"CONFLICT","requestId":request.state.request_id,
            },headers={"Cache-Control":"no-store"})
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


async def _estimate_staff_mutation(request: Request, target_id: str, operation: str,
                                   contract=False):
    """Private localhost-only synthetic quote drafting/sending."""
    import os
    from urllib.parse import urlsplit
    from uuid import UUID
    import psycopg
    from .auth_http import _origin_ok
    from .session_service import verify_csrf
    from .estimate_decision import (
        create_estimate_version,send_estimate_version,staff_idempotent,EstimateForbidden,EstimateConflict)
    if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API")!="1"
            or request.url.scheme!="https"
            or request.url.hostname not in ("127.0.0.1","localhost")):
        return not_found(request)
    dsn=os.getenv("ROBOport_DATABASE_URL","")
    try:
        parsed=urlsplit(dsn)
        if (parsed.scheme not in ("postgresql","postgresql+psycopg")
                or parsed.hostname!="127.0.0.1" or parsed.port is None):
            return not_found(request)
        target=UUID(target_id)
    except ValueError:
        return not_found(request)
    token=request.cookies.get("rp_session","")
    if not token:
        return JSONResponse(status_code=401,content={"code":"UNAUTHENTICATED",
                            "requestId":request.state.request_id})
    if not _origin_ok(request):
        return JSONResponse(status_code=403,content={"code":"FORBIDDEN",
                            "requestId":request.state.request_id})
    try:
        if contract and operation=="send":
            payload={}
        else:
            payload=await request.json()
        if not isinstance(payload,dict):
            raise ValueError()
        if contract and operation=="create":
            from decimal import Decimal, InvalidOperation
            if type(payload.get("expectedTicketVersion")) is not int or payload.get("currency")!="RUB":
                raise ValueError()
            lines=[]
            for item in payload.get("lines",[]):
                import re
                if (not isinstance(item,dict) or item.get("kind") not in ("labor","logistics")
                        or not isinstance(item.get("description"),str)
                        or not item["description"].strip()
                        or not isinstance(item.get("unitPrice"),str)
                        or re.fullmatch(r"[0-9]+(\.[0-9]{1,2})?",item["unitPrice"]) is None):
                    # Parts require a verified compatibility registry and cannot
                    # be quoted from untrusted P/N or client-supplied evidence.
                    raise ValueError()
                try:
                    price=Decimal(item["unitPrice"])
                    quantity=Decimal(str(item["quantity"]))
                    minor=price*quantity*100
                except (KeyError,InvalidOperation,TypeError,ValueError):
                    raise ValueError()
                if (not quantity.is_finite() or quantity<=0
                        or not minor.is_finite() or minor!=minor.to_integral_value()):
                    raise ValueError()
                lines.append({"description":item["description"],"amountMinor":int(minor)})
            payload={"lines":lines,"expectedTicketVersion":payload["expectedTicketVersion"]}
        if operation=="send" and not contract and type(payload.get("expectedVersion")) is not int:
            raise ValueError()
        if operation=="create" and not isinstance(payload.get("lines"),list):
            raise ValueError()
    except (ValueError,TypeError):
        return JSONResponse(status_code=422,content={"code":"INVALID_INPUT",
                            "requestId":request.state.request_id})
    with psycopg.connect(dsn.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        if not verify_csrf(conn,token,request.headers.get("x-csrf-token","")):
            return JSONResponse(status_code=403,content={"code":"FORBIDDEN",
                                "requestId":request.state.request_id})
        try:
            if contract and operation=="send":
                sent=conn.execute("SELECT version FROM estimate WHERE id=%s",(target,)).fetchone()
                if sent is None:
                    return not_found(request)
                payload={"expectedVersion":sent[0]}
            result=staff_idempotent(
                conn,token,target,operation,payload,request.headers.get("Idempotency-Key"),
                lambda: create_estimate_version(conn,token,target,payload["lines"],
                          expected_ticket_version=payload.get("expectedTicketVersion"))
                if operation=="create" else
                send_estimate_version(conn,token,target,payload["expectedVersion"]))
            if contract:
                quote=conn.execute("SELECT ticket_id,currency,total_minor FROM estimate WHERE id=%s",(result["id"],)).fetchone()
                result={"id":result["id"],"ticketId":str(quote[0]),"version":result["version"],
                        "status":result["state"],"total":f"{quote[2]//100}.{quote[2]%100:02d}",
                        "currency":quote[1].strip()}
        except EstimateForbidden:
            return not_found(request)
        except (EstimateConflict,psycopg.errors.RaiseException,psycopg.errors.UniqueViolation):
            conn.rollback()
            return JSONResponse(status_code=409,content={"code":"CONFLICT",
                                "requestId":request.state.request_id})
        except ValueError:
            conn.rollback()
            return JSONResponse(status_code=422,content={"code":"INVALID_INPUT",
                                "requestId":request.state.request_id})
    return JSONResponse(content=result,status_code=201 if operation=="create" else 200,
                        headers={"Cache-Control":"no-store"})


@app.post("/api/v1/dev/tickets/{ticket_id}/estimates",include_in_schema=False)
async def dev_create_estimate(ticket_id: str, request: Request):
    return await _estimate_staff_mutation(request,ticket_id,"create")


@app.post("/api/v1/dev/estimates/{estimate_id}/send",include_in_schema=False)
async def dev_send_estimate(estimate_id: str, request: Request):
    return await _estimate_staff_mutation(request,estimate_id,"send")


@app.post("/api/v1/staff/tickets/{ticketId}/estimates",include_in_schema=False)
async def public_create_estimate(ticketId: str, request: Request):
    return await _estimate_staff_mutation(request,ticketId,"create",contract=True)


@app.post("/api/v1/staff/estimates/{estimateId}/send",include_in_schema=False)
async def public_send_estimate(estimateId: str, request: Request):
    return await _estimate_staff_mutation(request,estimateId,"send",contract=True)


@app.post("/api/v1/customer/estimates/{estimateId}/approve",include_in_schema=False)
async def public_approve_estimate(estimateId: str, request: Request):
    return await dev_estimate_decision(estimateId,request,public_decision="APPROVED")


@app.post("/api/v1/customer/estimates/{estimateId}/decline",include_in_schema=False)
async def public_decline_estimate(estimateId: str, request: Request):
    return await dev_estimate_decision(estimateId,request,public_decision="DECLINED")


@app.get("/api/v1/dev/catalog/models/exact",include_in_schema=False)
def dev_catalog_exact(request: Request,brand: str="",model: str="",variant: str="",
                      dock: str="",region: str="",revision: str=""):
    """Synthetic exact catalog lookup, no fuzzy compatibility inference."""
    import os
    import psycopg
    from urllib.parse import urlsplit
    from .catalog_approval import lookup_model
    raw=os.getenv("ROBOport_DATABASE_URL","")
    try:
        db=urlsplit(raw)
        if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API")!="1"
            or request.url.scheme!="https" or request.url.hostname not in ("127.0.0.1","localhost")
            or db.hostname!="127.0.0.1" or db.port is None
            or db.scheme not in ("postgresql","postgresql+psycopg")):
            return not_found(request)
        with psycopg.connect(raw.replace("postgresql+psycopg://","postgresql://",1)) as conn:
            result=lookup_model(conn,brand=brand,model=model,variant=variant,
                                dock=dock,region=region,revision=revision)
    except ValueError:
        return JSONResponse(status_code=422,content={"code":"INVALID_INPUT",
                            "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
    if result is None:
        return not_found(request)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@app.post("/api/v1/dev/catalog/capabilities/{capability_id}/review",include_in_schema=False)
async def dev_catalog_review(capability_id: str,request: Request,is_part: bool=False):
    import os
    from uuid import UUID
    import psycopg
    from urllib.parse import urlsplit
    from .auth_http import _origin_ok
    from .session_service import verify_csrf
    from .catalog_approval import review_idempotent,CatalogForbidden,CatalogConflict
    raw=os.getenv("ROBOport_DATABASE_URL","")
    try:
        db=urlsplit(raw)
        if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API")!="1"
            or request.url.scheme!="https" or request.url.hostname not in ("127.0.0.1","localhost")
            or db.hostname!="127.0.0.1" or db.port is None
            or db.scheme not in ("postgresql","postgresql+psycopg")):
            return not_found(request)
        capability=UUID(capability_id)
        data=await request.json()
        if not isinstance(data,dict) or type(data.get("approved")) is not bool:
            raise ValueError()
    except (ValueError,TypeError,AttributeError):
        return JSONResponse(status_code=422,content={"code":"INVALID_INPUT",
                            "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
    token=request.cookies.get("rp_session","")
    if not token:
        return JSONResponse(status_code=401,content={"code":"UNAUTHENTICATED",
                            "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
    if not _origin_ok(request):
        return JSONResponse(status_code=403,content={"code":"FORBIDDEN",
                            "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
    with psycopg.connect(raw.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        if not verify_csrf(conn,token,request.headers.get("x-csrf-token","")):
            return JSONResponse(status_code=403,content={"code":"FORBIDDEN",
                                "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
        try:
            result=review_idempotent(conn,token,capability,
                 "part" if is_part else "capability",data["approved"],
                 request.headers.get("Idempotency-Key"))
        except CatalogForbidden:
            return not_found(request)
        except ValueError:
            conn.rollback()
            return JSONResponse(status_code=422,content={"code":"INVALID_INPUT",
                                "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
        except (CatalogConflict,psycopg.errors.UniqueViolation):
            conn.rollback()
            return JSONResponse(status_code=409,content={"code":"CONFLICT",
                                "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@app.post("/api/v1/dev/catalog/parts/{part_id}/review",include_in_schema=False)
async def dev_part_review(part_id: str,request: Request):
    return await dev_catalog_review(part_id,request,is_part=True)

async def _inventory_http(request: Request, operation: str, reservation_id: str = "",
                          contract: bool = False):
    """Синтетический склад: HTTPS loopback, session, Origin, CSRF, live grants."""
    import os
    from urllib.parse import urlsplit
    from uuid import UUID
    import psycopg
    from .auth_http import _origin_ok
    from .session_service import verify_csrf
    from .inventory import reserve, cancel, StockForbidden, StockConflict
    dsn=os.getenv("ROBOport_DATABASE_URL","")
    if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API")!="1"
            or request.url.scheme!="https" or request.url.hostname not in ("127.0.0.1","localhost")):
        return not_found(request)
    try:
        parsed=urlsplit(dsn)
        if parsed.scheme not in ("postgresql","postgresql+psycopg") or parsed.hostname!="127.0.0.1" or parsed.port is None:
            return not_found(request)
        payload=await request.json() if operation=="reserve" or contract else {}
        key=request.headers.get("Idempotency-Key")
        if not isinstance(key,str) or not 1<=len(key)<=128 or not key.isascii() or not key.isprintable():
            raise ValueError()
        if operation=="reserve":
            if type(payload.get("quantity")) is not int:
                raise ValueError()
            ticket,lot=UUID(payload["ticketId"]),UUID(payload["lotId"])
            part_id=UUID(payload["partId"]) if contract else None
            ticket_version=payload["expectedTicketVersion"] if contract else None
            if contract and (type(ticket_version) is not int or ticket_version<1):
                raise ValueError()
        else:
            rid=UUID(reservation_id)
            reason=payload.get("reason") if contract else None
            if contract and (not isinstance(reason,str) or len(reason.strip())<3 or len(reason)>1000):
                raise ValueError()
    except (ValueError,TypeError,KeyError,AttributeError):
        return JSONResponse(status_code=422,content={"code":"INVALID_INPUT",
                            "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
    token=request.cookies.get("rp_session","")
    if not token:
        return JSONResponse(status_code=401,content={"code":"UNAUTHENTICATED",
                            "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
    if not _origin_ok(request):
        return JSONResponse(status_code=403,content={"code":"FORBIDDEN",
                            "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
    with psycopg.connect(dsn.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        if not verify_csrf(conn,token,request.headers.get("x-csrf-token","")):
            return JSONResponse(status_code=403,content={"code":"FORBIDDEN",
                                "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
        try:
            result=(reserve(conn,token,ticket,lot,payload["quantity"],key,
                            part_id=part_id,expected_ticket_version=ticket_version)
                    if operation=="reserve" else cancel(conn,token,rid,key,reason=reason))
            if contract:
                result={"id":result["id"],"status":result["status"]}
        except StockForbidden:
            return not_found(request)
        except (StockConflict,psycopg.errors.CheckViolation):
            conn.rollback()
            return JSONResponse(status_code=409,content={"code":"CONFLICT",
                                "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
        except ValueError:
            conn.rollback()
            return JSONResponse(status_code=422,content={"code":"INVALID_INPUT",
                                "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@app.post("/api/v1/dev/inventory/reservations",include_in_schema=False)
async def dev_inventory_reserve(request: Request):
    return await _inventory_http(request,"reserve")


@app.post("/api/v1/dev/inventory/reservations/{reservation_id}/cancel",include_in_schema=False)
async def dev_inventory_cancel(reservation_id: str,request: Request):
    return await _inventory_http(request,"cancel",reservation_id)


@app.post("/api/v1/staff/stock/reservations",include_in_schema=False)
async def contract_inventory_reserve(request: Request):
    return await _inventory_http(request,"reserve",contract=True)


@app.post("/api/v1/staff/stock/reservations/{reservationId}/release",include_in_schema=False)
async def contract_inventory_release(reservationId: str,request: Request):
    return await _inventory_http(request,"cancel",reservationId,contract=True)


@app.get("/api/v1/dev/account/tickets/{ticket_id}/timeline",include_in_schema=False)
def dev_customer_ticket_timeline(ticket_id: str,request: Request):
    """Dev-only history. The live session and ownership are checked by the same SQL query."""
    import os
    from urllib.parse import urlsplit
    from uuid import UUID
    import psycopg
    from .customer_timeline import owned_ticket_timeline
    raw=os.getenv("ROBOport_DATABASE_URL","")
    try:
        dsn=urlsplit(raw)
        ticket=UUID(ticket_id)
        if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API")!="1"
            or request.url.scheme!="https"
            or request.url.hostname not in ("localhost","127.0.0.1")
            or dsn.scheme not in ("postgresql","postgresql+psycopg")
            or dsn.hostname!="127.0.0.1" or dsn.port is None):
            return not_found(request)
    except ValueError:
        return not_found(request)
    token=request.cookies.get("rp_session","")
    if not token:
        return JSONResponse(status_code=401,content={"code":"UNAUTHENTICATED",
             "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
    with psycopg.connect(raw.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        events=owned_ticket_timeline(conn,token,ticket)
    if events is None:
        return not_found(request)
    return JSONResponse(content={"items":events},headers={"Cache-Control":"no-store"})


@app.get("/api/v1/dev/account/tickets",include_in_schema=False)
def dev_customer_account_tickets(request: Request,limit: int=20,offset: int=0):
    """Account read is session-scoped; no client-supplied identity or customer ID."""
    import os
    from urllib.parse import urlsplit
    import psycopg
    from .ticket_read import owned_ticket_list
    raw=os.getenv("ROBOport_DATABASE_URL","")
    try:
        dsn=urlsplit(raw)
        if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API")!="1"
            or request.url.scheme!="https"
            or request.url.hostname not in ("localhost","127.0.0.1")
            or dsn.scheme not in ("postgresql","postgresql+psycopg")
            or dsn.hostname!="127.0.0.1" or dsn.port is None):
            return not_found(request)
    except ValueError:
        return not_found(request)
    if type(limit) is not int or not 1<=limit<=50 or type(offset) is not int or not 0<=offset<=10000:
        return JSONResponse(status_code=422,content={"code":"INVALID_INPUT",
            "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
    with psycopg.connect(raw.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        rows=owned_ticket_list(conn,request.cookies.get("rp_session",""),
                               limit=limit,offset=offset)
    if rows is None:
        return JSONResponse(status_code=401,content={"code":"UNAUTHENTICATED",
            "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
    return JSONResponse(content={"items":rows,"limit":limit,"offset":offset},
                        headers={"Cache-Control":"no-store"})


@app.get("/api/v1/dev/account/summary/{section}",include_in_schema=False)
def dev_account_summary(section: str,request: Request):
    """Owner-scoped read-only synthetic summary; never return object keys or prior-owner PII."""
    import os
    from urllib.parse import urlsplit
    import psycopg
    from .session_lookup import session_subject
    if section not in ("robots","estimates","documents"):
        return not_found(request)
    raw=os.getenv("ROBOport_DATABASE_URL","")
    try:
        dsn=urlsplit(raw)
        if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API")!="1"
                or request.url.scheme!="https"
                or request.url.hostname not in ("127.0.0.1","localhost")
                or dsn.scheme not in ("postgresql","postgresql+psycopg")
                or dsn.hostname!="127.0.0.1" or dsn.port is None):
            return not_found(request)
    except ValueError:
        return not_found(request)
    queries={
        "robots": """SELECT DISTINCT d.id::text AS id,d.serial_number AS label
            FROM account_customer_link l JOIN service_ticket t ON t.customer_id=l.customer_id
            JOIN robot_device d ON d.id=t.device_id
            WHERE l.user_id=%s AND l.revoked_at IS NULL ORDER BY id LIMIT 50""",
        "estimates": """SELECT e.id::text AS id,e.state AS status,e.version,
            e.total_minor,e.currency
            FROM account_customer_link l JOIN service_ticket t ON t.customer_id=l.customer_id
            JOIN estimate e ON e.ticket_id=t.id
            WHERE l.user_id=%s AND l.revoked_at IS NULL
              AND e.state IN ('SENT','APPROVED','DECLINED')
            ORDER BY e.version DESC,e.id LIMIT 50""",
        "documents": """SELECT a.id::text AS id,a.mime,a.byte_size
            FROM account_customer_link l JOIN service_ticket t ON t.customer_id=l.customer_id
            JOIN attachment a ON a.ticket_id=t.id
            WHERE l.user_id=%s AND l.revoked_at IS NULL
              AND a.upload_state='READY' AND a.quarantine_state='CLEAR'
            ORDER BY a.created_at DESC,a.id LIMIT 50""",
    }
    with psycopg.connect(raw.replace("postgresql+psycopg://","postgresql://",1)) as conn:
        actor=session_subject(conn,request.cookies.get("rp_session",""))
        if actor is None:
            return JSONResponse(status_code=401,content={"code":"UNAUTHENTICATED",
                "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
        allowed=conn.execute("""SELECT EXISTS (SELECT 1 FROM user_role
            WHERE user_id=%s AND role_code='CLIENT' AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at>now()))""",(actor,)).fetchone()[0]
        if not allowed:
            return not_found(request)
        cursor=conn.execute(queries[section],(actor,))
        columns=[field.name for field in cursor.description]
        rows=[dict(zip(columns,row)) for row in cursor.fetchall()]
        # Opaque summaries; no object_key, sha256, email, tokens, or historical ownership.
        for row in rows:
            if "currency" in row:
                row["currency"]=row["currency"].strip()
    return JSONResponse(content={"items":rows},headers={"Cache-Control":"no-store"})


async def _customer_notification_preferences(request: Request, write: bool):
    """Current-client in-app preference; never dispatch external messages."""
    import os
    from uuid import uuid4
    import psycopg
    from .auth_http import _dsn,_origin_ok
    from .session_lookup import session_subject
    from .session_service import verify_csrf
    dsn=_dsn(request)
    if dsn is None or os.getenv("ROBOport_SYNTHETIC_ACCESS_API")!="1":
        return not_found(request)
    desired=None
    if write:
        if not _origin_ok(request):
            return JSONResponse(status_code=403,content={"code":"FORBIDDEN",
                    "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
        try:
            data=await request.json()
            if not isinstance(data,dict) or type(data.get("inAppEnabled")) is not bool:
                raise ValueError()
            desired=data["inAppEnabled"]
        except (ValueError,TypeError):
            return JSONResponse(status_code=422,content={"code":"INVALID_INPUT",
                "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
    token=request.cookies.get("rp_session","")
    with psycopg.connect(dsn) as conn:
        actor=session_subject(conn,token)
        if actor is None:
            return JSONResponse(status_code=401,content={"code":"UNAUTHENTICATED",
                "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
        allowed=conn.execute("""SELECT EXISTS(
          SELECT 1 FROM user_role WHERE user_id=%s AND role_code='CLIENT'
            AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>now())
        )""",(actor,)).fetchone()[0]
        if not allowed:
            return not_found(request)
        if write and not verify_csrf(conn,token,request.headers.get("x-csrf-token","")):
            return JSONResponse(status_code=403,content={"code":"FORBIDDEN",
                "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})
        row=conn.execute("""SELECT in_app_enabled FROM customer_notification_preference
          WHERE user_id=%s FOR UPDATE""",(actor,)).fetchone()
        before=row[0] if row is not None else True
        if write and before!=desired:
            conn.execute("""INSERT INTO customer_notification_preference
                (user_id,in_app_enabled,updated_at) VALUES(%s,%s,now())
                ON CONFLICT(user_id) DO UPDATE SET in_app_enabled=excluded.in_app_enabled,
                updated_at=now()""",(actor,desired))
            conn.execute("""INSERT INTO customer_preference_audit(id,user_id,in_app_enabled)
                VALUES(%s,%s,%s)""",(uuid4(),actor,desired))
        result=desired if write else before
    return JSONResponse(content={"inAppEnabled":result},
                        headers={"Cache-Control":"no-store"})


@app.get("/api/v1/dev/account/preferences",include_in_schema=False)
async def dev_customer_preferences(request: Request):
    return await _customer_notification_preferences(request,False)


@app.post("/api/v1/dev/account/preferences",include_in_schema=False)
async def dev_update_customer_preferences(request: Request):
    return await _customer_notification_preferences(request,True)

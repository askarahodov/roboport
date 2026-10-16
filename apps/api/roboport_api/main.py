"""Синтетический API. Реальные заявки и приватные маршруты отключены."""
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="ROBOport development API", version="0.1.0")

from .auth_http import router as synthetic_auth_router
app.include_router(synthetic_auth_router)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    # Идентификатор создаётся сервером; клиентский X-Request-ID не доверенный.
    request.state.request_id = str(uuid4())
    # Dev tickets carry private session cookies. Reject insecure/non-loopback transport
    # before any handler can validate a token or connect to PostgreSQL.
    if request.url.path.startswith("/api/v1/dev/tickets"):
        if (request.url.scheme != "https"
                or request.url.hostname not in ("127.0.0.1", "localhost")):
            return not_found(request)
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

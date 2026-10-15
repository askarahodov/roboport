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
    from .ticket_read import TICKET_STATES, owned_ticket_keyset_page\n    from .keyset_cursor import decode_cursor, encode_cursor

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



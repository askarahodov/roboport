"""Синтетический API. Реальные заявки и приватные маршруты отключены."""
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="ROBOport development API", version="0.1.0")


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


@app.get("/api/v1/dev/tickets/{ticket_id}/access", include_in_schema=False)
def dev_ticket_access(ticket_id: str, request: Request):
    """Synthetic-only read probe, disabled without explicit opt-in and disposable DB."""
    import os
    from uuid import UUID
    import psycopg
    from .session_lookup import session_can_read_ticket

    if os.getenv("ROBOport_SYNTHETIC_ACCESS_API") != "1":
        return not_found(request)
    raw_dsn = os.getenv("ROBOport_DATABASE_URL", "")
    if not raw_dsn or "127.0.0.1" not in raw_dsn:
        return not_found(request)
    token = request.cookies.get("roboport_session")
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

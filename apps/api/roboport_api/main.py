"""Minimal dev bootstrap; never report authenticated services as implemented."""

from uuid import uuid4
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="ROBOport API (development)", version="0.1.0", docs_url="/internal-dev/docs")

class Meta(BaseModel):
    name: str = "ROBOport"
    version: str = "0.1.0"
    environment: str = "development"
    production_ready: bool = False

@app.middleware("http")
async def add_correlation(request: Request, call_next):
    # Always server-issued; do not trust user-controlled X-Request-ID.
    request.state.request_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Cache-Control"] = "no-store"
    return response

@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, exc: HTTPException):
    # Explicit safe messages; never echo user-supplied identifiers or body.
    text = {401:"Требуется вход",403:"Доступ запрещён",404:"Не найдено",503:"Сервис ещё не готов"}.get(exc.status_code,"Запрос отклонён")
    return JSONResponse(status_code=exc.status_code,content={"code":f"HTTP_{exc.status_code}","message":text,"requestId":request.state.request_id})

@app.get("/health/live", tags=["health"])
def live():
    return {"status":"alive"}

@app.get("/health/ready", tags=["health"])
def ready():
    # Deliberately false before real DB and IdP are wired. No pretend availability.
    raise HTTPException(status_code=503)

@app.get("/api/v1/meta", response_model=Meta, tags=["meta"])
def meta():
    return Meta()

@app.get("/api/v1/customer/me", tags=["not-implemented"], include_in_schema=False)
def customer_me():
    # Never return the historic synthetic fixture as genuine authenticated data.
    raise HTTPException(status_code=401)

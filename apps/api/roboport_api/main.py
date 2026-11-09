"""Синтетический API. Реальные заявки и приватные маршруты отключены."""
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Airmatik automotive development API", version="0.1.0")

from .auth_http import router as synthetic_auth_router
app.include_router(synthetic_auth_router)
from .oidc_http import router as oidc_router
app.include_router(oidc_router)
from .auto_vehicle_http import router as auto_vehicle_router
app.include_router(auto_vehicle_router)
from .auto_vehicle_claim_http import router as auto_vehicle_claim_router
app.include_router(auto_vehicle_claim_router)
from .auto_intake_delegation_http import router as auto_intake_delegation_router
app.include_router(auto_intake_delegation_router)
from .auto_org_staff_http import router as auto_org_staff_router
app.include_router(auto_org_staff_router)
from .auto_appointment_http import router as auto_appointment_router
app.include_router(auto_appointment_router)
from .auto_bay_occupancy import router as auto_bay_occupancy_router
app.include_router(auto_bay_occupancy_router)
from .auto_demo_finance_report import router as auto_demo_finance_report_router
app.include_router(auto_demo_finance_report_router)
from .auto_crm_leads_http import router as auto_crm_leads_router
app.include_router(auto_crm_leads_router)
from .auto_crm_followups_http import router as auto_crm_followups_router
app.include_router(auto_crm_followups_router)
from .auto_crm_inbox_http import router as auto_crm_inbox_router
app.include_router(auto_crm_inbox_router)
from .auto_crm_contact_choices_http import router as auto_crm_contact_choices_router
app.include_router(auto_crm_contact_choices_router)
from .auto_crm_funnel import router as auto_crm_funnel_router
app.include_router(auto_crm_funnel_router)
from .auto_stock_abc_xyz import router as auto_stock_abc_xyz_router
app.include_router(auto_stock_abc_xyz_router)
from .auto_opening_hours_http import router as auto_opening_hours_router
app.include_router(auto_opening_hours_router)
from .auto_stocktake_http import router as auto_stocktake_router
app.include_router(auto_stocktake_router)
from .auto_order_stock_read_http import router as auto_order_stock_read_router
app.include_router(auto_order_stock_read_router)
from .auto_order_customer_read_http import router as auto_order_customer_read_router
app.include_router(auto_order_customer_read_router)
from .auto_order_overview_http import router as auto_order_overview_router
app.include_router(auto_order_overview_router)
from .auto_order_financial_read_http import router as auto_order_financial_read_router
app.include_router(auto_order_financial_read_router)
from .auto_order_service_http import router as auto_order_service_router
app.include_router(auto_order_service_router)
from .auto_intake_act_http import router as auto_intake_act_router
app.include_router(auto_intake_act_router)
from .auto_workshop_qc_http import router as auto_workshop_qc_router
app.include_router(auto_workshop_qc_router)
from .auto_work_order_diagnostics_http import router as auto_work_order_diagnostics_router
app.include_router(auto_work_order_diagnostics_router)
from .auto_work_order_estimate_http import router as auto_work_order_estimate_router
app.include_router(auto_work_order_estimate_router)
from .auto_work_order_execution_http import router as auto_work_order_execution_router
app.include_router(auto_work_order_execution_router)
from .auto_work_order_assignment_http import router as auto_work_order_assignment_router
app.include_router(auto_work_order_assignment_router)
from .auto_work_order_additional_http import router as auto_work_order_additional_router
app.include_router(auto_work_order_additional_router)
from .auto_work_tariffs_http import router as auto_work_tariffs_router
app.include_router(auto_work_tariffs_router)
from .auto_work_complexes_http import router as auto_work_complexes_router
app.include_router(auto_work_complexes_router)
from .auto_demo_close_http import router as auto_demo_close_router
app.include_router(auto_demo_close_router)
from .auto_customer_orders_http import router as auto_customer_orders_router
app.include_router(auto_customer_orders_router)
from .auto_customer_booking_http import router as auto_customer_booking_router
app.include_router(auto_customer_booking_router)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    # Идентификатор создаётся сервером; клиентский X-Request-ID не доверенный.
    request.state.request_id = str(uuid4())
    # All synthetic dev endpoints must fail before DB/storage access on unsafe transports.
    # Apply to the exact /dev root and slash-delimited descendants, not lookalike paths.
    path = request.url.path
    # Old robot catalog is retired even for direct HTTP callers and methods.
    # Canonical 404 does not reveal the old entity identity or route table.
    if path == "/api/v1/catalog" or path.startswith("/api/v1/catalog/"):
        return not_found(request)
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
    # Закрытый dev-пилот по адресу прямого сокета, НЕ доверенная proxy-схема.
    # Сетевую квоту проверяем до сессии/CSRF и бизнес-операции; отдельная
    # транзакция PostgreSQL фиксирует расход даже при ответах 403/422.
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
    return {"service": "Airmatik", "environment": "development"}


# В рабочем приложении регистрируются только автомобильные маршруты и общая
# аутентификация. Исторический robot HTTP оставлен исключительно в тестовом архиве.

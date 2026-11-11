"""Синтетический API. Реальные заявки и приватные маршруты отключены."""
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="БоксРитм — автомобильный dev API", version="0.1.0")

from .auth_http import router as synthetic_auth_router
from .oidc_http import router as oidc_router
from .auto_vehicle_http import router as auto_vehicle_router
from .auto_vehicle_claim_http import router as auto_vehicle_claim_router
from .auto_intake_delegation_http import router as auto_intake_delegation_router
from .auto_org_staff_http import router as auto_org_staff_router
from .auto_appointment_http import router as auto_appointment_router
from .auto_bay_occupancy import router as auto_bay_occupancy_router
from .auto_demo_finance_report import router as auto_demo_finance_report_router
from .auto_treasury_http import router as auto_treasury_router
from .auto_treasury_cashbook import router as auto_treasury_cashbook_router
from .auto_crm_leads_http import router as auto_crm_leads_router
from .auto_crm_followups_http import router as auto_crm_followups_router
from .auto_crm_inbox_http import router as auto_crm_inbox_router
from .auto_crm_contact_choices_http import router as auto_crm_contact_choices_router
from .auto_crm_funnel import router as auto_crm_funnel_router
from .auto_crm_outcomes import router as auto_crm_outcomes_router
from .auto_mechanic_productivity import router as auto_mechanic_productivity_router
from .auto_stock_abc_xyz import router as auto_stock_abc_xyz_router
from .auto_sku_catalog_http import router as auto_sku_catalog_router
from .auto_opening_hours_http import router as auto_opening_hours_router
from .auto_stocktake_http import router as auto_stocktake_router
from .auto_supplier_purchase_http import router as auto_supplier_purchase_router
from .auto_order_stock_read_http import router as auto_order_stock_read_router
from .auto_order_customer_read_http import router as auto_order_customer_read_router
from .auto_order_overview_http import router as auto_order_overview_router
from .auto_order_financial_read_http import router as auto_order_financial_read_router
from .auto_order_service_http import router as auto_order_service_router
from .auto_intake_act_http import router as auto_intake_act_router
from .auto_workshop_qc_http import router as auto_workshop_qc_router
from .auto_work_order_diagnostics_http import router as auto_work_order_diagnostics_router
from .auto_work_order_estimate_http import router as auto_work_order_estimate_router
from .auto_work_order_execution_http import router as auto_work_order_execution_router
from .auto_work_order_assignment_http import router as auto_work_order_assignment_router
from .auto_work_order_additional_http import router as auto_work_order_additional_router
from .auto_work_tariffs_http import router as auto_work_tariffs_router
from .auto_work_complexes_http import router as auto_work_complexes_router
from .auto_demo_close_http import router as auto_demo_close_router
from .auto_customer_orders_http import router as auto_customer_orders_router
from .auto_customer_booking_http import router as auto_customer_booking_router
from .auto_public_booking_http import router as auto_public_booking_router



# Exclude two historical staff ticket views before mounting shared auth.
# Never mutate the entire live route table as a cleanup operation.
from fastapi import APIRouter
_current_auth_router = APIRouter(include_in_schema=False)
_RETIRED_STAFF_PATHS = {"/api/v1/dev/staff/queue", "/api/v1/dev/staff/audit"}
_current_auth_router.routes.extend(
    route for route in synthetic_auth_router.routes
    if route.path not in _RETIRED_STAFF_PATHS
)

# Register all routers after imports and direct route decorators.
app.include_router(_current_auth_router)
app.include_router(oidc_router)
app.include_router(auto_vehicle_router)
app.include_router(auto_vehicle_claim_router)
app.include_router(auto_intake_delegation_router)
app.include_router(auto_org_staff_router)
app.include_router(auto_appointment_router)
app.include_router(auto_bay_occupancy_router)
app.include_router(auto_demo_finance_report_router)
app.include_router(auto_treasury_router)
app.include_router(auto_treasury_cashbook_router)
app.include_router(auto_crm_leads_router)
app.include_router(auto_crm_followups_router)
app.include_router(auto_crm_inbox_router)
app.include_router(auto_crm_contact_choices_router)
app.include_router(auto_crm_funnel_router)
app.include_router(auto_crm_outcomes_router)
app.include_router(auto_mechanic_productivity_router)
app.include_router(auto_stock_abc_xyz_router)
app.include_router(auto_sku_catalog_router)
app.include_router(auto_opening_hours_router)
app.include_router(auto_stocktake_router)
app.include_router(auto_supplier_purchase_router)
app.include_router(auto_order_stock_read_router)
app.include_router(auto_order_customer_read_router)
app.include_router(auto_order_overview_router)
app.include_router(auto_order_financial_read_router)
app.include_router(auto_order_service_router)
app.include_router(auto_intake_act_router)
app.include_router(auto_workshop_qc_router)
app.include_router(auto_work_order_diagnostics_router)
app.include_router(auto_work_order_estimate_router)
app.include_router(auto_work_order_execution_router)
app.include_router(auto_work_order_assignment_router)
app.include_router(auto_work_order_additional_router)
app.include_router(auto_work_tariffs_router)
app.include_router(auto_work_complexes_router)
app.include_router(auto_demo_close_router)
app.include_router(auto_customer_orders_router)
app.include_router(auto_customer_booking_router)
app.include_router(auto_public_booking_router)



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
        content={"code": "NOT_FOUND", "message": "Ресурс не найден", "requestId": request_id},
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
    return {"service": "БоксРитм", "environment": "development"}


# Retired robot, ticket, catalog, attachment, estimate and inventory HTTP
# endpoints are intentionally not imported or mounted by the БоксРитм app.
# Their historical acceptance harness lives under tests/legacy_app.py only.


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

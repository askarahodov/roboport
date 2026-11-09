"""Only automotive-session routes from the shared authentication adapter.

The historical staff ticket queue and audit endpoints are intentionally not
registered on the Airmatik runtime. Legacy acceptance tests use tests.legacy_app.
"""
from fastapi import APIRouter

from .auth_http import router as source_router

_RETIRED = {
    "/api/v1/dev/staff/queue",
    "/api/v1/dev/staff/audit",
}

router = APIRouter(include_in_schema=False)
for route in source_router.routes:
    if route.path not in _RETIRED:
        router.routes.append(route)

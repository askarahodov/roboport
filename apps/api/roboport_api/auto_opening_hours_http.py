"""Dev-only opening schedule API for MANAGER and branch INTAKE readers."""
from datetime import date
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_opening_hours import (
    get_opening, set_opening, OpeningDenied, OpeningInvalid, OpeningConflict,
)

router = APIRouter(include_in_schema=False)


class OpeningWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weekday: int = Field(strict=True, ge=1, le=7)
    opensMinute: int = Field(strict=True, ge=0, le=1439)
    closesMinute: int = Field(strict=True, ge=1, le=1440)


class OpeningUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedVersion: int = Field(strict=True, ge=0, le=1000000000)
    windows: list[OpeningWindow] = Field(max_length=28)
    closedDates: list[str] = Field(default_factory=list, max_length=120)


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/opening-hours")
def read_opening(org_id: str, loc_id: str, request: Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(org_id),UUID(loc_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            data=get_opening(db,request.cookies.get("rp_session",""),org,loc)
        except OpeningDenied:
            return _response(request,403,"FORBIDDEN")
    return JSONResponse(content=data,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/opening-hours")
async def update_opening(org_id: str, loc_id: str, request: Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(org_id),UUID(loc_id)
        body=OpeningUpdate.model_validate(await request.json())
        dates=[]
        for entry in body.closedDates:
            if len(entry)!=10:
                raise ValueError("invalid closed date")
            parsed=date.fromisoformat(entry)
            if parsed.isoformat()!=entry:
                raise ValueError("noncanonical closed date")
            dates.append(parsed)
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            data=set_opening(
                db,request.cookies.get("rp_session",""),org,loc,
                body.expectedVersion,
                [window.model_dump() for window in body.windows],dates,
            )
        except OpeningInvalid:
            return _response(request,422,"INVALID_INPUT")
        except OpeningDenied:
            return _response(request,403,"FORBIDDEN")
        except (OpeningConflict,psycopg.errors.DeadlockDetected):
            return _response(request,409,"OPENING_POLICY_CONFLICT")
    return JSONResponse(content=data,headers={"Cache-Control":"no-store"})

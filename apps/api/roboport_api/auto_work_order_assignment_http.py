"""Closed HTTPS loopback routes for shift-aware mechanic assignment."""
from datetime import datetime
from uuid import UUID
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,ValidationError
from .auth_http import _mutation_ok
from .auto_org_authority import organization_role_allows
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_work_order_assignment import (
    AssignmentDenied,AssignmentConflict,assign_mechanic,respond_assignment,
    list_assignments,list_withdrawable_offers,withdraw_assignment,
    list_releasable_accepted,release_accepted_assignment,
)

router=APIRouter(include_in_schema=False)


class Offer(BaseModel):
    model_config=ConfigDict(extra="forbid")
    mechanicId:UUID
    startsAt:datetime
    endsAt:datetime
    requestKey:UUID


class Response(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID


def _scope(request,org_id,loc_id,order_id=None):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        ids=[UUID(org_id),UUID(loc_id)]
        if order_id is not None: ids.append(UUID(order_id))
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    return dsn,ids


def _error(request,exc):
    if isinstance(exc,AssignmentConflict):
        return _response(request,409,str(exc))
    if str(exc)=="INVALID_INPUT":
        return _response(request,422,"INVALID_INPUT")
    return _response(request,403,"FORBIDDEN")


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/assignments")
def queue(org_id:str,loc_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse): return scoped
    dsn,(org,loc)=scoped
    # Отдельная страница позволяет очистить любое число старых OFFERED,
    # не блокируя работу филиала ограничением суммарного списка.
    params=request.query_params.getlist("withdrawPage")
    if len(params)>1 or (params and (
        not params[0].isascii() or not params[0].isdecimal() or
        len(params[0])>4 or str(int(params[0]))!=params[0] or
        not 0<=int(params[0])<=1000
    )):
        return _response(request,422,"INVALID_INPUT")
    withdraw_page=int(params[0]) if params else 0
    release_params=request.query_params.getlist("releasePage")
    if len(release_params)>1 or (release_params and (
        not release_params[0].isascii() or not release_params[0].isdecimal() or
        len(release_params[0])>4 or
        str(int(release_params[0]))!=release_params[0] or
        not 0<=int(release_params[0])<=1000
    )):
        return _response(request,422,"INVALID_INPUT")
    release_page=int(release_params[0]) if release_params else 0
    with psycopg.connect(dsn) as db:
        try:
            # The same live tenant/branch role is rechecked on every GET.
            # canOffer is presentation-only; POST still verifies MANAGER.
            with db.transaction():
                token=request.cookies.get("rp_session","")
                items=list_assignments(db,token,org,loc)
                can_offer=organization_role_allows(
                    db,token,org,"MANAGER",loc,for_mutation=True)
                withdrawal=list_withdrawable_offers(
                    db,token,org,loc,page=withdraw_page) if can_offer else {
                        "items":[],"page":withdraw_page,"hasMore":False,
                    }
                released=list_releasable_accepted(
                    db,token,org,loc,page=release_page) if can_offer else {
                        "items":[],"page":release_page,"hasMore":False,
                    }
        except (AssignmentDenied,AssignmentConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content={"items":items,
                                 "withdrawable":withdrawal["items"],
                                 "withdrawPage":withdrawal["page"],
                                 "withdrawHasMore":withdrawal["hasMore"],
                                 "releasable":released["items"],
                                 "releasePage":released["page"],
                                 "releaseHasMore":released["hasMore"],
                                 "syntheticOnly":True,
                                 "organizationId":str(org),"locationId":str(loc),
                                 "canOffer":bool(can_offer),"releaseAllowed":False},
                        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/assignments")
async def offer(org_id:str,loc_id:str,order_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id,order_id)
    if isinstance(scoped,JSONResponse): return scoped
    dsn,(org,loc,order)=scoped
    try: payload=Offer.model_validate(await request.json())
    except (ValidationError,ValueError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db): return _response(request,403,"FORBIDDEN")
        try:
            data=assign_mechanic(db,request.cookies.get("rp_session",""),org,loc,
                order,payload.mechanicId,payload.startsAt,payload.endsAt,payload.requestKey)
        except (AssignmentDenied,AssignmentConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,content=data,
                        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/assignments/{assignment_id}/accept")
async def accept(org_id:str,loc_id:str,assignment_id:str,request:Request):
    return await _respond(org_id,loc_id,assignment_id,request,True)


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/assignments/{assignment_id}/decline")
async def decline(org_id:str,loc_id:str,assignment_id:str,request:Request):
    return await _respond(org_id,loc_id,assignment_id,request,False)


async def _respond(org_id,loc_id,assignment_id,request,accept):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse): return scoped
    dsn,(org,loc)=scoped
    try:
        assignment=UUID(assignment_id)
        payload=Response.model_validate(await request.json())
    except (ValidationError,ValueError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db): return _response(request,403,"FORBIDDEN")
        try:
            data=respond_assignment(db,request.cookies.get("rp_session",""),
                org,loc,assignment,payload.requestKey,accept)
        except (AssignmentDenied,AssignmentConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,content=data,
                        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/assignments/{assignment_id}/withdraw")
async def withdraw(org_id:str,loc_id:str,assignment_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse): return scoped
    dsn,(org,loc)=scoped
    try:
        ident=UUID(assignment_id)
        payload=Response.model_validate(await request.json())
    except (ValidationError,ValueError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db): return _response(request,403,"FORBIDDEN")
        try:
            data=withdraw_assignment(
                db,request.cookies.get("rp_session",""),
                org,loc,ident,payload.requestKey)
        except (AssignmentDenied,AssignmentConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,
                        content=data,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/assignments/{assignment_id}/release")
async def release(org_id:str,loc_id:str,assignment_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse): return scoped
    dsn,(org,loc)=scoped
    try:
        ident=UUID(assignment_id)
        payload=Response.model_validate(await request.json())
    except (ValidationError,ValueError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db): return _response(request,403,"FORBIDDEN")
        try:
            data=release_accepted_assignment(
                db,request.cookies.get("rp_session",""),
                org,loc,ident,payload.requestKey)
        except (AssignmentDenied,AssignmentConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,
                        content=data,headers={"Cache-Control":"no-store"})

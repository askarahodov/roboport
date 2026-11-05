"""Private, synthetic-only staff CRM inbox; no outbound communications."""
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .auto_vehicle_claim_http import _response
from .auto_crm_leads_http import _context, _error
from .auto_crm_leads import LeadDenied, LeadConflict
from .auto_crm_inbox import list_inbox

router=APIRouter(include_in_schema=False)


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/inbox")
def crm_inbox(org_id:str, loc_id:str, request:Request, cursor:str|None=None):
    scoped=_context(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,(org,loc)=scoped
    with psycopg.connect(dsn) as db:
        try:
            result=list_inbox(db,request.cookies.get("rp_session",""),org,loc,cursor)
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})

"""Synthetic serial identity -> accepted receipt attestation with independent review."""
from uuid import UUID, uuid4
import psycopg
from pydantic import BaseModel,ConfigDict,ValidationError
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from .auto_org_authority import organization_role_allows
from .auto_document_access import require_document_access,DocumentPolicyDenied
from .session_lookup import session_subject
from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn,_response

router=APIRouter(include_in_schema=False)
PREFIX="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/serial-receipt-provenance"


class Denied(ValueError):
    pass


class Conflict(ValueError):
    pass


class AssertBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    serialId:UUID
    receiptId:UUID


class ApproveBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID


def _authority(db,token,org,loc,role):
    if not organization_role_allows(db,token,org,role,loc,for_mutation=True):
        raise Denied()
    try:
        for cap in ("READ","PRICE_PURCHASE"):
            if require_document_access(db,token,org,loc,("MANAGER","WAREHOUSE"),
                                       "SUPPLIER_ORDER",cap) is not None:
                raise Denied()
    except DocumentPolicyDenied:
        raise Denied() from None
    actor=session_subject(db,token,for_mutation=True)
    if actor is None:
        raise Denied()
    return actor


def _state(db,org,loc):
    rows=db.execute("""
      SELECT p.id,p.serial_id,p.receipt_id,a.id,
             p.cost_registration_seq,a.cost_registration_seq
      FROM auto_stock_serial_receipt_assertion p
      JOIN auto_stock_receipt_document r ON r.id=p.receipt_id
      LEFT JOIN auto_stock_serial_receipt_approval a ON a.assertion_id=p.id
      WHERE r.organization_id=%s AND r.location_id=%s
      ORDER BY p.cost_registration_seq LIMIT 101
    """,(org,loc)).fetchall()
    if len(rows)>100:
        raise Conflict()
    return {"syntheticOnly":True,"accountingApproved":False,
            "items":[{"assertionId":str(x[0]),"serialId":str(x[1]),
                      "receiptId":str(x[2]),"approvalId":str(x[3]) if x[3] else None,
                      "assertionSequence":x[4],"approvalSequence":x[5]} for x in rows]}


def list_links(db,token,org,loc):
    with db.transaction():
        _authority(db,token,org,loc,"MANAGER")
        return _state(db,org,loc)


def propose(db,token,org,loc,serial,receipt,key):
    with db.transaction():
        actor=_authority(db,token,org,loc,"WAREHOUSE")
        # All checks execute again on retry; no pre-revocation replay leak.
        prior=db.execute("""
          SELECT p.id,p.serial_id,p.receipt_id,p.proposed_by
          FROM auto_stock_serial_receipt_assertion p WHERE p.request_key=%s
        """,(key,)).fetchone()
        if prior:
            if prior[1:]!=(serial,receipt,actor):
                raise Conflict()
            return {"assertionId":str(prior[0]),"replayed":True}
        scope=db.execute("""
          SELECT s.id FROM auto_stock_serial s
          JOIN auto_stock_lot l ON l.id=s.lot_id
          JOIN auto_stock_receipt_document d
            ON d.id=%s AND d.lot_id=l.id
              AND d.sku_id=l.sku_id AND d.organization_id=l.organization_id
              AND d.location_id=l.location_id
          JOIN auto_stock_receipt_acceptance a ON a.document_id=d.id
          WHERE s.id=%s AND l.organization_id=%s AND l.location_id=%s
          FOR SHARE OF s,l,d,a
        """,(receipt,serial,org,loc)).fetchone()
        if not scope:
            raise Denied()
        try:
            with db.transaction():
                db.execute("SELECT set_config('roboport.serial_receipt_actor',%s,true)",(str(actor),))
                ident=uuid4()
                db.execute("""
                  INSERT INTO auto_stock_serial_receipt_assertion(
                    id,serial_id,receipt_id,proposed_by,request_key,cost_registration_seq)
                  VALUES(%s,%s,%s,%s,%s,1)
                """,(ident,serial,receipt,actor,key))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise Conflict() from None
        finally:
            db.execute("SELECT set_config('roboport.serial_receipt_actor','',true)")
        return {"assertionId":str(ident),"replayed":False}


def approve(db,token,org,loc,assertion,key):
    with db.transaction():
        actor=_authority(db,token,org,loc,"MANAGER")
        row=db.execute("""
          SELECT p.id,p.proposed_by,a.id,a.approved_by,a.request_key
          FROM auto_stock_serial_receipt_assertion p
          JOIN auto_stock_receipt_document d ON d.id=p.receipt_id
          LEFT JOIN auto_stock_serial_receipt_approval a ON a.assertion_id=p.id
          WHERE p.id=%s AND d.organization_id=%s AND d.location_id=%s
        """,(assertion,org,loc)).fetchone()
        if row is None or row[1]==actor:
            raise Denied()
        if row[2]:
            if (row[3],row[4])!=(actor,key):
                raise Conflict()
            return {"approvalId":str(row[2]),"replayed":True}
        try:
            with db.transaction():
                db.execute("SELECT set_config('roboport.serial_receipt_actor',%s,true)",(str(actor),))
                ident=uuid4()
                db.execute("""
                  INSERT INTO auto_stock_serial_receipt_approval(
                    id,assertion_id,approved_by,request_key,cost_registration_seq)
                  VALUES(%s,%s,%s,%s,1)
                """,(ident,assertion,actor,key))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise Conflict() from None
        finally:
            db.execute("SELECT set_config('roboport.serial_receipt_actor','',true)")
        return {"approvalId":str(ident),"replayed":False}


def _handle(request,org_id,loc_id,fn,write=False):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(org_id),UUID(loc_id)
        with psycopg.connect(dsn) as db:
            if write and not _mutation_ok(request,db):
                return _response(request,403,"FORBIDDEN")
            result=fn(db,request.cookies.get("rp_session",""),org,loc)
    except Denied:
        return _response(request,403,"FORBIDDEN")
    except (Conflict,psycopg.errors.SerializationFailure,psycopg.errors.DeadlockDetected):
        return _response(request,409,"CONFLICT")
    except ValueError:
        return _response(request,422,"INVALID_INPUT")
    return JSONResponse(content=result,status_code=201 if write and not result["replayed"] else 200,
                        headers={"Cache-Control":"no-store"})


@router.get(PREFIX)
def view(org_id:str,loc_id:str,request:Request):
    return _handle(request,org_id,loc_id,list_links)


@router.post(PREFIX)
async def post_assertion(org_id:str,loc_id:str,request:Request):
    try:
        body=AssertBody.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    return _handle(request,org_id,loc_id,
                   lambda db,t,o,l:propose(db,t,o,l,body.serialId,body.receiptId,
                                            body.requestKey),write=True)


@router.post(PREFIX+"/{assertion_id}/approve")
async def post_approval(org_id:str,loc_id:str,assertion_id:str,request:Request):
    try:
        ident=UUID(assertion_id)
        body=ApproveBody.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    return _handle(request,org_id,loc_id,
                   lambda db,t,o,l:approve(db,t,o,l,ident,body.requestKey),write=True)

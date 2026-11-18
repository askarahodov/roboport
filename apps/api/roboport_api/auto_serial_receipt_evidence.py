"""Independent receipt attribution for individual serialized stock units (#448).

No serial numbers, user identities or supplier prices are returned via HTTP.
Only an accepted receipt in the serial's original lot can be attested.
"""
from __future__ import annotations

from uuid import UUID, uuid4
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from .auth_http import _mutation_ok
from .auto_org_authority import organization_role_allows
from .auto_stock_service import AutoStockDenied, AutoStockConflict
from .auto_vehicle_claim_http import _request_dsn, _response
from .session_lookup import session_subject

router=APIRouter(include_in_schema=False)
PREFIX="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/warehouse/serial-receipts"


class Assertion(BaseModel):
    model_config=ConfigDict(extra="forbid")
    serialId:UUID
    receiptId:UUID
    requestKey:UUID


class Approval(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID


def _role(conn,token,org,loc,role):
    if not organization_role_allows(conn,token,org,role,loc,for_mutation=True):
        raise AutoStockDenied("FORBIDDEN")
    actor=session_subject(conn,token,for_mutation=True)
    if actor is None:
        raise AutoStockDenied("FORBIDDEN")
    return actor


def _scope(conn,serial,org,loc):
    scope=conn.execute("""
       SELECT s.lot_id,s.sku_id FROM auto_stock_serial s
       WHERE s.id=%s AND s.organization_id=%s AND s.location_id=%s FOR SHARE
    """,(serial,org,loc)).fetchone()
    if scope is None:
        raise AutoStockDenied("FORBIDDEN")
    return scope


def _item(conn,ident):
    row=conn.execute("""
      SELECT s.id,s.serial_id,s.receipt_id,s.location_id,s.created_at,
             a.id,a.created_at
      FROM auto_stock_serial_receipt_assertion s
      LEFT JOIN auto_stock_serial_receipt_approval a ON a.assertion_id=s.id
      WHERE s.id=%s
    """,(ident,)).fetchone()
    if not row:
        raise AutoStockDenied("FORBIDDEN")
    return {
        "assertionId":str(row[0]),"serialId":str(row[1]),"receiptId":str(row[2]),
        "locationId":str(row[3]),"assertedAt":row[4].isoformat(),
        "approvalId":str(row[5]) if row[5] else None,
        "approvedAt":row[6].isoformat() if row[6] else None,
        "syntheticOnly":True,
    }


def assert_receipt(conn,token,org,loc,serial,receipt,key):
    with conn.transaction():
        actor=_role(conn,token,org,loc,"WAREHOUSE")
        _,sku=_scope(conn,serial,org,loc)
        conn.execute("SELECT id FROM auto_stock_sku WHERE id=%s FOR UPDATE",(sku,))
        prior=conn.execute("""
          SELECT id,serial_id,receipt_id,asserted_by FROM auto_stock_serial_receipt_assertion
          WHERE request_key=%s
        """,(key,)).fetchone()
        if prior:
            if prior[1:]!=(serial,receipt,actor):
                raise AutoStockConflict("CONFLICT")
            return False,_item(conn,prior[0])
        if conn.execute("""SELECT 1 FROM auto_stock_serial_receipt_assertion
            WHERE serial_id=%s""",(serial,)).fetchone():
            raise AutoStockConflict("CONFLICT")
        try:
            with conn.transaction():
                ident=uuid4()
                conn.execute("""
                  INSERT INTO auto_stock_serial_receipt_assertion
                    (id,serial_id,receipt_id,organization_id,location_id,asserted_by,request_key)
                  VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,(ident,serial,receipt,org,loc,actor,key))
        except (psycopg.errors.RaiseException,psycopg.errors.CheckViolation,
                psycopg.errors.UniqueViolation,psycopg.errors.ForeignKeyViolation):
            raise AutoStockConflict("CONFLICT") from None
        return True,_item(conn,ident)


def approve_receipt(conn,token,org,loc,ident,key):
    with conn.transaction():
        actor=_role(conn,token,org,loc,"MANAGER")
        target=conn.execute("""
          SELECT s.id,s.serial_id,s.asserted_by
          FROM auto_stock_serial_receipt_assertion s
          WHERE s.id=%s AND s.organization_id=%s AND s.location_id=%s
        """,(ident,org,loc)).fetchone()
        if not target:
            raise AutoStockDenied("FORBIDDEN")
        if actor==target[2]:
            raise AutoStockDenied("FORBIDDEN")
        _,sku=_scope(conn,target[1],org,loc)
        conn.execute("SELECT id FROM auto_stock_sku WHERE id=%s FOR UPDATE",(sku,))
        prior=conn.execute("""
          SELECT approved_by,request_key FROM auto_stock_serial_receipt_approval
          WHERE assertion_id=%s
        """,(ident,)).fetchone()
        if prior:
            if prior!=(actor,key):
                raise AutoStockConflict("CONFLICT")
            return False,_item(conn,ident)
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_stock_serial_receipt_approval
                    (id,assertion_id,approved_by,request_key)
                  VALUES(%s,%s,%s,%s)
                """,(uuid4(),ident,actor,key))
        except (psycopg.errors.RaiseException,psycopg.errors.CheckViolation,
                psycopg.errors.UniqueViolation,psycopg.errors.ForeignKeyViolation):
            raise AutoStockConflict("CONFLICT") from None
        return True,_item(conn,ident)


def list_receipts(conn,token,org,loc):
    with conn.transaction():
        if not (organization_role_allows(conn,token,org,"WAREHOUSE",loc,for_mutation=True)
                or organization_role_allows(conn,token,org,"MANAGER",loc,for_mutation=True)):
            raise AutoStockDenied("FORBIDDEN")
        rows=conn.execute("""
          SELECT s.id FROM auto_stock_serial_receipt_assertion s
          WHERE s.organization_id=%s AND s.location_id=%s
          ORDER BY s.created_at,s.id LIMIT 201
        """,(org,loc)).fetchall()
        if len(rows)>200:
            raise AutoStockConflict("LIMIT")
        return {"syntheticOnly":True,"items":[_item(conn,r[0]) for r in rows]}


def _run(request,org_id,loc_id,operation,*,write=False):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(org_id),UUID(loc_id)
    except ValueError:
        return _response(request,422,"INVALID_INPUT")
    try:
        with psycopg.connect(dsn) as conn:
            if write and not _mutation_ok(request,conn):
                return _response(request,403,"FORBIDDEN")
            result=operation(conn,request.cookies.get("rp_session",""),org,loc)
    except AutoStockDenied:
        return _response(request,403,"FORBIDDEN")
    except (AutoStockConflict,psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected):
        return _response(request,409,"CONFLICT")
    if write:
        created,item=result
        return JSONResponse(item,status_code=201 if created else 200,
                            headers={"Cache-Control":"no-store"})
    return JSONResponse(result,headers={"Cache-Control":"no-store"})


@router.get(PREFIX)
def get_serial_receipts(request:Request,org_id:str,loc_id:str):
    return _run(request,org_id,loc_id,list_receipts)


@router.post(PREFIX)
async def create_serial_receipt(request:Request,org_id:str,loc_id:str):
    try:
        data=Assertion.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    return _run(request,org_id,loc_id,lambda db,t,o,l:assert_receipt(
        db,t,o,l,data.serialId,data.receiptId,data.requestKey),write=True)


@router.post(PREFIX+"/{assertion_id}/approve")
async def approve_serial_receipt(request:Request,org_id:str,loc_id:str,assertion_id:str):
    try:
        ident=UUID(assertion_id)
        data=Approval.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    return _run(request,org_id,loc_id,lambda db,t,o,l:approve_receipt(
        db,t,o,l,ident,data.requestKey),write=True)

"""Регистрация нового автомобиля и правка до начала доказательства (#130).

Только synthetic: существующий VIN не даёт доступ к карточке или истории.
"""
from hashlib import sha256
import json
import re
from uuid import UUID, uuid4

import psycopg

from .auto_org_authority import organization_role_allows
from .auto_vehicle_claim import VehicleClaimRejected
from .auto_vehicle_link_intake import create_pending_vehicle_link, VehicleLinkConflict
from .session_lookup import session_subject


class RegistrationDenied(Exception):
    pass


class RegistrationConflict(Exception):
    pass


def _fields(make, model, variant, year):
    for value, maximum in ((make,100),(model,120),(variant,160)):
        if value is None and maximum==160:
            continue
        if (type(value) is not str or not 1<=len(value)<=maximum or value!=value.strip()
            or any(ord(c)<32 for c in value)):
            raise ValueError("INVALID_INPUT")
    if year is not None and (type(year) is not int or not 1886<=year<=2100):
        raise ValueError("INVALID_INPUT")
    return make,model,variant,year


def _actor(db,token,org,loc):
    if not organization_role_allows(db,token,org,"INTAKE",loc,for_mutation=True):
        raise RegistrationDenied()
    actor=session_subject(db,token,for_mutation=True)
    if actor is None:
        raise RegistrationDenied()
    return actor


def _eligible(db,customer):
    if not db.execute("""
      SELECT 1 FROM account_customer_link acl
      JOIN app_user u ON u.id=acl.user_id AND u.status='ACTIVE'
      JOIN user_role ur ON ur.user_id=u.id AND ur.role_code='CLIENT'
        AND ur.revoked_at IS NULL AND (ur.expires_at IS NULL OR ur.expires_at>now())
      WHERE acl.customer_id=%s AND acl.revoked_at IS NULL LIMIT 1 FOR SHARE OF acl,u,ur
    """,(customer,)).fetchone():
        raise RegistrationDenied()


def _digest(*payload):
    return sha256(json.dumps(payload,ensure_ascii=True,separators=(",",":"),default=str).encode()).digest()


def _prior(db,actor,request_id,org,loc,action,fingerprint):
    db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
               (f"vehicle-registration:{actor}:{request_id}",))
    row=db.execute("""
      SELECT organization_id,location_id,action,request_fingerprint,vehicle_id,link_id,revision
      FROM auto_vehicle_registration_event WHERE actor_id=%s AND request_id=%s
    """,(actor,request_id)).fetchone()
    if row is None:
        return None
    if row[:4]!=(org,loc,action,fingerprint):
        raise RegistrationConflict()
    return row[4:]


def _row(db,org,loc,vehicle):
    row=db.execute("""
      SELECT v.registration_revision,v.make,v.model,v.variant,v.model_year,
             right(v.vin,4),e.link_id,l.customer_id,l.verified_at,l.revoked_at
      FROM auto_vehicle v
      JOIN auto_vehicle_registration_event e ON e.vehicle_id=v.id AND e.revision=1
      JOIN auto_vehicle_location_binding b ON b.vehicle_id=v.id
      JOIN auto_vehicle_customer_link l ON l.id=e.link_id AND l.vehicle_id=v.id
      WHERE v.id=%s AND e.organization_id=%s AND e.location_id=%s AND b.location_id=%s
      FOR UPDATE OF v,l
    """,(vehicle,org,loc,loc)).fetchone()
    if row is None:
        raise RegistrationDenied()
    editable=(row[8] is None and row[9] is None and not db.execute("""
      SELECT 1 FROM auto_vehicle_customer_link l LEFT JOIN auto_vehicle_claim c ON c.link_id=l.id
      WHERE l.vehicle_id=%s AND (l.id<>%s OR c.id IS NOT NULL) LIMIT 1
    """,(vehicle,row[6])).fetchone())
    return row,editable


def _receipt(vehicle,link,revision):
    return {"vehicleId":str(vehicle),"linkId":str(link),"revision":revision,"status":"PENDING"}


def _event(db,actor,org,loc,vehicle,link,revision,action,key,fingerprint,fields):
    db.execute("""
      INSERT INTO auto_vehicle_registration_event
        (id,actor_id,organization_id,location_id,vehicle_id,link_id,revision,action,
         request_id,request_fingerprint,metadata_digest)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
        sha256(convert_to(jsonb_build_array(%s::text,%s::text,%s::text,%s::integer)::text,'UTF8')))
    """,(uuid4(),actor,org,loc,vehicle,link,revision,action,key,fingerprint,*fields))
    db.execute("""
      UPDATE auto_vehicle SET make=%s,model=%s,variant=%s,model_year=%s,registration_revision=%s
      WHERE id=%s
    """,(*fields,revision,vehicle))
    # Проверяем deferred связь события и карточки до возврата HTTP.
    db.execute("SET CONSTRAINTS auto_vehicle_registration_applied IMMEDIATE")
    db.execute("SET CONSTRAINTS auto_vehicle_registration_applied DEFERRED")


def register_vehicle(db,token,org,loc,customer,key,*,vin,make,model,variant=None,year=None):
    fields=_fields(make,model,variant,year)
    if (any(type(x) is not UUID for x in (org,loc,customer,key))
        or (vin is not None and (type(vin) is not str or not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}",vin)))):
        raise ValueError("INVALID_INPUT")
    fingerprint=_digest(org,loc,customer,vin,*fields)
    with db.transaction():
        actor=_actor(db,token,org,loc)
        _eligible(db,customer)
        prior=_prior(db,actor,key,org,loc,"CREATE",fingerprint)
        if prior:
            vehicle,link,revision=prior
            row,editable=_row(db,org,loc,vehicle)
            if not editable or row[0]!=revision or row[6]!=link or row[7]!=customer:
                raise RegistrationConflict()
            return _receipt(vehicle,link,revision)
        vehicle=uuid4()
        try:
            with db.transaction():
                db.execute("""
                  INSERT INTO auto_vehicle(id,vin,make,model,variant,model_year)
                  VALUES(%s,%s,%s,%s,%s,%s)
                """,(vehicle,vin,*fields))
                db.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
                           (vehicle,loc))
                link=create_pending_vehicle_link(db,token,vehicle,customer)
                _event(db,actor,org,loc,vehicle,link,1,"CREATE",key,fingerprint,fields)
        except VehicleClaimRejected:
            raise RegistrationDenied() from None
        except (VehicleLinkConflict,psycopg.errors.UniqueViolation,psycopg.errors.RaiseException,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise RegistrationConflict() from None
        return _receipt(vehicle,link,1)


def edit_vehicle(db,token,org,loc,vehicle,key,*,expected_revision,make,model,variant=None,year=None):
    fields=_fields(make,model,variant,year)
    if (any(type(x) is not UUID for x in (org,loc,vehicle,key))
        or type(expected_revision) is not int or not 1<=expected_revision<1000000):
        raise ValueError("INVALID_INPUT")
    fingerprint=_digest(org,loc,vehicle,expected_revision,*fields)
    with db.transaction():
        actor=_actor(db,token,org,loc)
        prior=_prior(db,actor,key,org,loc,"EDIT",fingerprint)
        row,editable=_row(db,org,loc,vehicle)
        _eligible(db,row[7])
        if not editable:
            raise RegistrationConflict()
        if prior:
            if prior!=(vehicle,row[6],row[0]):
                raise RegistrationConflict()
            return _receipt(vehicle,row[6],row[0])
        if expected_revision!=row[0] or fields==row[1:5]:
            raise RegistrationConflict()
        try:
            with db.transaction():
                _event(db,actor,org,loc,vehicle,row[6],row[0]+1,"EDIT",key,fingerprint,fields)
        except (psycopg.errors.UniqueViolation,psycopg.errors.RaiseException,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise RegistrationConflict() from None
        return _receipt(vehicle,row[6],row[0]+1)


def registration_details(db,token,org,loc,vehicle):
    if any(type(x) is not UUID for x in (org,loc,vehicle)):
        raise ValueError("INVALID_INPUT")
    with db.transaction():
        _actor(db,token,org,loc)
        row,editable=_row(db,org,loc,vehicle)
        return {"vehicleId":str(vehicle),"revision":row[0],"make":row[1],"model":row[2],
                "variant":row[3],"modelYear":row[4],"vinLast4":row[5],"editable":editable}

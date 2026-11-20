"""Закрытый registry write: создать филиальный автомобиль и pending link, редактировать под MANAGER."""

import re
from uuid import UUID, uuid4

import psycopg

from .session_lookup import session_subject
from .auto_org_authority import organization_role_allows
from .auto_vehicle_org_scope import vehicle_organization_scope
from .auto_vehicle_link_intake import (
    create_pending_vehicle_link, VehicleLinkConflict,
)
from .auto_create_idempotency import (
    existing, remember, AutoIdempotencyConflict, validated_key,
)


_VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_COLUMNS = {
    "vin": "vin",
    "make": "make",
    "model": "model",
    "variant": "variant",
    "modelYear": "model_year",
}


class VehicleRegistryDenied(ValueError):
    """Нет активной сессии или полномочия точной организации/филиала."""


class VehicleRegistryConflict(Exception):
    """Конфликт VIN, активной связи или устаревшего idempotency replay."""


def _normalize_fields(values: dict, *, create: bool) -> dict:
    if type(values) is not dict:
        raise ValueError("invalid vehicle fields")
    allowed = set(_COLUMNS)
    if set(values) - allowed:
        raise ValueError("unknown vehicle field")
    if create and not {"make", "model"} <= set(values):
        raise ValueError("make and model required")
    if not create and not values:
        raise ValueError("empty vehicle update")

    result = {}
    for key, value in values.items():
        if key in ("make", "model"):
            if type(value) is not str:
                raise ValueError("invalid vehicle text")
            normalized = value.strip()
            limit = 100 if key == "make" else 120
            if not 1 <= len(normalized) <= limit:
                raise ValueError("invalid vehicle text")
            result[key] = normalized
        elif key == "variant":
            if value is None:
                result[key] = None
            elif type(value) is str:
                normalized = value.strip()
                if not 1 <= len(normalized) <= 160:
                    raise ValueError("invalid vehicle variant")
                result[key] = normalized
            else:
                raise ValueError("invalid vehicle variant")
        elif key == "vin":
            if value is None:
                result[key] = None
            elif type(value) is str:
                normalized = value.strip().upper()
                if not _VIN.fullmatch(normalized):
                    raise ValueError("invalid synthetic VIN")
                result[key] = normalized
            else:
                raise ValueError("invalid synthetic VIN")
        elif key == "modelYear":
            if value is None:
                result[key] = None
            elif type(value) is int and 1886 <= value <= 2100:
                result[key] = value
            else:
                raise ValueError("invalid model year")
    return result


def _fingerprint_value(value) -> str:
    return "<NULL>" if value is None else str(value)


def _create_payload(
    organization_id: UUID, location_id: UUID, customer_id: UUID, fields: dict,
) -> dict[str, str]:
    return {
        "objectId": str(location_id),
        "organizationId": str(organization_id),
        "locationId": str(location_id),
        "customerId": str(customer_id),
        "vin": _fingerprint_value(fields.get("vin")),
        "make": fields["make"],
        "model": fields["model"],
        "variant": _fingerprint_value(fields.get("variant")),
        "modelYear": _fingerprint_value(fields.get("modelYear")),
    }


def _link_status(conn: psycopg.Connection, link_id: UUID) -> str:
    row = conn.execute(
        """SELECT verified_at FROM auto_vehicle_customer_link
           WHERE id=%s AND revoked_at IS NULL""",
        (link_id,),
    ).fetchone()
    if row is None:
        raise AutoIdempotencyConflict("pending vehicle link is no longer active")
    return "VERIFIED" if row[0] is not None else "PENDING"


def create_vehicle_with_pending_link(
    conn: psycopg.Connection, staff_token: str,
    organization_id: UUID, location_id: UUID, customer_id: UUID,
    values: dict, idempotency_key: str | None,
) -> dict:
    """Создать автомобиль, immutable branch binding и pending link атомарно."""
    if (type(organization_id) is not UUID or type(location_id) is not UUID
            or type(customer_id) is not UUID):
        raise VehicleRegistryDenied("invalid scope")
    key = validated_key(idempotency_key)
    if key is None:
        raise ValueError("idempotency key required")
    fields = _normalize_fields(values, create=True)
    payload = _create_payload(organization_id, location_id, customer_id, fields)

    with conn.transaction():
        actor = session_subject(conn, staff_token, for_mutation=True)
        if actor is None or not organization_role_allows(
            conn, staff_token, organization_id, "INTAKE",
            location_id, for_mutation=True,
        ):
            raise VehicleRegistryDenied("organization INTAKE required")

        previous = existing(
            conn, actor, "vehicle.registry.create", key, payload,
        )
        if previous is not None:
            current = conn.execute(
                """SELECT v.id
                   FROM auto_vehicle v
                   JOIN auto_vehicle_location_binding b ON b.vehicle_id=v.id
                   JOIN auto_service_location l ON l.id=b.location_id
                   WHERE v.id=%s AND b.location_id=%s
                     AND l.organization_id=%s AND l.disabled_at IS NULL
                   FOR SHARE OF v,b,l""",
                (previous, location_id, organization_id),
            ).fetchone()
            if current is None:
                raise AutoIdempotencyConflict("created vehicle is no longer in scope")
            try:
                link_id = create_pending_vehicle_link(
                    conn, staff_token, previous, customer_id, key,
                )
            except VehicleLinkConflict as exc:
                raise AutoIdempotencyConflict("created link is no longer current") from exc
            return {
                "vehicleId": str(previous),
                "linkId": str(link_id),
                "status": _link_status(conn, link_id),
                "replayed": True,
            }

        vehicle_id = uuid4()
        try:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO auto_vehicle(
                         id,vin,make,model,variant,model_year
                       ) VALUES(%s,%s,%s,%s,%s,%s)""",
                    (vehicle_id, fields.get("vin"), fields["make"], fields["model"],
                     fields.get("variant"), fields.get("modelYear")),
                )
                conn.execute(
                    """INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id)
                       VALUES(%s,%s)""",
                    (vehicle_id, location_id),
                )
                link_id = create_pending_vehicle_link(
                    conn, staff_token, vehicle_id, customer_id, key,
                )
                conn.execute(
                    """INSERT INTO auto_vehicle_registry_audit(
                         id,vehicle_id,link_id,organization_id,location_id,
                         actor_id,action,changed_fields
                       ) VALUES(%s,%s,%s,%s,%s,%s,'CREATED',%s)""",
                    (uuid4(), vehicle_id, link_id, organization_id, location_id,
                     actor, ["vin","make","model","variant","model_year"]),
                )
                remember(
                    conn, actor, "vehicle.registry.create", key, payload, vehicle_id,
                )
        except VehicleLinkConflict as exc:
            raise VehicleRegistryConflict("active customer link already exists") from exc
        except (psycopg.errors.UniqueViolation, psycopg.errors.CheckViolation,
                psycopg.errors.RaiseException) as exc:
            raise VehicleRegistryConflict("vehicle registry conflict") from exc

        return {
            "vehicleId": str(vehicle_id),
            "linkId": str(link_id),
            "status": "PENDING",
            "replayed": False,
        }


def update_vehicle(
    conn: psycopg.Connection, manager_token: str,
    organization_id: UUID, location_id: UUID, vehicle_id: UUID,
    values: dict, idempotency_key: str | None,
) -> dict:
    """Изменить данные только в своём филиале; VIN нельзя заменить после заполнения."""
    if any(type(value) is not UUID for value in (
        organization_id, location_id, vehicle_id,
    )):
        raise VehicleRegistryDenied("invalid scope")
    key = validated_key(idempotency_key)
    if key is None:
        raise ValueError("idempotency key required")
    fields = _normalize_fields(values, create=False)
    payload = {
        "objectId": str(vehicle_id),
        "organizationId": str(organization_id),
        "locationId": str(location_id),
        **{key_name: _fingerprint_value(value)
           for key_name, value in fields.items()},
    }

    with conn.transaction():
        actor = session_subject(conn, manager_token, for_mutation=True)
        if actor is None:
            raise VehicleRegistryDenied("session unavailable")
        scope = vehicle_organization_scope(conn, vehicle_id)
        if scope != (organization_id, location_id):
            raise VehicleRegistryDenied("vehicle outside organization branch")
        if not organization_role_allows(
            conn, manager_token, organization_id, "MANAGER",
            location_id, for_mutation=True,
        ):
            raise VehicleRegistryDenied("organization MANAGER required")

        row = conn.execute(
            """SELECT vin,make,model,variant,model_year
               FROM auto_vehicle WHERE id=%s FOR UPDATE""",
            (vehicle_id,),
        ).fetchone()
        if row is None:
            raise VehicleRegistryDenied("vehicle unavailable")
        current = {
            "vin": row[0], "make": row[1], "model": row[2],
            "variant": row[3], "modelYear": row[4],
        }

        previous = existing(
            conn, actor, "vehicle.registry.update", key, payload,
        )
        if previous is not None:
            if previous != vehicle_id or any(
                current[name] != value for name, value in fields.items()
            ):
                raise AutoIdempotencyConflict("vehicle update result is no longer current")
            return {"vehicleId": str(vehicle_id), "status": "UPDATED", "replayed": True}

        if ("vin" in fields and current["vin"] is not None
                and fields["vin"] != current["vin"]):
            raise VehicleRegistryConflict("VIN cannot be changed after initial assignment")

        changed = {
            name: value for name, value in fields.items()
            if current[name] != value
        }
        if changed:
            assignments = ",".join(
                f"{_COLUMNS[name]}=%s" for name in changed
            )
            conn.execute(
                f"UPDATE auto_vehicle SET {assignments} WHERE id=%s",
                (*changed.values(), vehicle_id),
            )
            conn.execute(
                """INSERT INTO auto_vehicle_registry_audit(
                     id,vehicle_id,link_id,organization_id,location_id,
                     actor_id,action,changed_fields
                   ) VALUES(%s,%s,NULL,%s,%s,%s,'UPDATED',%s)""",
                (uuid4(), vehicle_id, organization_id, location_id,
                 actor, sorted(_COLUMNS[name] for name in changed)),
            )
        remember(
            conn, actor, "vehicle.registry.update", key, payload, vehicle_id,
        )
        return {"vehicleId": str(vehicle_id), "status": "UPDATED", "replayed": False}

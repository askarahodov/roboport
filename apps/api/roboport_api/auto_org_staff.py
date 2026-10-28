"""Internal organization staff administration with fail-closed live authority.

ADMIN provisioning is deliberately out of scope: a trusted offline
bootstrap must create ADMIN records after independent identity verification.
No HTTP routes call this module until production bootstrap is approved.
"""
from uuid import UUID, uuid4

import psycopg

from .session_lookup import session_subject

STAFF_ROLES = frozenset({"MANAGER", "INTAKE", "MECHANIC", "WAREHOUSE"})


class OrgStaffDenied(ValueError):
    pass


class OrgStaffConflict(ValueError):
    pass


def _admin(conn: psycopg.Connection, token: str, organization_id: UUID) -> UUID:
    actor = session_subject(conn, token, for_mutation=True)
    if actor is None:
        raise OrgStaffDenied("session unavailable")
    # Organization row is locked before grant row; a disable must wait for
    # the mutation, and a completed disable denies a new mutation.
    org = conn.execute(
        """SELECT id FROM auto_organization WHERE id=%s AND disabled_at IS NULL
           FOR SHARE""", (organization_id,),
    ).fetchone()
    if org is None:
        raise OrgStaffDenied("organization unavailable")
    granted = conn.execute(
        """SELECT id FROM auto_organization_role_grant
           WHERE organization_id=%s AND user_id=%s
             AND role_code='ADMIN' AND location_id IS NULL
             AND revoked_at IS NULL FOR SHARE""",
        (organization_id, actor),
    ).fetchone()
    if granted is None:
        raise OrgStaffDenied("organization admin required")
    return actor


def grant_staff(conn: psycopg.Connection, token: str, organization_id: UUID,
                target: UUID, role: str, location_id: UUID | None = None) -> UUID:
    if (not isinstance(organization_id, UUID) or not isinstance(target, UUID)
            or role not in STAFF_ROLES
            or (location_id is not None and not isinstance(location_id, UUID))):
        raise OrgStaffDenied("invalid request")
    with conn.transaction():
        actor = _admin(conn, token, organization_id)
        if actor == target:
            raise OrgStaffDenied("self grant forbidden")
        if location_id is not None:
            location = conn.execute(
                """SELECT id FROM auto_service_location
                   WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
                   FOR SHARE""", (location_id, organization_id),
            ).fetchone()
            if location is None:
                raise OrgStaffDenied("location unavailable")
        user = conn.execute(
            "SELECT id FROM app_user WHERE id=%s AND status='ACTIVE' FOR SHARE",
            (target,),
        ).fetchone()
        if user is None:
            raise OrgStaffDenied("target unavailable")
        # Lock per organization to serialize duplicate proposals reliably.
        # SELECT FOR SHARE above is compatible, but a unique constraint is
        # the final concurrency guard for concurrent inserts.
        grant_id = uuid4()
        try:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO auto_organization_role_grant
                       (id,organization_id,location_id,user_id,role_code)
                       VALUES(%s,%s,%s,%s,%s)""",
                    (grant_id, organization_id, location_id, target, role),
                )
                conn.execute(
                    """INSERT INTO auto_org_staff_action
                       (id,grant_id,organization_id,actor_id,action)
                       VALUES(%s,%s,%s,%s,'GRANTED')""",
                    (uuid4(), grant_id, organization_id, actor),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise OrgStaffConflict("active grant already exists") from exc
        return grant_id


def revoke_staff(conn: psycopg.Connection, token: str, organization_id: UUID,
                 grant_id: UUID) -> str:
    if not isinstance(organization_id, UUID) or not isinstance(grant_id, UUID):
        raise OrgStaffDenied("invalid request")
    with conn.transaction():
        actor = _admin(conn, token, organization_id)
        row = conn.execute(
            """SELECT user_id,role_code,revoked_at
               FROM auto_organization_role_grant
               WHERE id=%s AND organization_id=%s FOR UPDATE""",
            (grant_id, organization_id),
        ).fetchone()
        if row is None or row[1] == "ADMIN" or row[0] == actor:
            raise OrgStaffDenied("grant unavailable")
        if row[2] is not None:
            return "ALREADY_REVOKED"
        conn.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE id=%s AND revoked_at IS NULL""", (grant_id,),
        )
        conn.execute(
            """INSERT INTO auto_org_staff_action
               (id,grant_id,organization_id,actor_id,action)
               VALUES(%s,%s,%s,%s,'REVOKED')""",
            (uuid4(), grant_id, organization_id, actor),
        )
        return "REVOKED"


def list_staff(conn: psycopg.Connection, token: str, organization_id: UUID) -> list[dict]:
    """Bounded active grants, only to a live ADMIN of the exact tenant."""
    if not isinstance(organization_id, UUID):
        raise OrgStaffDenied("invalid organization")
    with conn.transaction():
        _admin(conn, token, organization_id)
        rows = conn.execute(
            """SELECT g.id,g.user_id,g.role_code,g.location_id
               FROM auto_organization_role_grant g
               JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
               WHERE g.organization_id=%s AND g.revoked_at IS NULL
                 AND g.role_code <> 'ADMIN'
                 AND (g.location_id IS NULL OR EXISTS
                   (SELECT 1 FROM auto_service_location l
                    WHERE l.id=g.location_id AND l.organization_id=g.organization_id
                      AND l.disabled_at IS NULL))
               ORDER BY g.created_at,g.id LIMIT 100""", (organization_id,),
        ).fetchall()
        return [{"grantId":str(g),"userId":str(u),"role":role,
                 "locationId":str(loc) if loc else None} for g,u,role,loc in rows]

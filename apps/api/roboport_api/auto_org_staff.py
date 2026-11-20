"""Internal organization staff administration with fail-closed live authority.

ADMIN provisioning and staff invitations are synthetic/dev only until the
independent identity, delivery and legal gates are approved.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
from uuid import UUID, uuid4

import psycopg

from .session_lookup import session_subject

STAFF_ROLES = frozenset({"MANAGER", "INTAKE", "MECHANIC", "WAREHOUSE", "CASHIER"})


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
        if conn.execute(
            """SELECT 1 FROM auto_org_staff_invitation
               WHERE organization_id=%s AND target_user_id=%s AND role_code=%s
                 AND location_id IS NOT DISTINCT FROM %s AND status='PENDING'
                 AND expires_at>clock_timestamp() LIMIT 1""",
            (organization_id, target, role, location_id),
        ).fetchone() is not None:
            raise OrgStaffConflict("pending invitation already exists")
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


def list_staff_audit(conn: psycopg.Connection, token: str,
                     organization_id: UUID, *, limit: int = 100) -> list[dict]:
    """Tenant-scoped, bounded immutable action history for current ADMIN only.

    Includes revoked grants; never reveals an organization via another tenant.
    """
    if (type(organization_id) is not UUID or type(limit) is not int
            or not 1 <= limit <= 100):
        raise OrgStaffDenied("invalid request")
    with conn.transaction():
        _admin(conn, token, organization_id)
        rows = conn.execute(
            """SELECT a.id,a.grant_id,a.actor_id,a.action,a.created_at,
                      g.user_id,g.role_code,g.location_id
               FROM auto_org_staff_action a
               JOIN auto_organization_role_grant g
                 ON g.id=a.grant_id AND g.organization_id=a.organization_id
               WHERE a.organization_id=%s
               ORDER BY a.created_at DESC,a.id DESC LIMIT %s""",
            (organization_id, limit),
        ).fetchall()
        return [{
            "eventId": str(eid), "grantId": str(grant), "actorId": str(actor),
            "action": action, "createdAt": stamp.isoformat(),
            "userId": str(target), "role": role,
            "locationId": str(loc) if loc else None,
        } for eid, grant, actor, action, stamp, target, role, loc in rows]


_INVITATION_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,64}$")


def _invitation_digest(raw_token: str) -> str:
    if (type(raw_token) is not str or not _INVITATION_TOKEN_RE.fullmatch(raw_token)):
        raise OrgStaffDenied("invitation unavailable")
    return hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def create_staff_invitation(
    conn: psycopg.Connection, token: str, organization_id: UUID,
    target_login: str, role: str, location_id: UUID | None = None,
    *, expires_days: int = 7,
) -> dict:
    """Create a one-time invite for an existing account; reveal token once.

    The invitation token is stored only as SHA-256. The invitee must already
    have an active account and accept while authenticated as that exact user.
    Delivery is intentionally out-of-band in this synthetic-only slice.
    """
    if (type(organization_id) is not UUID or type(role) is not str
            or role not in STAFF_ROLES
            or (location_id is not None and type(location_id) is not UUID)
            or type(expires_days) is not int or not 1 <= expires_days <= 30
            or type(target_login) is not str):
        raise OrgStaffDenied("invalid invitation")
    normalized = target_login.strip().lower()
    if (not 3 <= len(normalized) <= 128 or not normalized.isascii()
            or any(not char.isprintable() or char.isspace() for char in normalized)):
        raise OrgStaffDenied("invalid invitation target")

    raw_token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
    invitation_id = uuid4()
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(days=expires_days)

    with conn.transaction():
        actor = _admin(conn, token, organization_id)
        target = conn.execute(
            """SELECT id FROM app_user
               WHERE normalized_login=%s AND status='ACTIVE' FOR SHARE""",
            (normalized,),
        ).fetchone()
        if target is None or target[0] == actor:
            raise OrgStaffDenied("invitation target unavailable")
        target_id = target[0]
        if location_id is not None:
            location = conn.execute(
                """SELECT id FROM auto_service_location
                   WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
                   FOR SHARE""", (location_id, organization_id),
            ).fetchone()
            if location is None:
                raise OrgStaffDenied("location unavailable")

        if conn.execute(
            """SELECT 1 FROM auto_organization_role_grant
               WHERE organization_id=%s AND user_id=%s AND role_code=%s
                 AND location_id IS NOT DISTINCT FROM %s AND revoked_at IS NULL
               LIMIT 1""",
            (organization_id, target_id, role, location_id),
        ).fetchone() is not None:
            raise OrgStaffConflict("active staff grant already exists")

        expired = conn.execute(
            """UPDATE auto_org_staff_invitation
               SET status='EXPIRED',expired_at=%s
               WHERE organization_id=%s AND target_user_id=%s AND role_code=%s
                 AND location_id IS NOT DISTINCT FROM %s
                 AND status='PENDING' AND expires_at<=%s
               RETURNING id""",
            (created_at, organization_id, target_id, role, location_id, created_at),
        ).fetchall()
        for (old_id,) in expired:
            conn.execute(
                """INSERT INTO auto_org_staff_invitation_event
                   (id,invitation_id,organization_id,actor_id,action)
                   VALUES(%s,%s,%s,%s,'EXPIRED')""",
                (uuid4(), old_id, organization_id, actor),
            )

        try:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO auto_org_staff_invitation
                       (id,organization_id,location_id,target_user_id,target_login,
                        role_code,token_hash,created_by,created_at,expires_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (invitation_id, organization_id, location_id, target_id,
                     normalized, role, digest, actor, created_at, expires_at),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise OrgStaffConflict("pending invitation already exists") from exc
        conn.execute(
            """INSERT INTO auto_org_staff_invitation_event
               (id,invitation_id,organization_id,actor_id,action)
               VALUES(%s,%s,%s,%s,'CREATED')""",
            (uuid4(), invitation_id, organization_id, actor),
        )

    return {
        "invitationId": str(invitation_id),
        "token": raw_token,
        "targetLogin": normalized,
        "role": role,
        "locationId": str(location_id) if location_id else None,
        "expiresAt": expires_at.isoformat(),
    }


def list_staff_invitations(
    conn: psycopg.Connection, token: str, organization_id: UUID,
    *, limit: int = 100,
) -> list[dict]:
    if type(organization_id) is not UUID or type(limit) is not int or not 1 <= limit <= 100:
        raise OrgStaffDenied("invalid request")
    with conn.transaction():
        _admin(conn, token, organization_id)
        rows = conn.execute(
            """SELECT id,target_login,role_code,location_id,status,created_at,
                      expires_at,accepted_grant_id
               FROM auto_org_staff_invitation
               WHERE organization_id=%s
               ORDER BY created_at DESC,id DESC LIMIT %s""",
            (organization_id, limit),
        ).fetchall()
        now = datetime.now(timezone.utc)
        return [{
            "invitationId": str(ident),
            "targetLogin": login,
            "role": role,
            "locationId": str(location) if location else None,
            "status": "EXPIRED" if status == "PENDING" and expires_at <= now else status,
            "createdAt": created.isoformat(),
            "expiresAt": expires_at.isoformat(),
            "grantId": str(grant) if grant else None,
        } for ident,login,role,location,status,created,expires_at,grant in rows]


def revoke_staff_invitation(
    conn: psycopg.Connection, token: str, organization_id: UUID,
    invitation_id: UUID,
) -> str:
    if type(organization_id) is not UUID or type(invitation_id) is not UUID:
        raise OrgStaffDenied("invalid request")
    with conn.transaction():
        actor = _admin(conn, token, organization_id)
        row = conn.execute(
            """SELECT status,expires_at FROM auto_org_staff_invitation
               WHERE id=%s AND organization_id=%s FOR UPDATE""",
            (invitation_id, organization_id),
        ).fetchone()
        if row is None:
            raise OrgStaffDenied("invitation unavailable")
        status, expires_at = row
        if status == "REVOKED":
            return "ALREADY_REVOKED"
        if status == "ACCEPTED":
            return "ALREADY_ACCEPTED"
        if status == "EXPIRED":
            return "EXPIRED"
        now = datetime.now(timezone.utc)
        if expires_at <= now:
            conn.execute(
                """UPDATE auto_org_staff_invitation
                   SET status='EXPIRED',expired_at=%s WHERE id=%s""",
                (now, invitation_id),
            )
            action = "EXPIRED"
        else:
            conn.execute(
                """UPDATE auto_org_staff_invitation
                   SET status='REVOKED',revoked_at=%s WHERE id=%s""",
                (now, invitation_id),
            )
            action = "REVOKED"
        conn.execute(
            """INSERT INTO auto_org_staff_invitation_event
               (id,invitation_id,organization_id,actor_id,action)
               VALUES(%s,%s,%s,%s,%s)""",
            (uuid4(), invitation_id, organization_id, actor, action),
        )
        return action


def accept_staff_invitation(
    conn: psycopg.Connection, session_token: str, invitation_token: str,
) -> dict:
    """Accept only as the pre-provisioned account bound to the invite."""
    digest = _invitation_digest(invitation_token)
    with conn.transaction():
        actor = session_subject(conn, session_token, for_mutation=True)
        if actor is None:
            raise OrgStaffDenied("session unavailable")
        row = conn.execute(
            """SELECT id,organization_id,location_id,target_user_id,target_login,
                      role_code,created_by,status,expires_at
               FROM auto_org_staff_invitation WHERE token_hash=%s FOR UPDATE""",
            (digest,),
        ).fetchone()
        if row is None:
            raise OrgStaffDenied("invitation unavailable")
        (invitation_id,organization_id,location_id,target_id,target_login,
         role,created_by,status,expires_at) = row
        if target_id != actor:
            raise OrgStaffDenied("invitation unavailable")
        if status == "ACCEPTED":
            return {"status":"ALREADY_ACCEPTED"}
        if status == "REVOKED":
            return {"status":"REVOKED"}
        if status == "EXPIRED":
            return {"status":"EXPIRED"}
        user = conn.execute(
            """SELECT normalized_login FROM app_user
               WHERE id=%s AND status='ACTIVE' FOR SHARE""", (actor,),
        ).fetchone()
        if user is None or user[0] != target_login:
            raise OrgStaffDenied("invitation unavailable")
        now = datetime.now(timezone.utc)
        if expires_at <= now:
            conn.execute(
                """UPDATE auto_org_staff_invitation
                   SET status='EXPIRED',expired_at=%s WHERE id=%s""",
                (now, invitation_id),
            )
            conn.execute(
                """INSERT INTO auto_org_staff_invitation_event
                   (id,invitation_id,organization_id,actor_id,action)
                   VALUES(%s,%s,%s,%s,'EXPIRED')""",
                (uuid4(), invitation_id, organization_id, actor),
            )
            return {"status":"EXPIRED"}

        org = conn.execute(
            """SELECT id FROM auto_organization
               WHERE id=%s AND disabled_at IS NULL FOR SHARE""",
            (organization_id,),
        ).fetchone()
        if org is None:
            raise OrgStaffDenied("organization unavailable")
        issuer = conn.execute(
            """SELECT g.id FROM auto_organization_role_grant g
               JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
               WHERE g.organization_id=%s AND g.user_id=%s
                 AND g.role_code='ADMIN' AND g.location_id IS NULL
                 AND g.revoked_at IS NULL FOR SHARE OF g,u""",
            (organization_id, created_by),
        ).fetchone()
        if issuer is None:
            raise OrgStaffDenied("invitation issuer no longer authorized")
        if location_id is not None:
            location = conn.execute(
                """SELECT id FROM auto_service_location
                   WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
                   FOR SHARE""", (location_id, organization_id),
            ).fetchone()
            if location is None:
                raise OrgStaffDenied("location unavailable")
        if conn.execute(
            """SELECT 1 FROM auto_organization_role_grant
               WHERE organization_id=%s AND user_id=%s AND role_code=%s
                 AND location_id IS NOT DISTINCT FROM %s AND revoked_at IS NULL
               LIMIT 1""",
            (organization_id, actor, role, location_id),
        ).fetchone() is not None:
            raise OrgStaffConflict("active staff grant already exists")

        grant_id = uuid4()
        try:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO auto_organization_role_grant
                       (id,organization_id,location_id,user_id,role_code)
                       VALUES(%s,%s,%s,%s,%s)""",
                    (grant_id, organization_id, location_id, actor, role),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise OrgStaffConflict("active staff grant already exists") from exc
        # The administrator authorized the membership; the invitee's separate
        # ACCEPTED event proves possession of the token and authenticated account.
        conn.execute(
            """INSERT INTO auto_org_staff_action
               (id,grant_id,organization_id,actor_id,action)
               VALUES(%s,%s,%s,%s,'GRANTED')""",
            (uuid4(), grant_id, organization_id, created_by),
        )
        conn.execute(
            """UPDATE auto_org_staff_invitation
               SET status='ACCEPTED',accepted_by=%s,accepted_grant_id=%s,
                   accepted_at=%s WHERE id=%s""",
            (actor, grant_id, now, invitation_id),
        )
        conn.execute(
            """INSERT INTO auto_org_staff_invitation_event
               (id,invitation_id,organization_id,actor_id,action)
               VALUES(%s,%s,%s,%s,'ACCEPTED')""",
            (uuid4(), invitation_id, organization_id, actor),
        )
        return {
            "status":"ACCEPTED",
            "organizationId":str(organization_id),
            "grantId":str(grant_id),
            "role":role,
            "locationId":str(location_id) if location_id else None,
        }


def list_staff_invitation_audit(
    conn: psycopg.Connection, token: str, organization_id: UUID,
    *, limit: int = 100,
) -> list[dict]:
    if type(organization_id) is not UUID or type(limit) is not int or not 1 <= limit <= 100:
        raise OrgStaffDenied("invalid request")
    with conn.transaction():
        _admin(conn, token, organization_id)
        rows = conn.execute(
            """SELECT e.id,e.invitation_id,e.actor_id,e.action,e.created_at,
                      i.target_login,i.role_code,i.location_id
               FROM auto_org_staff_invitation_event e
               JOIN auto_org_staff_invitation i
                 ON i.id=e.invitation_id AND i.organization_id=e.organization_id
               WHERE e.organization_id=%s
               ORDER BY e.created_at DESC,e.id DESC LIMIT %s""",
            (organization_id, limit),
        ).fetchall()
        return [{
            "eventId":str(event_id),"invitationId":str(invitation_id),
            "actorId":str(actor_id),"action":action,"createdAt":created_at.isoformat(),
            "targetLogin":login,"role":role,
            "locationId":str(location) if location else None,
        } for event_id,invitation_id,actor_id,action,created_at,login,role,location in rows]

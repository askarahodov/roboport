"""Authorized synthetic branch opening schedule: weekly windows and closures."""
from datetime import date
from uuid import UUID
import psycopg

from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject


class OpeningDenied(ValueError):
    pass


class OpeningInvalid(ValueError):
    pass


class OpeningConflict(ValueError):
    pass


def validate_opening(windows: list[dict], closed_dates: list[date]) -> None:
    if (type(windows) is not list or len(windows) > 28
        or type(closed_dates) is not list or len(closed_dates) > 120
        or any(type(day) is not date for day in closed_dates)
        or len(set(closed_dates)) != len(closed_dates)):
        raise OpeningInvalid("invalid schedule shape")
    seen: dict[int, list[tuple[int,int]]] = {}
    for window in windows:
        if (type(window) is not dict or set(window) !=
            {"weekday", "opensMinute", "closesMinute"}):
            raise OpeningInvalid("invalid window fields")
        weekday, opens, closes = (
            window["weekday"], window["opensMinute"], window["closesMinute"]
        )
        if (any(type(x) is not int for x in (weekday,opens,closes))
            or not 1 <= weekday <= 7
            or not 0 <= opens < closes <= 1440):
            raise OpeningInvalid("invalid opening bounds")
        for other_start,other_end in seen.get(weekday,[]):
            if opens < other_end and closes > other_start:
                raise OpeningInvalid("overlapping opening windows")
        seen.setdefault(weekday,[]).append((opens,closes))


def get_opening(conn: psycopg.Connection, token: str,
                org: UUID, loc: UUID) -> dict:
    with conn.transaction():
        if not (
            organization_role_allows(conn,token,org,"INTAKE",loc,for_mutation=True)
            or organization_role_allows(conn,token,org,"MANAGER",loc,for_mutation=True)
        ):
            raise OpeningDenied("branch staff required")
        loc_row=conn.execute(
            """SELECT timezone_name FROM auto_service_location
               WHERE organization_id=%s AND id=%s AND disabled_at IS NULL""",
            (org,loc),
        ).fetchone()
        if loc_row is None:
            raise OpeningDenied("location unavailable")
        row=conn.execute(
            """SELECT version,windows,closed_dates FROM auto_location_opening_policy
               WHERE location_id=%s AND organization_id=%s""",
            (loc,org),
        ).fetchone()
        if row is None:
            return {"configured":False,"version":0,"windows":[],
                    "closedDates":[],"timeZone":loc_row[0],"syntheticOnly":True}
        return {"configured":True,"version":row[0],
                "windows":row[1],
                "closedDates":[x.isoformat() for x in row[2]],
                "timeZone":loc_row[0],"syntheticOnly":True}


def set_opening(conn: psycopg.Connection, token: str,
                org: UUID, loc: UUID, expected_version: int,
                windows: list[dict], closed_dates: list[date]) -> dict:
    if (type(org) is not UUID or type(loc) is not UUID
        or type(expected_version) is not int
        or not 0 <= expected_version <= 1_000_000_000):
        raise OpeningInvalid("invalid version/scope")
    validate_opening(windows,closed_dates)
    from psycopg.types.json import Jsonb
    with conn.transaction():
        if not organization_role_allows(
            conn,token,org,"MANAGER",loc,for_mutation=True
        ):
            raise OpeningDenied("manager required")
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None:
            raise OpeningDenied("session unavailable")
        scoped=conn.execute(
            """SELECT id FROM auto_service_location
               WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
               FOR UPDATE""",
            (loc,org),
        ).fetchone()
        if scoped is None:
            raise OpeningDenied("location unavailable")
        existing=conn.execute(
            """SELECT version FROM auto_location_opening_policy
               WHERE location_id=%s AND organization_id=%s FOR UPDATE""",
            (loc,org),
        ).fetchone()
        actual=existing[0] if existing else 0
        if actual != expected_version:
            raise OpeningConflict("opening schedule changed")
        canonical=sorted(windows,key=lambda w:(
            w["weekday"],w["opensMinute"],w["closesMinute"],
        ))
        dates=sorted(closed_dates)
        try:
            with conn.transaction():
                if existing is None:
                    conn.execute(
                        """INSERT INTO auto_location_opening_policy
                           (location_id,organization_id,windows,closed_dates,
                            version,changed_by)
                           VALUES(%s,%s,%s,%s,1,%s)""",
                        (loc,org,Jsonb(canonical),dates,actor),
                    )
                else:
                    conn.execute(
                        """UPDATE auto_location_opening_policy
                           SET windows=%s,closed_dates=%s,version=version+1,
                               changed_by=%s,changed_at=clock_timestamp()
                           WHERE location_id=%s AND organization_id=%s""",
                        (Jsonb(canonical),dates,actor,loc,org),
                    )
        except psycopg.errors.RaiseException as exc:
            if "opening policy conflicts with active bookings" in str(exc):
                raise OpeningConflict("active bookings outside new schedule") from exc
            raise
        return {"configured":True,"version":actual+1,
                "windows":canonical,
                "closedDates":[d.isoformat() for d in dates],
                "syntheticOnly":True}

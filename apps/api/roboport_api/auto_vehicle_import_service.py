"""Authorized synthetic vehicle staging; session, grant and write share transaction.

INTERNAL PG17 ONLY: not a production route or permission to process real VINs.
"""
from pathlib import Path
from uuid import UUID

import psycopg

from roboport_api.auto_org_authority import organization_role_allows
from scripts.auto_vehicle_migration_pg_staging import stage_synthetic_vehicles


class VehicleImportDenied(ValueError):
    """Generic denial without source identifiers."""


def stage_authorized_synthetic_vehicles(
    conn: psycopg.Connection, token: str, path: Path, *,
    organization_id: UUID, location_id: UUID, batch_id: UUID,
    expected_sha256: str,
) -> dict:
    if (type(token) is not str or not token
            or type(organization_id) is not UUID
            or type(location_id) is not UUID
            or type(batch_id) is not UUID):
        raise VehicleImportDenied("FORBIDDEN")
    with conn.transaction():
        # This function locks live session/grant/tenant/branch until commit.
        if not organization_role_allows(
            conn, token, organization_id, "MANAGER", location_id,
            for_mutation=True,
        ):
            raise VehicleImportDenied("FORBIDDEN")
        # The authority check above is for ONE branch. Never grant it access
        # to a file containing vehicles from another branch in the same org.
        # The staging helper still enforces organization and active branches.
        # An extra per-row branch condition is enforced by the wrapper below.
        return stage_synthetic_vehicles(
            conn, path, organization_id=organization_id,
            batch_id=batch_id, expected_sha256=expected_sha256,
            required_location_id=location_id,
        )

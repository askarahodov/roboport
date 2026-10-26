"""Проверка, что назначение на автомобиль нельзя подменить или оживить."""
from uuid import uuid4

import psycopg
import pytest

from test_pg_baseline import DSN


def test_auto_staff_assignment_identity_and_revocation_are_immutable():
    user, another, vehicle, other_vehicle, assignment = (uuid4() for _ in range(5))
    with psycopg.connect(DSN) as db:
        for ident in (user, another):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                       (ident, "auto-assign-" + ident.hex))
        for ident in (vehicle, other_vehicle):
            db.execute("INSERT INTO auto_vehicle(id,make,model) VALUES(%s,'Synthetic','Van')",
                       (ident,))
        db.execute("""INSERT INTO auto_vehicle_staff_assignment
                   (id,vehicle_id,user_id,role_code) VALUES(%s,%s,%s,'INTAKE')""",
                   (assignment,vehicle,user))
        for column, value in (("user_id",another), ("vehicle_id",other_vehicle),
                              ("role_code","MANAGER")):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(f"UPDATE auto_vehicle_staff_assignment SET {column}=%s WHERE id=%s",
                               (value,assignment))
        db.execute("UPDATE auto_vehicle_staff_assignment SET revoked_at=now() WHERE id=%s",
                   (assignment,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_vehicle_staff_assignment SET revoked_at=NULL WHERE id=%s",
                           (assignment,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_vehicle_staff_assignment WHERE id=%s",
                           (assignment,))
        row=db.execute("""SELECT user_id,vehicle_id,role_code,revoked_at
                           FROM auto_vehicle_staff_assignment WHERE id=%s""",
                       (assignment,)).fetchone()
        assert row[:3]==(user,vehicle,"INTAKE") and row[3] is not None
        # Новое назначение после отзыва старого допустимо только отдельным INSERT.
        replacement=uuid4()
        db.execute("""INSERT INTO auto_vehicle_staff_assignment
                   (id,vehicle_id,user_id,role_code) VALUES(%s,%s,%s,'INTAKE')""",
                   (replacement,vehicle,user))
        assert replacement != assignment

"""Прямой SQL не может переписать идентичность и историю связи авто с клиентом."""
from uuid import uuid4

import psycopg
import pytest

from test_pg_baseline import DSN


def _seed(conn):
    vehicle, other_vehicle = uuid4(), uuid4()
    customer_a, customer_b, customer_c = uuid4(), uuid4(), uuid4()
    reviewer, other_reviewer = uuid4(), uuid4()
    link_a, link_b = uuid4(), uuid4()

    conn.execute(
        """INSERT INTO customer(id,label)
           VALUES(%s,'synthetic-owner-a'),(%s,'synthetic-owner-b'),(%s,'synthetic-owner-c')""",
        (customer_a, customer_b, customer_c),
    )
    conn.execute(
        """INSERT INTO auto_vehicle(id,make,model)
           VALUES(%s,'Synthetic','Sedan'),(%s,'Synthetic','Wagon')""",
        (vehicle, other_vehicle),
    )
    conn.execute(
        "INSERT INTO app_user(id,normalized_login) VALUES(%s,%s),(%s,%s)",
        (
            reviewer, "vehicle-link-reviewer-" + reviewer.hex,
            other_reviewer, "vehicle-link-reviewer-" + other_reviewer.hex,
        ),
    )
    conn.execute(
        """INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id)
           VALUES(%s,%s,%s),(%s,%s,%s)""",
        (link_a, vehicle, customer_a, link_b, vehicle, customer_b),
    )
    return locals()


def test_vehicle_customer_link_identity_and_verification_are_immutable():
    with psycopg.connect(DSN) as db:
        data = _seed(db)
        # Автомобиль может иметь несколько разных клиентских связей.
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_customer_link WHERE vehicle_id=%s",
            (data["vehicle"],),
        ).fetchone() == (2,)

        # Даже прямой INSERT не может создать уже подтверждённую или отозванную
        # связь: любая новая запись обязана начинаться в состоянии PENDING.
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    """INSERT INTO auto_vehicle_customer_link(
                         id,vehicle_id,customer_id,verified_at,verified_by
                       ) VALUES(%s,%s,%s,clock_timestamp(),%s)""",
                    (uuid4(), data["vehicle"], data["customer_c"], data["reviewer"]),
                )
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    """INSERT INTO auto_vehicle_customer_link(
                         id,vehicle_id,customer_id,revoked_at
                       ) VALUES(%s,%s,%s,clock_timestamp())""",
                    (uuid4(), data["vehicle"], data["customer_c"]),
                )

        identity_updates = (
            (
                "UPDATE auto_vehicle_customer_link SET id=%s WHERE id=%s",
                (uuid4(), data["link_a"]),
            ),
            (
                "UPDATE auto_vehicle_customer_link SET vehicle_id=%s WHERE id=%s",
                (data["other_vehicle"], data["link_a"]),
            ),
            (
                "UPDATE auto_vehicle_customer_link SET customer_id=%s WHERE id=%s",
                (data["customer_c"], data["link_a"]),
            ),
            (
                "UPDATE auto_vehicle_customer_link SET linked_at=linked_at + interval '1 second' WHERE id=%s",
                (data["link_a"],),
            ),
        )
        for statement, params in identity_updates:
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(statement, params)

        # Независимая проверка может один раз перевести PENDING → VERIFIED.
        db.execute(
            """UPDATE auto_vehicle_customer_link
               SET verified_at=clock_timestamp(), verified_by=%s WHERE id=%s""",
            (data["reviewer"], data["link_a"]),
        )
        verified = db.execute(
            "SELECT verified_at,verified_by FROM auto_vehicle_customer_link WHERE id=%s",
            (data["link_a"],),
        ).fetchone()
        assert verified[0] is not None and verified[1] == data["reviewer"]

        verification_rewrites = (
            ("UPDATE auto_vehicle_customer_link SET verified_at=NULL WHERE id=%s", (data["link_a"],)),
            (
                "UPDATE auto_vehicle_customer_link SET verified_at=verified_at + interval '1 second' WHERE id=%s",
                (data["link_a"],),
            ),
            (
                "UPDATE auto_vehicle_customer_link SET verified_by=%s WHERE id=%s",
                (data["other_reviewer"], data["link_a"]),
            ),
            ("UPDATE auto_vehicle_customer_link SET verified_by=NULL WHERE id=%s", (data["link_a"],)),
        )
        for statement, params in verification_rewrites:
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(statement, params)

        # Верифицированную связь можно отозвать, но нельзя воскресить или
        # переписать момент отзыва.
        db.execute(
            "UPDATE auto_vehicle_customer_link SET revoked_at=clock_timestamp() WHERE id=%s",
            (data["link_a"],),
        )
        revoked = db.execute(
            "SELECT revoked_at FROM auto_vehicle_customer_link WHERE id=%s",
            (data["link_a"],),
        ).fetchone()[0]
        assert revoked is not None

        revocation_rewrites = (
            ("UPDATE auto_vehicle_customer_link SET revoked_at=NULL WHERE id=%s", (data["link_a"],)),
            (
                "UPDATE auto_vehicle_customer_link SET revoked_at=revoked_at + interval '1 second' WHERE id=%s",
                (data["link_a"],),
            ),
        )
        for statement, params in revocation_rewrites:
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(statement, params)

        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "DELETE FROM auto_vehicle_customer_link WHERE id=%s",
                    (data["link_a"],),
                )

        # Отозванную pending-связь нельзя впоследствии подтвердить.
        db.execute(
            "UPDATE auto_vehicle_customer_link SET revoked_at=clock_timestamp() WHERE id=%s",
            (data["link_b"],),
        )
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    """UPDATE auto_vehicle_customer_link
                       SET verified_at=clock_timestamp(), verified_by=%s WHERE id=%s""",
                    (data["reviewer"], data["link_b"]),
                )

        # Повторное предложение той же пары возможно только новой строкой;
        # старые строки не переписываются, несколько клиентов на авто сохраняются.
        replacement = uuid4()
        db.execute(
            """INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id)
               VALUES(%s,%s,%s)""",
            (replacement, data["vehicle"], data["customer_a"]),
        )
        assert db.execute(
            """SELECT count(*) FROM auto_vehicle_customer_link
               WHERE vehicle_id=%s AND customer_id=%s""",
            (data["vehicle"], data["customer_a"]),
        ).fetchone() == (2,)

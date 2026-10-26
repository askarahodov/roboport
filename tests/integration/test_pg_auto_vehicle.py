"""Проверки целостности автомобильной модели на одноразовом PostgreSQL 17.

Реальные VIN, клиентские данные и госномера не используются.
"""
from uuid import uuid4

import psycopg
import pytest

from test_pg_baseline import DSN


def test_vehicle_vin_is_optional_normalized_and_unique():
    with psycopg.connect(DSN) as db:
        db.execute(
            "INSERT INTO auto_vehicle(id,make,model,model_year) VALUES(%s,'TestMake','Sedan',2026)",
            (uuid4(),),
        )
        vin = "TEST0000000000001"
        db.execute(
            "INSERT INTO auto_vehicle(id,vin,make,model) VALUES(%s,%s,'TestMake','Van')",
            (uuid4(), vin),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db.transaction():
                db.execute(
                    "INSERT INTO auto_vehicle(id,vin,make,model) VALUES(%s,%s,'TestMake','Van')",
                    (uuid4(), vin),
                )
        for invalid in ("short", "TEST000000000000I", "test0000000000001"):
            with pytest.raises(psycopg.errors.CheckViolation):
                with db.transaction():
                    db.execute(
                        "INSERT INTO auto_vehicle(id,vin,make,model) VALUES(%s,%s,'TestMake','Van')",
                        (uuid4(), invalid),
                    )


def test_vehicle_rejects_incomplete_identity_and_invalid_year():
    with psycopg.connect(DSN) as db:
        for make, model, year in ((" ", "Sedan", 2026), ("Brand", " ", 2026), ("Brand", "Sedan", 1800)):
            with pytest.raises(psycopg.errors.CheckViolation):
                with db.transaction():
                    db.execute(
                        "INSERT INTO auto_vehicle(id,make,model,model_year) VALUES(%s,%s,%s,%s)",
                        (uuid4(), make, model, year),
                    )


def test_customer_vehicle_links_require_valid_owners_and_allow_revocation():
    vehicle, customer, another_customer = uuid4(), uuid4(), uuid4()
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO auto_vehicle(id,make,model) VALUES(%s,'Brand','Sedan')", (vehicle,))
        db.execute(
            "INSERT INTO customer(id,label) VALUES(%s,'synthetic-A'),(%s,'synthetic-B')",
            (customer, another_customer),
        )
        first_link = uuid4()
        db.execute(
            "INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id) VALUES(%s,%s,%s)",
            (first_link, vehicle, customer),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db.transaction():
                db.execute(
                    "INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id) VALUES(%s,%s,%s)",
                    (uuid4(), vehicle, customer),
                )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with db.transaction():
                db.execute(
                    "INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id) VALUES(%s,%s,%s)",
                    (uuid4(), uuid4(), another_customer),
                )
        # Новая связь — только неподтверждённая заявка на привязку.
        # Самостоятельно она не даёт права читать сервисную историю.
        assert db.execute(
            "SELECT verified_at, verified_by FROM auto_vehicle_customer_link WHERE id=%s",
            (first_link,),
        ).fetchone() == (None, None)
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.transaction():
                db.execute(
                    "UPDATE auto_vehicle_customer_link SET verified_at=now() WHERE id=%s",
                    (first_link,),
                )
        db.execute(
            "UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
            (first_link,),
        )
        db.execute(
            "INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id) VALUES(%s,%s,%s)",
            (uuid4(), vehicle, customer),
        )
        # Связи с несколькими клиентами могут сосуществовать, но не дают
        # автоматически доступ к прежним документам service_ticket.
        db.execute(
            "INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id) VALUES(%s,%s,%s)",
            (uuid4(), vehicle, another_customer),
        )

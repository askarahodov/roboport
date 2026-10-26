"""Automotive foundation: negative integrity tests against disposable PostgreSQL 17.

No real VIN, customer or plate data is used.
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
        vin = "WVWZZZ1JZXW000001"
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
        for invalid in ("short", "WVWZZZ1JZXW00000I", "wvwzzz1jzxw000001"):
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
        db.execute(
            "UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
            (first_link,),
        )
        db.execute(
            "INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id) VALUES(%s,%s,%s)",
            (uuid4(), vehicle, customer),
        )
        # An additional verified customer link can coexist; it does NOT grant
        # access to previously created service_ticket documents.
        db.execute(
            "INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id) VALUES(%s,%s,%s)",
            (uuid4(), vehicle, another_customer),
        )

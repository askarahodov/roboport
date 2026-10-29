"""PG17 bin/lot identity and tenant/branch isolation."""
from uuid import uuid4
import psycopg
import pytest
from test_pg_baseline import DSN
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_ledger import sku


def add_bin(db, org, loc, code="B-01"):
    ident=uuid4()
    db.execute("""INSERT INTO auto_stock_bin(id,organization_id,location_id,code)
                  VALUES(%s,%s,%s,%s)""",(ident,org,loc,code))
    return ident


def add_lot(db,org,loc,item,bin_id,batch="BATCH-1"):
    ident=uuid4()
    db.execute("""INSERT INTO auto_stock_lot
      (id,organization_id,location_id,sku_id,bin_id,batch_code,source_revision)
      VALUES(%s,%s,%s,%s,%s,%s,'synthetic-rev')""",
      (ident,org,loc,item,bin_id,batch))
    return ident


def test_lot_bin_identity_immutability_and_unique_batches():
    with psycopg.connect(DSN) as db:
        org,loc,_=_org_and_bay(db)
        item=sku(db,org,loc)
        bin_id=add_bin(db,org,loc)
        lot=add_lot(db,org,loc,item,bin_id)
        assert db.execute("SELECT sku_id,bin_id FROM auto_stock_lot WHERE id=%s",(lot,)).fetchone()==(item,bin_id)
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db.transaction():
                add_bin(db,org,loc)
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db.transaction():
                add_lot(db,org,loc,item,bin_id)
        for sql,identity in (
            ("UPDATE auto_stock_bin SET code='NEW' WHERE id=%s",bin_id),
            ("DELETE FROM auto_stock_bin WHERE id=%s",bin_id),
            ("UPDATE auto_stock_lot SET batch_code='NEW' WHERE id=%s",lot),
            ("DELETE FROM auto_stock_lot WHERE id=%s",lot),
        ):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(sql,(identity,))


def test_tenant_branch_mismatch_and_disabled_location():
    with psycopg.connect(DSN) as db:
        org,loc,_=_org_and_bay(db)
        foreign,foreign_loc,_=_org_and_bay(db)
        item=sku(db,org,loc)
        bin_id=add_bin(db,org,loc)
        foreign_bin=add_bin(db,foreign,foreign_loc)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                add_bin(db,org,foreign_loc,"BAD")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with db.transaction():
                add_lot(db,org,loc,item,foreign_bin)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                add_lot(db,foreign,foreign_loc,item,foreign_bin)
        db.execute("UPDATE auto_service_location SET disabled_at=now() WHERE id=%s",(loc,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                add_bin(db,org,loc,"B-02")
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                add_lot(db,org,loc,item,bin_id,"BATCH-2")


def test_transaction_rollback_of_bin_and_lot():
    with psycopg.connect(DSN) as db:
        org,loc,_=_org_and_bay(db)
        item=sku(db,org,loc)
        ident=None
        with pytest.raises(RuntimeError):
            with db.transaction():
                ident=add_bin(db,org,loc)
                add_lot(db,org,loc,item,ident)
                raise RuntimeError("rollback")
        assert db.execute("SELECT count(*) FROM auto_stock_bin WHERE id=%s",(ident,)).fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM auto_stock_lot WHERE sku_id=%s",(item,)).fetchone()[0]==0

"""Synthetic financial balance cannot bypass manager, tenant, or proof revocation."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_order_financial_read import get_work_order_balance
from roboport_api.auto_stock_service import AutoStockDenied
from test_pg_auto_order_customer_read import prepare
from test_pg_baseline import DSN


def test_manager_only_balance_with_revocation_and_foreign_scope():
    d, org, loc, order, foreign, foreign_loc = prepare()
    with psycopg.connect(DSN) as db:
        grant = uuid4()
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'MANAGER')""", (grant,org,loc,d["manager"]))
        db.execute("SELECT record_auto_work_order_financial(%s,%s,'CHARGE',%s,%s)",
                   (order,uuid4(),7200,"synthetic services"))
        db.execute("SELECT record_auto_work_order_financial(%s,%s,'CREDIT',%s,%s)",
                   (order,uuid4(),200,"synthetic revision"))
        token = d["tokens"][d["manager"]]
        assert get_work_order_balance(db,token,org,loc,order) == {
            "orderId":str(order),"balanceMinor":7000,
            "currency":None,"syntheticOnly":True}
        for wrong in ((foreign,foreign_loc,order),(org,loc,uuid4())):
            with pytest.raises(AutoStockDenied,match="FORBIDDEN"):
                get_work_order_balance(db,token,*wrong)
        with pytest.raises(AutoStockDenied,match="FORBIDDEN"):
            get_work_order_balance(db,d["tokens"][d["intake"]],org,loc,order)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(grant,))
        with pytest.raises(AutoStockDenied,match="FORBIDDEN"):
            get_work_order_balance(db,token,org,loc,order)


def test_financial_read_requires_live_customer_proof():
    d,org,loc,order,_,_ = prepare()
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'MANAGER')""", (uuid4(),org,loc,d["manager"]))
        token=d["tokens"][d["manager"]]
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
        with pytest.raises(AutoStockDenied,match="FORBIDDEN"):
            get_work_order_balance(db,token,org,loc,order)


def test_balance_invalid_scope_rejected_before_db():
    with pytest.raises(AutoStockDenied,match="INVALID_INPUT"):
        get_work_order_balance(None,"token",uuid4(),uuid4(),"invalid")
    with pytest.raises(AutoStockDenied,match="FORBIDDEN"):
        get_work_order_balance(None," ",uuid4(),uuid4(),uuid4())

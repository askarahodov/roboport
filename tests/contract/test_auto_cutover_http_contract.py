"""Строгий контракт частной загрузки; не публикуется в OpenAPI."""
import base64
from hashlib import sha256
from uuid import UUID
import pytest

from roboport_api.auto_cutover_http import prepare_bundle,CutoverInput,ROOT
from roboport_api.main import app

ORG=UUID("11111111-1111-4111-8111-111111111111")
LOC=UUID("22222222-2222-4222-8222-222222222222")


def sample():
    texts={
        "vehicles.csv":f"record_type,source_id,organization_id,location_id,vin,make,model,source_system\nVEHICLE,SYN-1,{ORG},{LOC},ABCDEF12345678901,SYNTHETIC,TEST CAR,DEMO\n",
        "services.csv":"record_type,source_system,source_id,code,title,duration_minutes\nSERVICE,DEMO,SYN-1,SYN-WORK,SYNTHETIC WORK,60\n",
        "inventory.csv":f"record_type,source_id,organization_id,location_id,article,quantity\nSKU,SYN-1,{ORG},{LOC},SYN-PART,0\nSTOCK,SYN-2,{ORG},{LOC},SYN-PART,3\n",
    }
    return {"syntheticOnly":True,"files":{
        name:{"base64":base64.b64encode(value.encode()).decode(),
              "sha256":sha256(value.encode()).hexdigest()} for name,value in texts.items()}}


def test_scope_and_byte_bound_identity_and_private_route():
    body=sample()
    _,_,counts,digest,batches=prepare_bundle(body,ORG,LOC,"preview")
    assert counts=={"vehicles":1,"services":1,"stockRows":2,"skus":1,"receipts":1}
    assert prepare_bundle({**body,"bundleSha256":digest},ORG,LOC,"commit")[4]==batches
    for bad in ({**body,"syntheticOnly":1},{**body,"bundleSha256":digest},
                {**body,"files":{**body["files"],"extra.csv":{}}}):
        with pytest.raises(CutoverInput):
            prepare_bundle(bad,ORG,LOC,"preview")
    with pytest.raises(CutoverInput):
        prepare_bundle(body,LOC,ORG,"preview")
    with pytest.raises(CutoverInput):
        prepare_bundle({**body,"bundleSha256":"0"*64},ORG,LOC,"commit")
    body["files"]["vehicles.csv"]["sha256"]="0"*64
    with pytest.raises(CutoverInput):
        prepare_bundle(body,ORG,LOC,"preview")
    assert ROOT+"/{operation}" not in app.openapi()["paths"]

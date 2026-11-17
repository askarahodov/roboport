"""PG17 + HTTPS: полный неизменяемый ZIP после технического закрытия ЗН.

Ни загрузка архива, ни проверка снимка не разрешают настоящую выдачу.
"""
from hashlib import sha256
from io import BytesIO
import json
from uuid import UUID,uuid4
from zipfile import ZipFile

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_intake_act import sanitize_png
from test_pg_auto_intake_act_http import synthetic_png
from test_pg_auto_document_lifecycle_http import ready,path,event
from test_pg_auto_work_order_execution_http import session
from test_pg_baseline import DSN


def test_closed_document_dossier_has_all_evidence_and_current_acl(monkeypatch):
    d,org,loc,order,act,preview=ready(monkeypatch,with_photo=True)
    http=TestClient(app,base_url="https://127.0.0.1")
    base=path(org,loc,order)
    endpoint=base+"/technical-dossier.zip"
    manager=session(http,d,d["intake"])
    assert http.get(endpoint).status_code==409  # DRAFT: архив недоступен.
    assert http.get(endpoint).json()["code"]=="TECHNICAL_CLOSE_REQUIRED"
    assert event(http,base,manager,act,preview,"POSTED").status_code==201
    assert http.get(endpoint).status_code==409  # POSTED без отметки клиента.
    client=session(http,d,d["client_a"])
    assert event(http,base,client,act,preview,"CLIENT_ACK").status_code==201
    assert http.get(endpoint).status_code==409  # Ещё не CLOSED_TECHNICAL.
    manager=session(http,d,d["intake"])
    assert event(http,base,manager,act,preview,"CLOSED_TECHNICAL").status_code==201
    downloaded=http.get(endpoint)
    assert downloaded.status_code==200,downloaded.text[:140] if downloaded.status_code!=200 else ""
    assert downloaded.headers["content-type"]=="application/zip"
    assert downloaded.headers["cache-control"]=="no-store, private"
    assert downloaded.headers["x-content-type-options"]=="nosniff"
    assert downloaded.headers["cross-origin-resource-policy"]=="same-origin"
    assert "attachment" in downloaded.headers["content-disposition"]

    with ZipFile(BytesIO(downloaded.content)) as archive:
        names=archive.namelist()
        assert names==["manifest.json","preview.json","document-events.json",
                       "settlement.json","intake-evidence.zip"]
        manifest=json.loads(archive.read("manifest.json"))
        assert manifest["schemaVersion"]==1
        assert manifest["documentStatus"]=="CLOSED_TECHNICAL"
        assert manifest["syntheticOnly"] is True
        assert manifest["releaseAllowed"] is False
        assert manifest["physicalHandover"] is False
        assert manifest["legalSignatureStatus"]=="NOT_IMPLEMENTED"
        assert manifest["workOrderId"]==str(order)
        assert manifest["previewId"]==preview["id"]
        assert manifest["previewDigest"]==preview["digest"]
        assert manifest["intakeActId"]==act["id"]
        assert manifest["intakeDigest"]==act["sha256"]
        assert [item["path"] for item in manifest["files"]]==names[1:]
        for item in manifest["files"]:
            data=archive.read(item["path"])
            assert item["bytes"]==len(data)
            assert sha256(data).hexdigest()==item["sha256"]

        snapshot=json.loads(archive.read("preview.json"))
        assert snapshot==preview["snapshot"]
        assert snapshot["simulatedDueMinor"]==0
        assert snapshot["releaseAllowed"] is False
        assert snapshot["legalGatePassed"] is False
        actions=json.loads(archive.read("document-events.json"))
        assert [item["action"] for item in actions]==[
            "POSTED","CLIENT_ACK","CLOSED_TECHNICAL"]
        assert all(item["previewDigest"]==preview["digest"] for item in actions)
        assert all(item["intakeDigest"]==act["sha256"] for item in actions)
        settlement=json.loads(archive.read("settlement.json"))
        assert settlement["simulatedPaidMinor"]==2500
        assert settlement["simulatedRefundedMinor"]==0
        assert settlement["simulatedDueMinor"]==0
        assert settlement["executionStatus"]=="QC_ACCEPTED"
        assert settlement["outstandingReserves"]==[]
        assert settlement["unreceivedMaterialCount"]==0

        with ZipFile(BytesIO(archive.read("intake-evidence.zip"))) as evidence:
            assert evidence.namelist()==["manifest.json","photos/1.png"]
            photographed=json.loads(evidence.read("manifest.json"))
            assert photographed["act"]["id"]==act["id"]
            assert photographed["act"]["review"]["decision"]=="ACKNOWLEDGED"
            clean,_,_=sanitize_png(synthetic_png())
            assert evidence.read("photos/1.png")==clean
            assert photographed["files"][0]["sha256"]==sha256(clean).hexdigest()

    # Байты детерминированны; собственный клиент получает ту же версию.
    assert http.get(endpoint).content==downloaded.content
    owner=session(http,d,d["client_a"])
    assert http.get(endpoint).content==downloaded.content
    assert http.get(endpoint.replace(str(loc),str(uuid4()))).status_code==403
    assert http.get(endpoint.replace(str(order),str(uuid4()))).status_code==403
    assert http.get(endpoint.replace(str(org),str(uuid4()))).status_code==403
    assert http.get(base.replace(str(order),str(uuid4()))).status_code==403
    assert http.get(base.replace(str(loc),str(uuid4()))).status_code==403

    other=session(http,d,d["client_b"])
    assert http.get(endpoint).status_code==403
    http.cookies.clear()
    assert http.get(endpoint).status_code in (401,403)

    # После отзыва исходного verified proof точный URL не сохраняет права.
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    owner=session(http,d,d["client_a"])
    assert http.get(endpoint).status_code==403
    manager=session(http,d,d["intake"])
    assert http.get(endpoint).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_order_demo_document_event
          WHERE work_order_id=%s""",(order,)).fetchone()==(3,)
        assert db.execute("""SELECT count(*) FROM auto_order_demo_handover_preview
          WHERE work_order_id=%s""",(order,)).fetchone()==(1,)


def test_disputed_document_never_exports_as_closed_dossier(monkeypatch):
    d,org,loc,order,act,preview=ready(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    base=path(org,loc,order)
    manager=session(http,d,d["intake"])
    assert event(http,base,manager,act,preview,"POSTED").status_code==201
    client=session(http,d,d["client_a"])
    assert event(http,base,client,act,preview,"CLIENT_DISPUTE").status_code==201
    assert http.get(base+"/technical-dossier.zip").status_code==409
    assert http.get(base+"/technical-dossier.zip").json()["code"]=="TECHNICAL_CLOSE_REQUIRED"

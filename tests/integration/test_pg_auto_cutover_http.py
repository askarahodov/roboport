"""PG17/HTTPS: браузерный контракт репетиции без реальных данных."""
import base64
from hashlib import sha256
import io
import json
from uuid import UUID
from zipfile import ZipFile

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_synthetic_cutover_export import verify_synthetic_cutover_export
from test_pg_auto_synthetic_cutover import _setup
from test_pg_baseline import DSN
from test_pg_auto_customer_booking_http import _auth


def test_whole_private_cutover_preview_commit_recovery_export_and_revocation(tmp_path,monkeypatch):
    d,org,loc,_,_,warehouse,sources=_setup(tmp_path/"sources")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    monkeypatch.delenv("ROBOport_SYNTHETIC_CUTOVER_UI",raising=False)
    http=TestClient(app,base_url="https://127.0.0.1")
    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/cutover/"
    raw={"vehicles.csv":sources.vehicle_path.read_bytes(),
         "services.csv":sources.service_path.read_bytes(),
         "inventory.csv":sources.inventory_path.read_bytes().replace(
             b"FILTER-001",b"SYN-FILTER-001").replace(b"COOLANT-001",b"SYN-COOLANT-001")}
    def body(files):
        return {"syntheticOnly":True,"files":{
            name:{"base64":base64.b64encode(value).decode(),
                  "sha256":sha256(value).hexdigest()} for name,value in files.items()}}
    initial=body(raw)
    headers=_auth(http,d,d["intake"])
    assert http.post(root+"preview",json=initial,headers=headers).status_code==404
    monkeypatch.setenv("ROBOport_SYNTHETIC_CUTOVER_UI","1")
    assert http.post(root+"preview",json=initial).status_code==403
    _auth(http,d,d["client_a"])
    assert http.post(root+"preview",json=initial,headers=_auth(http,d,d["client_a"])).status_code==403
    headers=_auth(http,d,d["intake"])
    assert http.post(root+"preview",json={**initial,"syntheticOnly":False},headers=headers).status_code==422
    assert http.post(root+"preview",json=body({**raw,"vehicles.csv":raw["vehicles.csv"].replace(
        b"SYNTHETIC",b"NOT-SYNTHETIC")}),headers=headers).status_code==422
    # Подготовка смешанного формата использует тот же ACL и не пишет staging.
    import csv
    from xml.sax.saxutils import escape
    workbook = io.BytesIO()
    rows = list(csv.reader(io.StringIO(raw["vehicles.csv"].decode())))
    aliases = ["F" + str(i) for i in range(len(rows[0]))]
    grid = [aliases[::-1], *[row[::-1] for row in rows[1:]]]
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with ZipFile(workbook, "w") as book:
        book.writestr("[Content_Types].xml", "<Types/>")
        book.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}"><sheets><sheet name="Import" sheetId="1"/></sheets></workbook>')
        cells = []
        for number, row in enumerate(grid, 1):
            values = "".join(f'<c r="{chr(65+i)}{number}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
                             for i, value in enumerate(row))
            cells.append(f'<row r="{number}">{values}</row>')
        book.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData>{"".join(cells)}</sheetData></worksheet>')
    prepare = {"syntheticOnly": True, "files": {name: {**entry, "format": "CSV", "mapping": None}
                for name, entry in initial["files"].items()}}
    content = workbook.getvalue()
    prepare["files"]["vehicles.csv"] = {"base64": base64.b64encode(content).decode(),
        "sha256": sha256(content).hexdigest(), "format": "XLSX", "mapping": dict(zip(aliases, rows[0]))}
    assert http.post(root+"prepare", json=prepare).status_code == 403
    prepared = http.post(root+"prepare", json=prepare, headers=headers)
    assert prepared.status_code == 200, prepared.text
    proof = prepared.json()
    assert proof["writes"] == 0 and proof["productionApproved"] is False
    assert prepared.headers["cache-control"] == "no-store"
    initial = {"syntheticOnly": True, "files": proof["files"]}
    raw = {name: base64.b64decode(entry["base64"]) for name, entry in initial["files"].items()}
    preview=http.post(root+"preview",json=initial,headers=headers)
    assert preview.status_code==200,preview.text
    verified=preview.json()
    assert verified["writes"]==0 and verified["productionApproved"] is False
    assert verified["counts"]=={"vehicles":1,"services":2,"stockRows":5,"skus":2,"receipts":2}
    assert preview.headers["cache-control"]=="no-store"
    batches={name:UUID(value) for name,value in verified["batchIds"].items()}
    def staged(db):
        return tuple(db.execute(f"SELECT count(*) FROM {table} WHERE id=%s",(batches[name],)).fetchone()[0]
                     for name,table in (("vehicles.csv","auto_vehicle_migration_batch"),
                       ("services.csv","auto_service_import_batch"),("inventory.csv","auto_migration_staging_batch")))
    with psycopg.connect(DSN) as db:
        assert staged(db)==(0,0,0)
    confirmed={**initial,"bundleSha256":verified["bundleSha256"]}
    assert http.post(root+"reconcile",json=confirmed,headers=headers).status_code==409
    changed=body({**raw,"inventory.csv":raw["inventory.csv"].replace(b",3\r\n",b",4\r\n")})
    assert http.post(root+"commit",json={**changed,"bundleSha256":verified["bundleSha256"]},
                     headers=headers).status_code==422
    commit=http.post(root+"commit",json=confirmed,headers=headers)
    assert commit.status_code==200,commit.text
    assert commit.json()["writes"]>0 and commit.json()["replayed"] is False
    replay=http.post(root+"commit",json=confirmed,headers=headers)
    assert replay.status_code==200,replay.text
    assert replay.json()["writes"]==0 and replay.json()["replayed"] is True
    checked=http.post(root+"reconcile",json=confirmed,headers=headers)
    assert checked.status_code==200 and checked.json()["counts"]==verified["counts"]
    assert checked.json()["writes"]==0
    archive=http.post(root+"export",json=confirmed,headers=headers)
    assert archive.status_code==200,archive.text
    assert archive.headers["x-cutover-sha256"]==sha256(archive.content).hexdigest()
    destination=tmp_path/"export"
    destination.mkdir(mode=0o700)
    with ZipFile(io.BytesIO(archive.content)) as zip_file:
        assert set(zip_file.namelist())==set(raw)|{"manifest.json"}
        for name in zip_file.namelist():
            data=zip_file.read(name)
            if name in raw:
                assert data==raw[name]
            path=destination/name
            path.write_bytes(data)
            path.chmod(0o600)
    proof=verify_synthetic_cutover_export(destination,
          expected_manifest_sha256=archive.headers["x-cutover-manifest-sha256"])
    assert proof["manifestDigestVerified"] and proof["counts"]==verified["counts"]
    with psycopg.connect(DSN) as db:
        assert staged(db)==(1,1,1)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(warehouse,))
    assert http.post(root+"prepare",json=prepare,headers=headers).status_code==403
    for action in ("preview","commit","reconcile","export"):
        response=http.post(root+action,json=initial if action=="preview" else confirmed,headers=headers)
        assert response.status_code==403
        assert "X-Cutover-SHA256" not in response.headers

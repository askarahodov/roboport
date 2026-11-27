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



def test_mapped_sources_preserve_scope_and_canonical_identity():
    from roboport_api.auto_cutover_prepare import normalize_sources, PrepareDenied
    import csv
    import io
    original = sample()
    inputs = {}
    for name, entry in original["files"].items():
        rows = list(csv.reader(io.StringIO(base64.b64decode(entry["base64"]).decode())))
        aliases = ["F" + str(i) for i in range(len(rows[0]))]
        mapping = dict(zip(aliases, rows[0]))
        stream = io.StringIO(newline="")
        csv.writer(stream).writerows([aliases[::-1], *[row[::-1] for row in rows[1:]]])
        raw = stream.getvalue().encode()
        inputs[name] = {"base64": base64.b64encode(raw).decode(),
                        "sha256": sha256(raw).hexdigest(), "format": "CSV", "mapping": mapping}
    body = {"syntheticOnly": True, "files": inputs}
    canonical = normalize_sources(body)
    assert normalize_sources(body) == canonical
    assert prepare_bundle(canonical, ORG, LOC, "preview")[2] == prepare_bundle(original, ORG, LOC, "preview")[2]
    with pytest.raises(CutoverInput):
        prepare_bundle(canonical, LOC, ORG, "preview")
    for invalid in ({"F0": "record_type"}, {"F0": "owner"}):
        with pytest.raises(PrepareDenied):
            normalize_sources({**body, "files": {**inputs, "vehicles.csv": {
                **inputs["vehicles.csv"], "mapping": invalid}}})
    with pytest.raises(PrepareDenied):
        normalize_sources({**body, "files": {**inputs, "inventory.csv": {
            **inputs["inventory.csv"], "sha256": "0"*64}}})


def test_mixed_xlsx_preparation_and_unsafe_formula(tmp_path):
    import csv
    import io
    from test_auto_synthetic_xlsx import workbook
    from roboport_api.auto_cutover_prepare import normalize_sources, PrepareDenied
    original = sample()
    files = {name: {**entry, "format": "CSV", "mapping": None}
             for name, entry in original["files"].items()}
    # Vehicle and service workbooks reuse the existing strict OOXML parser.
    for name in ("vehicles.csv", "services.csv"):
        rows = list(csv.reader(io.StringIO(base64.b64decode(files[name]["base64"]).decode())))
        path = tmp_path / (name + ".xlsx")
        workbook(path, rows[0], rows[1])
        raw = path.read_bytes()
        files[name] = {"base64": base64.b64encode(raw).decode(), "sha256": sha256(raw).hexdigest(),
                       "format": "XLSX", "mapping": None}
    body = {"syntheticOnly": True, "files": files}
    assert prepare_bundle(normalize_sources(body), ORG, LOC, "preview")[2]["receipts"] == 1
    # Inventory supports the same workbook path; a SKU-only file has zero receipts.
    rows = list(csv.reader(io.StringIO(base64.b64decode(files["inventory.csv"]["base64"]).decode())))
    path = tmp_path / "inventory.xlsx"
    workbook(path, rows[0], rows[1])
    raw = path.read_bytes()
    files["inventory.csv"] = {"base64": base64.b64encode(raw).decode(), "sha256": sha256(raw).hexdigest(),
                              "format": "XLSX", "mapping": None}
    assert prepare_bundle(normalize_sources(body), ORG, LOC, "preview")[2]["receipts"] == 0
    workbook(path, rows[0], rows[1], formula=True)
    raw = path.read_bytes()
    files["inventory.csv"].update(base64=base64.b64encode(raw).decode(), sha256=sha256(raw).hexdigest())
    with pytest.raises(PrepareDenied):
        normalize_sources(body)


def test_prepare_preserves_unmapped_csv_and_denies_extra_columns():
    from roboport_api.auto_cutover_prepare import normalize_sources, PrepareDenied
    original = sample()
    body = {"syntheticOnly": True, "files": {
        name: {**entry, "format": "CSV", "mapping": None}
        for name, entry in original["files"].items()}}
    assert normalize_sources(body) == original
    raw = base64.b64decode(body["files"]["vehicles.csv"]["base64"])
    raw = raw.replace(b"source_system\n", b"source_system,owner\n").rstrip(b"\n") + b",PERSON\n"
    body["files"]["vehicles.csv"].update(base64=base64.b64encode(raw).decode(), sha256=sha256(raw).hexdigest())
    with pytest.raises(PrepareDenied):
        normalize_sources(body)

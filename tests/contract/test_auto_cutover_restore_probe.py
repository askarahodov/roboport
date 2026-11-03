"""Contract checks for the full synthetic cutover clone-only recovery gate."""
import json
from uuid import uuid4

import pytest

from scripts.auto_cutover_restore_probe import FIELDS,digest,load,main


def _manifest():
    return {"version":1,"ids":{k:str(uuid4()) for k in FIELDS},"sha256":"a"*64}


def test_cutover_sha_is_canonical_and_sensitive_to_every_receipt():
    original={"vehicle":[["v1"]],"stock":[["RECEIPT",3]]}
    reordered={"stock":[["RECEIPT",3]],"vehicle":[["v1"]]}
    tampered={"vehicle":[["v1"]],"stock":[["RECEIPT",2]]}
    assert digest(original)==digest(reordered)
    assert digest(original)!=digest(tampered)
    assert len(digest(original))==64


def test_cutover_restore_manifest_requires_exact_source_graph(tmp_path):
    path=tmp_path/"proof.json"
    good=_manifest()
    path.write_text(json.dumps(good))
    assert load(path)==good
    for wrong in (
        {**good,"version":2},
        {**good,"ids":{**good["ids"],"vehicleBatch":"invalid"}},
        {**good,"ids":{k:v for k,v in good["ids"].items() if k!="serviceBatch"}},
        {**good,"ids":{**good["ids"],"other":str(uuid4())}},
        {**good,"sha256":"not-a-digest"},
        {**good,"sha256":"Z"*64},
        {**good,"extra":"not-allowed"},
    ):
        path.write_text(json.dumps(wrong))
        with pytest.raises((ValueError,TypeError)):
            load(path)


def test_cutover_clone_only_and_no_existing_manifest_overwrite(tmp_path):
    path=tmp_path/"existing.json"
    path.write_text(json.dumps(_manifest()))
    with pytest.raises(SystemExit):
        main(["verify-clone","--manifest",str(path),"--database","roboport"])
    with pytest.raises(SystemExit):
        main(["seed","--manifest",str(path),"--database","roboport_restored"])
    # Main returns an error without attempting to connect to any database
    # or replacing an existing source manifest.
    assert main(["seed","--manifest",str(path),"--database","roboport"])==1

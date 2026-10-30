from copy import deepcopy
from scripts.check_openapi_compatibility import regressions


def sample():
    return {"paths": {"/health": {"get": {"responses": {"200": {"description": "ok"}, "503": {"description": "down"}}}}},
            "components": {"schemas": {"Health": {"type": "object", "properties": {"status": {"type": "string"}}}}}}


def test_additions_preserve_existing_public_contract():
    old = sample()
    new = deepcopy(old)
    new["paths"]["/new"] = {"get": {"responses": {"200": {"description": "ok"}}}}
    new["components"]["schemas"]["Health"]["properties"]["extra"] = {"type": "string"}
    assert regressions(old, new) == []


def test_removal_of_path_method_status_schema_and_property_blocks():
    old = sample()
    for mutation, reason in (
        (lambda doc: doc["paths"].pop("/health"), "REMOVED_PATH:/health"),
        (lambda doc: doc["paths"]["/health"].pop("get"), "REMOVED_OPERATION:GET:/health"),
        (lambda doc: doc["paths"]["/health"]["get"]["responses"].pop("503"), "REMOVED_RESPONSE:GET:/health:503"),
        (lambda doc: doc["components"]["schemas"].pop("Health"), "REMOVED_SCHEMA:Health"),
        (lambda doc: doc["components"]["schemas"]["Health"]["properties"].pop("status"), "REMOVED_PROPERTY:Health.status"),
    ):
        changed = deepcopy(old)
        mutation(changed)
        assert reason in regressions(old, changed)


def test_invalid_doc_does_not_pass():
    assert regressions(None, sample())
    assert regressions(sample(), {"paths": []})

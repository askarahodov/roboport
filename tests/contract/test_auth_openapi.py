"""Контрактные требования локальных opaque cookie-сессий (#13, #15)."""
from pathlib import Path
import yaml
import pytest

SPEC=yaml.safe_load((Path(__file__).resolve().parents[2]/"contracts/openapi.yaml").read_text(encoding="utf-8"))
AUTH="/api/v1/auth/"
METHOD={"login":"post","logout":"post","me":"get","change-password":"post"}


def check_auth(spec):
    for name,method in METHOD.items():
        operation=spec["paths"][AUTH+name][method]
        assert operation["operationId"]
        responses=operation["responses"]
        assert "200" in responses and "401" in responses and "403" in responses
        if name=="login":
            assert operation["security"]==[]
            assert "429" in responses
            assert "Set-Cookie" in responses["200"]["headers"]
            assert operation["requestBody"]["required"]
        else:
            assert {"localSession":[]} in operation["security"]
        if name in ("logout","change-password"):
            assert any(p.get("name")=="X-CSRF-Token" and p.get("required") for p in operation["parameters"])
        if name=="change-password":
            assert "422" in responses and "429" in responses
    assert spec["components"]["securitySchemes"]["localSession"]["name"]=="rp_session"


def test_auth_contract_guards():
    check_auth(SPEC)


def test_deleting_csrf_guard_breaks_contract_test():
    from copy import deepcopy
    corrupted=deepcopy(SPEC)
    corrupted["paths"][AUTH+"change-password"]["post"]["parameters"]=[]
    with pytest.raises(AssertionError):
        check_auth(corrupted)

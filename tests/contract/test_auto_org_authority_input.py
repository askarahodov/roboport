"""Fail-closed pre-DB organization authority input validation (#166)."""
from uuid import uuid4
import pytest
from roboport_api.auto_org_authority import organization_role_allows


class NoDatabase:
    def execute(self, *_args, **_kwargs):
        raise AssertionError("bad authority request reached database")


@pytest.mark.parametrize("bad", [None, "", 123, True, [], {}, ["MANAGER"]])
def test_invalid_role_denied_without_database_access(bad):
    assert not organization_role_allows(NoDatabase(), "synthetic-token", uuid4(), bad)


@pytest.mark.parametrize("bad", [None, "", 0, True, [], {}])
def test_invalid_token_denied_without_database_access(bad):
    assert not organization_role_allows(NoDatabase(), bad, uuid4(), "MANAGER")


@pytest.mark.parametrize("bad", [None, "", 123, [], {}, str(uuid4())])
def test_invalid_org_denied_without_database_access(bad):
    assert not organization_role_allows(NoDatabase(), "synthetic-token", bad, "MANAGER")


@pytest.mark.parametrize("bad", ["", 123, [], {}, str(uuid4())])
def test_invalid_location_denied_without_database_access(bad):
    assert not organization_role_allows(
        NoDatabase(), "synthetic-token", uuid4(), "MANAGER", bad
    )


@pytest.mark.parametrize("bad", [None, 0, "false", [], {}])
def test_invalid_mutation_flag_denied_without_database_access(bad):
    assert not organization_role_allows(
        NoDatabase(), "synthetic-token", uuid4(), "MANAGER", for_mutation=bad
    )


def test_unknown_role_denied_before_session_query():
    assert not organization_role_allows(NoDatabase(), "synthetic-token", uuid4(), "ADMIN")

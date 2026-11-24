"""Offline adapter contract never invokes a network or contains recipient information."""
from dataclasses import asdict
import pytest
from roboport_api.auto_crm_provider import PROVIDERS,OUTCOMES,LeadDenied
from roboport_api.auto_crm_provider_http import ROOT,LEAD,CONSENT
from roboport_api.main import app


@pytest.mark.parametrize("channel",["CALL","EMAIL","SMS","MESSENGER"])
def test_offline_provider_receipts(channel):
    for outcome in OUTCOMES:
        receipt=asdict(PROVIDERS[channel].simulate(outcome))
        assert receipt=={"outcome":outcome,"retryable":outcome in ("TIMEOUT","RATE_LIMIT"),
                         "provider_code":"FIXTURE_"+channel}
    with pytest.raises(LeadDenied):
        PROVIDERS[channel].simulate("REAL_SEND")


def test_no_provider_routes_in_public_schema():
    paths=app.openapi()["paths"]
    assert CONSENT not in paths
    assert ROOT+"/mock-calls" not in paths
    assert LEAD+"/provider-messages/{message_id}/attempts" not in paths

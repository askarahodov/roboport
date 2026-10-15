from fastapi.testclient import TestClient
from roboport_api.main import app

client=TestClient(app)

def test_liveness_and_meta_are_synthetic():
    a=client.get('/health/live'); assert a.status_code==200 and a.json()=={'status':'alive'}
    b=client.get('/api/v1/meta'); assert b.status_code==200
    assert b.json()['production_ready'] is False
    assert b.json()['environment']=='development'

def test_readiness_fails_closed():
    r=client.get('/health/ready'); assert r.status_code==503
    assert r.json()['code']=='HTTP_503'

def test_private_endpoint_is_not_fake_logged_in():
    r=client.get('/api/v1/customer/me'); assert r.status_code==401
    assert r.json()['message']=='Требуется вход'
    assert r.headers['cache-control']=='no-store'
    assert r.headers['X-Request-ID']==r.json()['requestId']

def test_x_request_id_cannot_be_spoofed():
    r=client.get('/health/live',headers={'X-Request-ID':'customer-secret'});
    assert r.headers['X-Request-ID']!='customer-secret'

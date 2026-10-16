"""Real PG OIDC login: signed fake IdP token, prelinked user, no implicit ACL."""
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import base64
import json

import psycopg
from Crypto.PublicKey import RSA
from Crypto.Hash import SHA256
from Crypto.Signature import pkcs1_15
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api import oidc_http
from test_pg_baseline import DSN


def enc(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_oidc_callback_issues_only_prelinked_session(monkeypatch):
    issuer="https://idp.example.invalid/realms/demo"
    subject="synthetic-sub-"+uuid4().hex
    user=uuid4()
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(user,user.hex))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",(uuid4(),user))
        db.execute("INSERT INTO oidc_identity(issuer,subject,user_id) VALUES(%s,%s,%s)",(issuer,subject,user))
    env={
      "ROBOport_OIDC_ENABLED":"1","ROBOport_SYNTHETIC_AUTH_API":"1","ROBOport_DATABASE_URL":DSN,
      "ROBOport_OIDC_ISSUER":issuer,
      "ROBOport_OIDC_AUTHORIZE":issuer+"/protocol/openid-connect/auth",
      "ROBOport_OIDC_TOKEN":issuer+"/protocol/openid-connect/token",
      "ROBOport_OIDC_JWKS":issuer+"/protocol/openid-connect/certs",
      "ROBOport_OIDC_CLIENT":"synthetic-client",
      "ROBOport_OIDC_STATE_KEY":"only-synthetic-flow-key-very-long-and-randomlike-123456",
      "ROBOport_OIDC_REDIRECT":"https://127.0.0.1/api/v1/dev/oidc/callback",
    }
    for k,v in env.items():monkeypatch.setenv(k,v)
    private=RSA.generate(2048)
    public=private.publickey()
    jwks={"keys":[{"kid":"dev-test","kty":"RSA","use":"sig","alg":"RS256",
      "n":enc(public.n.to_bytes(256,"big")),"e":enc(public.e.to_bytes(3,"big"))}]}
    monkeypatch.setattr(oidc_http,"_document",lambda *args,**kwargs:jwks)
    payloads=[]
    def exchange(*args,**kwargs):
        nonce=payloads[-1]
        header=enc(json.dumps({"kid":"dev-test","alg":"RS256"}).encode())
        body=enc(json.dumps({"iss":issuer,"aud":"synthetic-client","sub":subject,
          "nonce":nonce,"iat":1700000000,"exp":4102444800}).encode())
        signature=enc(pkcs1_15.new(private).sign(SHA256.new((header+"."+body).encode())))
        return {"id_token":header+"."+body+"."+signature}
    monkeypatch.setattr(oidc_http,"exchange_code",exchange)
    http=TestClient(app,base_url="https://127.0.0.1")
    begin=http.get("/api/v1/dev/oidc/start",follow_redirects=False)
    assert begin.status_code==302
    flow=oidc_http._read(http.cookies.get(oidc_http.COOKIE),env["ROBOport_OIDC_STATE_KEY"])
    payloads.append(flow["nonce"])
    path="/api/v1/dev/oidc/callback?code=synthetic-code&state="+flow["state"]
    done=http.get(path,follow_redirects=False)
    assert done.status_code==303,done.text
    assert done.headers["location"]=="/account"
    assert http.cookies.get("rp_session")
    assert oidc_http.COOKIE not in http.cookies
    assert http.get("/api/v1/auth/me").json()["subjectId"]==str(user)
    # No implicit customer ownership from a valid OIDC subject.
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    assert http.get("/api/v1/dev/account/tickets").json()["items"]==[]
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_session SET revoked_at=now() WHERE user_id=%s",(user,))
    assert http.get("/api/v1/auth/me").status_code==401

"""Real RSA signature checks using synthetic, in-memory JWKS: no external IdP secrets."""
import base64
import json
from urllib.parse import parse_qs,urlsplit

import pytest
from Crypto.PublicKey import RSA
from Crypto.Hash import SHA256
from Crypto.Signature import pkcs1_15
from roboport_api.oidc import OIDCRejected, new_pkce, authorize_url, validate_id_token


def enc(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def signed(private,claims,alg="RS256"):
    header=enc(json.dumps({"alg":alg,"kid":"dev-test"}).encode())
    body=enc(json.dumps(claims).encode())
    signature=pkcs1_15.new(private).sign(SHA256.new((header+"."+body).encode()))
    return header+"."+body+"."+enc(signature)


def test_pkce_authorization_url_strong_random_challenge_and_https_only():
    verifier,challenge,state,nonce=new_pkce()
    assert len(verifier)>43 and len(state)>24 and len(nonce)>24
    assert challenge==enc(SHA256.new(verifier.encode()).digest())
    url=authorize_url("https://idp.example.invalid/auth","synthetic-client",
      "https://client.example.invalid/callback",challenge,state,nonce)
    query=parse_qs(urlsplit(url).query)
    assert query["response_type"]==["code"]
    assert query["code_challenge_method"]==["S256"]
    assert query["state"]==[state]
    with pytest.raises(OIDCRejected):
        authorize_url("http://idp.example.invalid/auth","synthetic-client",
          "https://client.example.invalid/callback",challenge,state,nonce)


def test_signed_id_token_rejects_forgery_expiry_nonce_audience_and_alg():
    private=RSA.generate(2048)
    public=private.publickey()
    jwks={"keys":[{"kid":"dev-test","kty":"RSA","use":"sig","alg":"RS256",
         "n":enc(public.n.to_bytes((public.n.bit_length()+7)//8,"big")),
         "e":enc(public.e.to_bytes((public.e.bit_length()+7)//8,"big"))}]}
    expected={"iss":"https://idp.example.invalid/realms/demo","aud":"synthetic-client",
              "sub":"synthetic-subject-A","nonce":"synthetic-nonce","iat":1000,"exp":2000}
    token=signed(private,expected)
    verify=lambda t:validate_id_token(t,issuer=expected["iss"],audience="synthetic-client",
                                       nonce=expected["nonce"],jwks=jwks,now=1500)
    assert verify(token)["sub"]=="synthetic-subject-A"
    for delta in ({"iss":"https://attacker.invalid"},{"aud":"other-client"},{"exp":1499},
                  {"iat":1601},{"nonce":"attacker-nonce"},{"sub":""}):
        with pytest.raises(OIDCRejected):
            verify(signed(private,{**expected,**delta}))
    with pytest.raises(OIDCRejected):
        verify(signed(RSA.generate(2048),expected))
    with pytest.raises(OIDCRejected):
        verify(signed(private,expected,"none"))
    with pytest.raises(OIDCRejected):
        validate_id_token(token,issuer=expected["iss"],audience="synthetic-client",
                          nonce=expected["nonce"],jwks={"keys":[]},now=1500)


def test_multiple_audiences_require_authorized_party():
    private=RSA.generate(2048)
    public=private.publickey()
    jwks={"keys":[{"kid":"dev-test","kty":"RSA","n":enc(public.n.to_bytes(256,"big")),
                   "e":enc(public.e.to_bytes(3,"big"))}]}
    claims={"iss":"https://idp.invalid","aud":["synthetic-client","second-app"],
            "sub":"client-a","nonce":"n","iat":1000,"exp":2000}
    with pytest.raises(OIDCRejected):
        validate_id_token(signed(private,claims),issuer=claims["iss"],
                          audience="synthetic-client",nonce="n",jwks=jwks,now=1500)
    claims["azp"]="synthetic-client"
    assert validate_id_token(signed(private,claims),issuer=claims["iss"],
                             audience="synthetic-client",nonce="n",jwks=jwks,now=1500)["sub"]=="client-a"


def test_live_keycloak_ci_tracks_automotive_bola_after_robot_cutover():
    """The live IdP test must not silently drift back to retired robot URLs."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/oidc-keycloak.yml").read_text(encoding="utf-8")
    browser = (root / "apps/web/e2e/keycloak-live.mjs").read_text(encoding="utf-8")
    seed = (root / "tests/integration/seed_oidc_keycloak.py").read_text(encoding="utf-8")
    assert "ROBOport_SYNTHETIC_AUTO_API: '1'" in workflow
    assert "https://127.0.0.1:8444/api/v1/meta" in workflow
    assert "if curl --silent --fail --cacert tls.crt https://127.0.0.1:8444/api/v1/catalog/brands" not in workflow
    assert "/api/v1/dev/auto/vehicles" in browser
    assert "/api/v1/dev/account/tickets" not in browser
    assert "foreign.status()!==404" in browser
    assert "revoked link still exposes individual vehicle" in browser
    assert "revoked session still reads automotive data" in browser
    assert "auto_vehicle_customer_link" in seed
    assert "service_ticket" not in seed

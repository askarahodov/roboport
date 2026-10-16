"""OIDC Code+PKCE primitives and strict RS256 ID-token/JWKS verification.

No subject is authorized by a decoded claim alone: domain ACL still uses a
server-issued PostgreSQL session after a verified account link.
"""
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode, urlparse

import httpx
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15


class OIDCRejected(ValueError):
    pass


def _b64(value: str) -> bytes:
    if not isinstance(value, str):
        raise OIDCRejected("invalid encoding")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise OIDCRejected("invalid encoding") from exc


def new_pkce() -> tuple[str, str, str, str]:
    verifier=secrets.token_urlsafe(48)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier,challenge,secrets.token_urlsafe(32),secrets.token_urlsafe(32)


def authorize_url(endpoint: str, client_id: str, redirect_uri: str, challenge: str, state: str, nonce: str) -> str:
    for url in (endpoint,redirect_uri):
        parsed=urlparse(url)
        if parsed.scheme!="https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise OIDCRejected("HTTPS endpoint required")
    if not all(isinstance(s,str) and len(s)>=16 for s in (challenge,state,nonce)):
        raise OIDCRejected("weak flow values")
    return endpoint+"?"+urlencode({"response_type":"code","client_id":client_id,
      "redirect_uri":redirect_uri,"code_challenge":challenge,"code_challenge_method":"S256",
      "state":state,"nonce":nonce,"scope":"openid profile"})


def _document(client: httpx.Client, url: str, issuer: str) -> dict:
    parsed=urlparse(url)
    if parsed.scheme!="https" or parsed.username or parsed.password or parsed.fragment:
        raise OIDCRejected("HTTPS JWKS required")
    try:
        res=client.get(url,follow_redirects=False,timeout=5.0)
        res.raise_for_status()
        if len(res.content)>262144:
            raise OIDCRejected("document too large")
        return res.json()
    except (httpx.HTTPError,ValueError) as exc:
        raise OIDCRejected("OIDC document unavailable") from exc


def validate_id_token(token: str, *, issuer: str, audience: str, nonce: str,
                      jwks: dict, now: int | None = None) -> dict:
    try:
        parts=token.split(".")
        if len(parts)!=3:
            raise OIDCRejected("invalid JWT")
        header=json.loads(_b64(parts[0]))
        payload=json.loads(_b64(parts[1]))
        if not isinstance(header,dict) or header.get("alg")!="RS256" or not isinstance(header.get("kid"),str):
            raise OIDCRejected("untrusted algorithm")
        if not isinstance(payload,dict):
            raise OIDCRejected("invalid claims")
        keys=[k for k in jwks["keys"] if k.get("kid")==header["kid"]
              and k.get("kty")=="RSA" and k.get("use","sig")=="sig"
              and k.get("alg","RS256")=="RS256"]
        if len(keys)!=1:
            raise OIDCRejected("unknown signing key")
        k=keys[0]
        key=RSA.construct((int.from_bytes(_b64(k["n"]),"big"),int.from_bytes(_b64(k["e"]),"big")))
        pkcs1_15.new(key).verify(SHA256.new((parts[0]+"."+parts[1]).encode("ascii")),_b64(parts[2]))
        clock=int(time.time()) if now is None else now
        aud=payload.get("aud")
        audiences=aud if isinstance(aud,list) else [aud]
        if (payload.get("iss")!=issuer or audience not in audiences
            or not isinstance(payload.get("exp"),int) or payload["exp"]<=clock
            or not isinstance(payload.get("iat"),int) or payload["iat"]>clock+60
            or payload.get("nonce")!=nonce
            or not isinstance(payload.get("sub"),str) or not payload["sub"]
            or (len(audiences)>1 and payload.get("azp")!=audience)):
            raise OIDCRejected("invalid token claims")
        return payload
    except (KeyError,TypeError,ValueError,UnicodeError,IndexError,pkcs1_15.ValueError if hasattr(pkcs1_15,"ValueError") else ValueError) as exc:
        raise OIDCRejected("invalid ID token") from exc


def exchange_code(client: httpx.Client, *, token_endpoint: str, code: str,
                  verifier: str, client_id: str, redirect_uri: str) -> dict:
    if urlparse(token_endpoint).scheme!="https":
        raise OIDCRejected("token endpoint must use HTTPS")
    try:
        res=client.post(token_endpoint,data={"grant_type":"authorization_code","code":code,
             "client_id":client_id,"code_verifier":verifier,"redirect_uri":redirect_uri},
             follow_redirects=False,timeout=5.0)
        res.raise_for_status()
        return res.json()
    except (httpx.HTTPError,ValueError) as exc:
        raise OIDCRejected("code exchange failed") from exc

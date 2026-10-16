"""Opt-in loopback-only OIDC flow; never provisions users or customer links.

Requires pre-provisioned (issuer, subject)->app_user binding and HTTPS. The
browser receives only the existing HttpOnly rp_session, never IdP tokens.
"""
import hashlib
import hmac
import json
import os
import time
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib.parse import urlsplit

import httpx
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse
from .oidc import new_pkce, authorize_url, exchange_code, validate_id_token, _document, OIDCRejected
from .session_service import issue_session
from .session_cookie import set_session_cookie

router=APIRouter(include_in_schema=False)
COOKIE="rp_oidc_flow"


def _config(request):
    raw=os.getenv("ROBOport_DATABASE_URL","")
    try:
        parsed=urlsplit(raw)
        issuer=os.environ["ROBOport_OIDC_ISSUER"].rstrip("/")
        authorization=os.environ["ROBOport_OIDC_AUTHORIZE"]
        tokens=os.environ["ROBOport_OIDC_TOKEN"]
        jwks=os.environ["ROBOport_OIDC_JWKS"]
        client=os.environ["ROBOport_OIDC_CLIENT"]
        secret=os.environ["ROBOport_OIDC_STATE_KEY"]
        redirect=os.environ["ROBOport_OIDC_REDIRECT"]
    except (ValueError,KeyError):
        return None
    urls=(issuer,authorization,tokens,jwks,redirect)
    if (os.getenv("ROBOport_OIDC_ENABLED")!="1"
        or request.url.scheme!="https" or request.url.hostname not in ("127.0.0.1","localhost")
        or parsed.scheme not in ("postgresql","postgresql+psycopg")
        or parsed.hostname!="127.0.0.1"
        or len(secret)<32 or len(client)<3
        or any(urlsplit(u).scheme!="https" or not urlsplit(u).hostname for u in urls)
        or redirect!=str(request.base_url).rstrip("/")+"/api/v1/dev/oidc/callback"):
        return None
    return raw.replace("postgresql+psycopg://","postgresql://",1),issuer,authorization,tokens,jwks,client,secret,redirect


def _deny(request,code=403):
    return JSONResponse(status_code=code,content={"code":"FORBIDDEN","message":"OIDC вход недоступен",
          "requestId":request.state.request_id},headers={"Cache-Control":"no-store"})


def _sign(data,secret):
    body=urlsafe_b64encode(json.dumps(data,separators=(",",":")).encode()).rstrip(b"=").decode()
    signature=hmac.new(secret.encode(),body.encode(),hashlib.sha256).hexdigest()
    return body+"."+signature


def _read(value,secret):
    try:
        body,signature=value.split(".")
        expected=hmac.new(secret.encode(),body.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):
            raise ValueError()
        data=json.loads(urlsafe_b64decode(body+"="*(-len(body)%4)))
        if not isinstance(data,dict) or not isinstance(data.get("at"),int) or not 0<=time.time()-data["at"]<=300:
            raise ValueError()
        return data
    except (ValueError,TypeError,KeyError,UnicodeError) as exc:
        raise OIDCRejected("invalid flow state") from exc


@router.get("/api/v1/dev/oidc/start")
def start(request:Request):
    cfg=_config(request)
    if not cfg:
        return _deny(request)
    _,issuer,authorization,_,_,client,secret,redirect=cfg
    verifier,challenge,state,nonce=new_pkce()
    location=authorize_url(authorization,client,redirect,challenge,state,nonce)
    response=RedirectResponse(location,status_code=302,headers={"Cache-Control":"no-store"})
    response.set_cookie(COOKIE,_sign({"verifier":verifier,"state":state,"nonce":nonce,
      "at":int(time.time())},secret),httponly=True,secure=True,samesite="lax",
      max_age=300,path="/api/v1/dev/oidc")
    return response


@router.get("/api/v1/dev/oidc/callback")
def callback(request:Request,code:str="",state:str=""):
    cfg=_config(request)
    if not cfg:
        return _deny(request)
    dsn,issuer,_,token_endpoint,jwks_endpoint,client_id,secret,redirect=cfg
    cookie=request.cookies.get(COOKIE,"")
    try:
        data=_read(cookie,secret)
        if not state or not hmac.compare_digest(state,data["state"]) or not code or len(code)>4096:
            raise OIDCRejected("state/code rejected")
        # All URLs are administrator-configured, not derived from token claims.
        with httpx.Client() as http:
            tokens=exchange_code(http,token_endpoint=token_endpoint,code=code,
                 verifier=data["verifier"],client_id=client_id,redirect_uri=redirect)
            token=tokens.get("id_token")
            if not isinstance(token,str):
                raise OIDCRejected("missing identity token")
            jwks=_document(http,jwks_endpoint,issuer)
            identity=validate_id_token(token,issuer=issuer,audience=client_id,
                                      nonce=data["nonce"],jwks=jwks)
        with psycopg.connect(dsn) as db:
            # No auto-provisioning and no implicit account/customer link.
            row=db.execute("""SELECT i.user_id FROM oidc_identity i JOIN app_user u ON u.id=i.user_id
              WHERE i.issuer=%s AND i.subject=%s AND u.status='ACTIVE'""",
              (issuer,identity["sub"])).fetchone()
            if row is None:
                return _deny(request)
            issued=issue_session(db,row[0])
        if issued is None:
            return _deny(request)
        response=RedirectResponse("/account",status_code=303,headers={"Cache-Control":"no-store"})
        set_session_cookie(response,issued[0],request)
        return response
    except (OIDCRejected,KeyError,psycopg.Error,httpx.HTTPError):
        return _deny(request)
    finally:
        # Failure also clears replayable state cookie via response cookies below?
        pass

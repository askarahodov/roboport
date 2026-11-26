"""CI-only live HTTPS smoke against the disposable, bootstrapped Linux stack.

No real data, no TLS bypass, no external destinations. Never print session tokens,
passwords or response bodies. The empty vehicle list is the expected fresh seed,
not a substitute for the full PostgreSQL and browser regression suites.
"""
import os
import ssl
from uuid import NAMESPACE_URL, uuid5

import httpx

ORIGIN = "https://127.0.0.1:8445"
LOGIN = "synthetic-customer-a"
VEHICLES = "/api/v1/dev/auto/vehicles"
RETIRED = "/api/v1/catalog/brands"


def expect(response, status, step):
    if response.status_code != status:
        raise RuntimeError(f"{step}: expected HTTP {status}, got {response.status_code}")
    return response


def check(client, password):
    expect(client.get("/health/live"), 200, "liveness")
    meta = expect(client.get("/api/v1/meta"), 200, "automotive meta")
    if meta.json() != {"service": "БоксРитм", "environment": "development"}:
        raise RuntimeError("unexpected automotive meta")
    expect(client.get(RETIRED), 404, "retired catalog")
    expect(client.get(VEHICLES), 401, "anonymous vehicle access")
    login = expect(client.post("/api/v1/auth/login", headers={"Origin": ORIGIN},
                              json={"login": LOGIN, "password": password}),
                   200, "synthetic login")
    csrf = login.json().get("csrfToken")
    session = client.cookies.get("rp_session")
    if not isinstance(csrf, str) or not csrf or not session:
        raise RuntimeError("missing synthetic session or CSRF")
    me = expect(client.get("/api/v1/auth/me"), 200, "database-backed identity")
    expected = str(uuid5(NAMESPACE_URL, "roboport-dev-only://" + LOGIN))
    if me.json() != {"subjectId": expected}:
        raise RuntimeError("unexpected synthetic identity")
    page = expect(client.get(VEHICLES), 200, "database-backed vehicle list")
    if page.json() != {"items": []} or page.headers.get("Cache-Control") != "no-store":
        raise RuntimeError("unexpected disposable vehicle list or cache policy")
    logout = expect(client.post("/api/v1/auth/logout",
                               headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}),
                    200, "synthetic logout")
    if logout.json() != {"loggedOut": True}:
        raise RuntimeError("unexpected logout receipt")
    if client.cookies.get("rp_session") is not None:
        raise RuntimeError("logout did not clear session cookie")
    expect(client.get(VEHICLES), 401, "post-logout anonymous vehicle access")
    # Do not let cookie deletion hide a server-side session revocation regression.
    expect(client.get(VEHICLES, headers={"Cookie": "rp_session=" + session}),
           401, "revoked session replay")


def main():
    if (os.getenv("GITHUB_ACTIONS") != "true"
            or os.getenv("ROBOport_SYNTHETIC_AUTH_API") != "1"
            or os.getenv("ROBOport_SYNTHETIC_AUTO_API") != "1"):
        raise SystemExit("requires explicit disposable GitHub Actions synthetic environment")
    password = os.environ.get("ROBOport_SEED_CLIENT_A_PASSWORD", "")
    if not password:
        raise SystemExit("missing synthetic account credential")
    context = ssl.create_default_context(cafile="infra/dev/tls/local.crt")
    with httpx.Client(base_url=ORIGIN, verify=context, trust_env=False,
                      follow_redirects=False, timeout=10.0) as client:
        check(client, password)
    print("LOCAL STACK SMOKE PASS: HTTPS, automotive meta, retired 404, synthetic login, PostgreSQL identity/vehicles, logout, cookie removal and revoked session replay denied")


if __name__ == "__main__":
    main()

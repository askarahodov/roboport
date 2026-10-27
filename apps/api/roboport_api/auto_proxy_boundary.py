"""Strictly reject untrusted reverse-proxy metadata on synthetic auto routes.

No ingress/proxy is approved for the local-only automobile proof workflow.
A client can set these headers directly; none proves an HTTPS connection or
authenticates a remote IP. The real proxy/IP-quota implementation is separate.
"""
from collections.abc import Mapping


# Headers whose semantics concern the transport/address/routing peer, including
# less common aliases used by reverse proxies. Match any X-Forwarded-* suffix
# to avoid a future accidental allowlist gap.
_PROXY_EXACT = frozenset({
    "forwarded",
    "x-real-ip",
    "x-client-ip",
    "x-original-host",
    "x-original-url",
    "x-rewrite-url",
    "x-url-scheme",
    "front-end-https",
    "x-forwarded",
    "client-ip",
    "true-client-ip",
})


def has_untrusted_proxy_headers(headers: Mapping[str, str]) -> bool:
    """Fail closed; never parse, log or use an attacker-supplied IP chain."""
    return any(
        (name.lower() in _PROXY_EXACT
         or name.lower().startswith("x-forwarded-"))
        for name in headers
    )

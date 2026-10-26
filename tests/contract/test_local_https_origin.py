"""Guard dev HTTPS wiring without modifying production ACL."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def test_https_origin_and_proxy_remain_explicit_loopback_only():
    vite=(ROOT/"apps/web/vite.config.ts").read_text()
    script=(ROOT/"infra/dev/start-https.sh").read_text()
    assert "process.env.ROBO_DEV_HTTPS === '1'" in vite
    assert "https://127.0.0.1:8445" in vite
    assert "changeOrigin: false" in vite
    assert "xfwd: false" in vite
    assert "--host 127.0.0.1 --port 8445" in script
    assert "ROBOport_SYNTHETIC_AUTH_API=1" in script
    assert "ROBOport_OIDC_REDIRECT=https://127.0.0.1:8444/api/v1/dev/oidc/callback" in script
    assert "ROBO_DEV_OIDC_STATE_KEY" in script

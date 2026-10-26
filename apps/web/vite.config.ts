import { defineConfig } from 'vite';
// Vite runs this config under Node; the browser TS project intentionally omits @types/node.
// @ts-expect-error Node builtin types are not part of the browser-only tsconfig
import { readFileSync } from 'node:fs';
declare const process: { env: Record<string, string | undefined> };

// HTTPS dev origin is explicit opt-in; backend remains bound to host loopback.
const secureLocal = process.env.ROBO_DEV_HTTPS === '1';
const ssl = secureLocal ? {
  key: readFileSync('../../infra/dev/tls/local.key'),
  cert: readFileSync('../../infra/dev/tls/local.crt'),
} : undefined;

export default defineConfig({
  server: {
    host: '127.0.0.1',
    strictPort: true,
    port: secureLocal ? 8444 : 5173,
    https: ssl,
    proxy: {
      '/api': {
        target: secureLocal ? 'https://127.0.0.1:8445' : 'http://127.0.0.1:8000',
        // Preserve browser-facing authority for OIDC redirect, CSRF Origin and
        // Secure cookies. Only the local self-signed backend is allowed.
        changeOrigin: false,
        secure: !secureLocal,
        xfwd: false,
      },
    },
  },
});

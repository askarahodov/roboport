import { defineConfig } from 'vite';
// Vite runs this config under Node; the browser TS project intentionally omits @types/node.
// @ts-expect-error Node builtin types are not part of the browser-only tsconfig
import { readFileSync, realpathSync, statSync } from 'node:fs';
// @ts-expect-error Node builtin types are not part of the browser-only tsconfig
import { resolve, sep, extname } from 'node:path';
declare const process: { env: Record<string, string | undefined> };

// HTTPS dev origin is explicit opt-in; backend remains bound to host loopback.
const secureLocal = process.env.ROBO_DEV_HTTPS === '1';
const ssl = secureLocal ? {
  key: readFileSync('../../infra/dev/tls/local.key'),
  cert: readFileSync('../../infra/dev/tls/local.crt'),
} : undefined;

// Dev-only, same-origin Docsify handoff from the customer UI. No production
// publishing: /docs/ is deliberately unavailable from Vite's static build.
// Limit the middleware to versioned public documentation assets and reject
// traversal/symlink escapes instead of letting SPA fallback spoof a 200.
const docsMime: Record<string, string> = {
  '.html': 'text/html; charset=utf-8', '.md': 'text/markdown; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
};

export default defineConfig({
  plugins: [{
    name: 'local-dev-docsify-help',
    configureServer(server) {
      const docsRoot = realpathSync(resolve('../../docs'));
      server.middlewares.use('/docs', (req, res) => {
        const requestPath = ((req as unknown as { url?: string }).url || '/').split('?', 1)[0];
        let relative: string;
        try { relative = decodeURIComponent(requestPath); }
        catch { res.statusCode = 400; res.end('Invalid docs URL'); return; }
        if (relative === '/') relative = 'index.html';
        else while (relative.startsWith('/')) relative = relative.slice(1);
        const file = resolve(docsRoot, relative);
        const ext = extname(file).toLowerCase();
        try {
          if ((file !== docsRoot && !file.startsWith(docsRoot + sep)) ||
              !Object.hasOwn(docsMime, ext)) throw new Error('not public docs');
          const canonical = realpathSync(file);
          if (!canonical.startsWith(docsRoot + sep) || !statSync(canonical).isFile())
            throw new Error('invalid docs file');
          res.setHeader('Content-Type', docsMime[ext]);
          res.setHeader('Cache-Control', 'no-store');
          res.setHeader('X-Content-Type-Options', 'nosniff');
          res.end(readFileSync(canonical));
        } catch { res.statusCode = 404; res.end('Docs file not found'); }
      });
    },
  }],
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

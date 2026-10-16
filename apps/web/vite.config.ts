import { defineConfig } from 'vite';
import { readFileSync } from 'node:fs';

// Local HTTP remains for public catalog E2E. Private synthetic login requires
// TLS on both Vite and FastAPI; never weaken the backend's HTTPS cookie gate.
const certPath=process.env.ROBOport_DEV_TLS_CERT;
const keyPath=process.env.ROBOport_DEV_TLS_KEY;
if (Boolean(certPath)!==Boolean(keyPath)) {
  throw new Error('ROBOport local HTTPS requires both TLS certificate and key');
}
const tls=certPath && keyPath
  ? { cert:readFileSync(certPath),key:readFileSync(keyPath) }
  : undefined;

export default defineConfig({
  server:{
    host:'127.0.0.1',
    strictPort:true,
    port:5173,
    https:tls,
    proxy:{
      '/api':{
        target:tls?'https://127.0.0.1:8000':'http://127.0.0.1:8000',
        changeOrigin:false,
        // Only local development: self-signed cert generated on the same host.
        ...(tls?{secure:false}:{}),
      },
    },
  },
});

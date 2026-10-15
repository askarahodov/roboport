import { defineConfig } from 'vite';

// Только локальный dev: браузер обращается к /api/v1/* через тот же origin.
// Бэкенд должен слушать 127.0.0.1:8000; CORS и внешняя публикация не нужны.
export default defineConfig({
  server: {
    host: '127.0.0.1',
    strictPort: true,
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
});

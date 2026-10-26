import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e-docs',
  timeout: 60_000,
  retries: 1,
  use: {
    ...devices['Desktop Chrome'],
    baseURL: 'http://127.0.0.1:3080',
    trace: 'retain-on-failure',
  },
  reporter: 'list',
  webServer: {
    command: 'python3 -m http.server 3080 --bind 127.0.0.1 --directory ../..',
    url: 'http://127.0.0.1:3080/docs/',
    reuseExistingServer: false,
    timeout: 30_000,
  },
});

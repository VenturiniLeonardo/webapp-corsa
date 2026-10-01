import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: 'tests/e2e',
  workers: 1, // tests share one seeded DB; flow 3 mutates it
  use: { baseURL: 'http://127.0.0.1:8001' },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  webServer: {
    command: 'python tests/e2e/serve.py',
    url: 'http://127.0.0.1:8001/healthz',
    reuseExistingServer: false,
    timeout: 60_000,
  },
})

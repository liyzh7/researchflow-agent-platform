import { defineConfig, devices } from '@playwright/test';
import { resolve } from 'node:path';

const cache = process.env.RESEARCHFLOW_WEB_CACHE_DIR ?? '../.cache/web-e2e';
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90000,
  expect: { timeout: 15000 },
  webServer:
    process.env.RESEARCHFLOW_UI_MOCK_ONLY === '1'
      ? {
          command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173',
          url: 'http://127.0.0.1:5173',
          reuseExistingServer: false,
        }
      : undefined,
  outputDir: resolve(cache, 'results'),
  reporter: [
    ['list'],
    [
      'json',
      {
        outputFile: resolve(cache, `${process.env.RESEARCHFLOW_E2E_MODE ?? 'mock'}-results.json`),
      },
    ],
  ],
  use: {
    ...devices['Desktop Chrome'],
    // Reuse local Chrome; CI installs Chromium only and never restores auth state.
    channel: process.env.CI ? undefined : 'chrome',
    baseURL: 'http://127.0.0.1:5173',
    actionTimeout: 15000,
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
  projects: [
    { name: 'mock', testMatch: /workspace\.spec\.ts/ },
    // Explicitly separate HTTP mocks from the real HTTP/PostgreSQL mock-AI suite.
    { name: 'ui-mock', testMatch: /ui-mock\.spec\.ts/ },
  ],
});

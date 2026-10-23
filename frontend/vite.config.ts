import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      ['/api', '/health', '/ready'].map((path) => [
        path,
        {
          target: process.env.RESEARCHFLOW_API_TARGET ?? 'http://127.0.0.1:8000',
          timeout: 360000,
          proxyTimeout: 360000,
        },
      ]),
    ),
  },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true },
});

import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
    // Vitest 4 restores spies without resetting auto-mocks. Keep each test's
    // API implementations and call history isolated from the previous test.
    clearMocks: true,
    mockReset: true,
    // scripts/*.test.mjs covers plain-Node build/CI scripts (e.g. the theme
    // contrast checker) that must stay runnable via `node scripts/x.mjs`
    // with no build step, so their logic lives outside src/ as .mjs.
    include: ['src/**/*.test.{ts,tsx}', 'scripts/**/*.test.mjs'],
  },
});

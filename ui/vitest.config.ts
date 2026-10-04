// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import path from 'path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  test: {
    css: false,
    globals: true,
    environment: 'happy-dom',
    testTimeout: 50000,
    root: './',
    // e2e-live/checks.ts is the live test's logic (scripts/demo.sh test live); its unit tests run here
    include: ['src/**/*.spec.{ts,tsx}', 'e2e-live/**/*.test.ts'],
    setupFiles: ['./config/vitest/vitest.setup.ts'],
    clearMocks: true,
    server: {
      deps: {
        inline: [/@nvidia/],
      },
    },
    coverage: {
      provider: 'v8',
      reporter: ['text', 'text-summary', 'cobertura', 'html'],
      reportsDirectory: './coverage',
      // Every source file, so a file no test imports shows at 0 % instead of not at all
      include: ['src/**/*.{ts,tsx}', 'e2e-live/checks.ts'],
      exclude: ['**/*.spec.{ts,tsx}', '**/test-utils/**', '**/*.d.ts', 'src/generated/**'],
      thresholds: {
        // The ResultChart module is held to full coverage so it cannot regress.
        'src/shared/components/ResultChart/**': {
          statements: 100,
          branches: 100,
          functions: 100,
          lines: 100,
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})

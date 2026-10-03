// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import '@testing-library/jest-dom/vitest'
import { beforeEach } from 'vitest'

// Storage mocks — Node.js 22+ exposes a broken built-in localStorage when
// `--localstorage-file` is passed without a valid path, overriding happy-dom's
// implementation.  Provide explicit in-memory mocks for both storages.
function createStorageMock(): Storage {
  let storage: Record<string, string> = {}

  return {
    clear: () => {
      storage = {}
    },
    getItem: (key: string) => (key in storage ? storage[key] : null),
    key: (index: number) => Object.keys(storage)[index] ?? null,
    get length() {
      return Object.keys(storage).length
    },
    removeItem: (key: string) => {
      delete storage[key]
    },
    setItem: (key: string, value: string) => {
      storage[key] = String(value)
    },
  }
}

class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(globalThis, 'localStorage', {
  value: createStorageMock(),
  configurable: true,
})
Object.defineProperty(globalThis, 'sessionStorage', {
  value: createStorageMock(),
  configurable: true,
})
globalThis.ResizeObserver = NoopObserver as unknown as typeof ResizeObserver
globalThis.IntersectionObserver = NoopObserver as unknown as typeof IntersectionObserver

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

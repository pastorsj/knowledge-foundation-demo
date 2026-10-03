// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ReactElement, ReactNode } from 'react'
import { ThemeProvider } from '@nvidia/foundations-react-core'
import { render } from '@testing-library/react'
import { getFileUploadConfigFromEnv } from '@/shared/config/file-upload'
import {
  AppConfigProvider,
  ExecutionFeatureProvider,
  noExecutionFeature,
  type AppConfig,
  type ExecutionFeature,
} from '@/shared/context'

interface ProviderOptions {
  /** Runtime configuration; defaults to live mode without Phoenix */
  config?: Partial<AppConfig>
  /** Execution feature slots; defaults to none */
  feature?: Partial<ExecutionFeature>
}

const renderWithProviders = (ui: ReactElement, { config, feature }: ProviderOptions = {}) => {
  const wrap = (children: ReactNode) => (
    <AppConfigProvider
      config={{
        mode: 'live',
        defaultPack: 'retail',
        phoenixUrl: null,
        speechInput: { enabled: false, maxSeconds: 60 },
        fileUpload: getFileUploadConfigFromEnv({} as NodeJS.ProcessEnv),
        ...config,
      }}
    >
      <ExecutionFeatureProvider feature={{ ...noExecutionFeature, ...feature }}>
        <ThemeProvider theme="light">{children}</ThemeProvider>
      </ExecutionFeatureProvider>
    </AppConfigProvider>
  )
  const { rerender, ...rest } = render(wrap(ui))
  return {
    ...rest,
    rerender: (rerenderUi: ReactElement) => rerender(wrap(rerenderUi)),
  }
}

// Re-export everything from @testing-library/react
export * from '@testing-library/react'
// Override render with our custom wrapper
export { renderWithProviders as render }

// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@/test-utils'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { useLayoutStore } from '../store'
import { AppBar } from './AppBar'

const initialLayout = useLayoutStore.getState()

describe('AppBar', () => {
  beforeEach(() => {
    useLayoutStore.setState(initialLayout, true)
  })

  test('shows the session title and starts a new session from the logo', async () => {
    const onNewSession = vi.fn()
    render(<AppBar sessionTitle="Top stores" onNewSession={onNewSession} />)

    expect(screen.getByText('Top stores')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Create new session' }))
    expect(onNewSession).toHaveBeenCalledOnce()
  })

  test('does not start a new session while disabled', () => {
    render(<AppBar onNewSession={vi.fn()} isNewSessionDisabled />)

    expect(screen.getByRole('button', { name: 'Create new session' })).toBeDisabled()
  })

  test('toggles the data sources panel', async () => {
    render(<AppBar />)
    const button = screen.getByRole('button', { name: 'Add data sources' })

    await userEvent.click(button)
    expect(useLayoutStore.getState().rightPanel).toBeNull()
    await userEvent.click(button)
    expect(useLayoutStore.getState().rightPanel).toBe('data-sources')
  })

  test('disables the data sources action when asked', () => {
    render(<AppBar isDataSourceSelectionDisabled />)

    expect(screen.getByRole('button', { name: 'Add data sources' })).toBeDisabled()
  })

  test('links to Phoenix only when it is configured', () => {
    const { unmount } = render(<AppBar />)
    expect(screen.queryByTestId('phoenix-observability-link')).not.toBeInTheDocument()
    unmount()

    render(<AppBar />, { config: { phoenixUrl: 'http://127.0.0.1:6006' } })
    expect(screen.getByTestId('phoenix-observability-link')).toHaveAttribute(
      'href',
      'http://127.0.0.1:6006'
    )
  })

  test('shows Phoenix disabled in replay, where there are no traces', async () => {
    render(<AppBar />, { config: { mode: 'replay', phoenixUrl: 'http://127.0.0.1:6006' } })
    const link = screen.getByTestId('phoenix-observability-link')

    expect(link).not.toHaveAttribute('href')
    expect(link).toHaveAttribute('aria-disabled', 'true')
    expect(link).toHaveTextContent('Phoenix')
    await userEvent.hover(link)
    expect(
      (await screen.findAllByText('Traces are only available in live mode.')).length
    ).toBeGreaterThan(0)
  })

  test('switches between light and dark themes', async () => {
    useLayoutStore.setState({ theme: 'light' })
    render(<AppBar />)

    await userEvent.click(screen.getByRole('button', { name: 'Switch to dark mode' }))
    expect(useLayoutStore.getState().theme).toBe('dark')
  })

  test('shows the Default User, whose menu says sign-in is not configured and sets the theme', async () => {
    useLayoutStore.setState({ theme: 'light' })
    render(<AppBar />)

    await userEvent.click(
      screen.getByRole('button', { name: 'Default User - Authentication Not Configured' })
    )
    expect(await screen.findByText('Default User')).toBeInTheDocument()
    expect(screen.getByText('Authentication Not Configured')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('radio', { name: 'System theme' }))
    expect(useLayoutStore.getState().theme).toBe('system')
  })
})

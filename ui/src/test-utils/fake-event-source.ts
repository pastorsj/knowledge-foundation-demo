// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * A controllable EventSource for tests: records every instance and lets a
 * test emit named SSE events and errors.
 */
export class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static instances: FakeEventSource[] = []

  readonly url: string
  readyState = FakeEventSource.CONNECTING
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  private listeners = new Map<string, Array<(event: MessageEvent) => void>>()

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  /** The most recently created instance. */
  static get latest(): FakeEventSource {
    const instance = FakeEventSource.instances.at(-1)
    if (!instance) throw new Error('No EventSource was created')
    return instance
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED
  }

  /** Deliver one SSE record: `event: type`, `id: cursor`, `data: JSON`. */
  emit(type: string, data: unknown, cursor = ''): void {
    const event = new MessageEvent(type, { data: JSON.stringify(data), lastEventId: cursor })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }

  /** Report a connection error in the given ready state. */
  fail(readyState: number): void {
    this.readyState = readyState
    this.onerror?.()
  }
}

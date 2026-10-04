// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * InputArea Component
 *
 * Chat input area at the bottom of the chat view: the demo scenario picker
 * (the active data pack's examples, five rows at a time), the question, the
 * microphone when voice input is on, the data source indicator, and send (or
 * stop while a run is in progress). A recorded session shows it read only, as
 * the original demo UI did; in replay mode that includes the original's
 * microphone, disabled.
 *
 * In Your data (the workspace pack) it also has upstream's file upload: the
 * paperclip, drag and drop onto the composer, the file counter, the pending
 * files chip, and the banners of an upload in progress and of a question sent
 * while files are still pending (the first send warns, the second sends).
 */

'use client'

import {
  type CSSProperties,
  type FC,
  memo,
  useState,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type KeyboardEvent,
} from 'react'
import { Banner, Flex, Text, Button, Select, TextArea } from '@/adapters/ui'
import { useHermesChat, useChatStore, useIsCurrentSessionBusy } from '@/features/chat'
import { FileUploadBanner } from '@/features/chat/components/FileUploadBanner'
import {
  describeAcceptedTypes,
  useFileDragDrop,
  useFileUpload,
  useFileUploadBanners,
  WORKSPACE_COLLECTION,
} from '@/features/documents'
import {
  getSpeechInputStatusMessage,
  SpeechInputButton,
  useSpeechInput,
} from '@/features/speech-input'
import { useAppConfig } from '@/shared/context'
import { useLayoutStore } from '../store'
import { ToolPills } from '@/shared/components/ToolPills'
import { getActiveDemoScenario, getAvailableDemoScenarios, type DemoScenario } from '../scenarios'
import {
  Cancel,
  ChartFlow,
  Document,
  Globe,
  Paperclip,
  Paperplane,
  StopCircle,
} from '@/adapters/ui/icons'
import { VISIBLE_EXAMPLE_ROWS, visibleRowsHeight } from './visible-rows'

const NO_SCENARIOS: DemoScenario[] = []
const EXAMPLE_LIST = 'demo-scenario-list'

interface InputAreaProps {
  /** Placeholder text */
  placeholder?: string
  /** The active data pack's examples, offered as demo scenarios */
  scenarios?: DemoScenario[]
  /** Whether the demo scenario picker shows (not beside the execution view) */
  showDemoScenarios?: boolean
}

/**
 * Chat input component with text area and action buttons.
 * Positioned at the bottom of the chat area.
 */
export const InputArea: FC<InputAreaProps> = memo(function InputArea({
  placeholder = 'Check data sources and ask a research question...',
  scenarios = NO_SCENARIOS,
  showDemoScenarios = true,
}) {
  const [message, setMessage] = useState('')
  // The latest draft, for a transcript that arrives after the user typed more
  const messageRef = useRef('')
  const textAreaRef = useRef<HTMLTextAreaElement>(null)
  const speechInsertionRef = useRef({ start: 0, end: 0 })
  const { sendMessage, stop } = useHermesChat()
  const { mode, speechInput: speechInputConfig, fileUpload: fileUploadConfig } = useAppConfig()

  // A running job in this session pauses the composer; it can be stopped.
  const isBusy = useIsCurrentSessionBusy()

  const currentConversation = useChatStore((state) => state.currentConversation)
  const isRecordedSession = mode === 'replay' || currentConversation?.readOnly === true
  const disabled = isBusy || isRecordedSession
  const ensureSession = useChatStore((state) => state.ensureSession)
  const saveDataSourcesToConversation = useChatStore((state) => state.saveDataSourcesToConversation)

  const enabledDataSourceIds = useLayoutStore((s) => s.enabledDataSourceIds)
  const availableDataSources = useLayoutStore((s) => s.availableDataSources)
  const setEnabledDataSources = useLayoutStore((s) => s.setEnabledDataSources)
  const promptDraft = useLayoutStore((s) => s.promptDraft)
  const setPromptDraft = useLayoutStore((s) => s.setPromptDraft)

  // Your data: uploads go to the workspace collection, in live mode only
  const packId = useLayoutStore((s) => s.packId)
  const canUpload = packId === WORKSPACE_COLLECTION && mode === 'live' && !isRecordedSession
  const fileInputRef = useRef<HTMLInputElement>(null)
  const {
    uploadFiles,
    sessionFiles,
    isUploading,
    error: uploadError,
    clearError,
  } = useFileUpload({ sessionId: canUpload ? WORKSPACE_COLLECTION : undefined })
  // Opens the Files tab when an upload's files have all settled
  useFileUploadBanners()
  const pendingCount = canUpload
    ? sessionFiles.filter((f) => f.status === 'uploading' || f.status === 'ingesting').length
    : 0
  const attachedFilesCount = sessionFiles.filter(
    (f) => f.status === 'uploading' || f.status === 'ingesting' || f.status === 'success'
  ).length
  // The first send while files are pending warns; the next one sends without them
  const [pendingFilesWarningActive, setPendingFilesWarningActive] = useState(false)
  const [uploadBannerDismissed, setUploadBannerDismissed] = useState(false)
  const prevPendingCountRef = useRef(0)
  useEffect(() => {
    const previous = prevPendingCountRef.current
    if (previous === 0 && pendingCount > 0) {
      setPendingFilesWarningActive(false)
      setUploadBannerDismissed(false)
    }
    if (previous > 0 && pendingCount === 0) setPendingFilesWarningActive(false)
    prevPendingCountRef.current = pendingCount
  }, [pendingCount])

  const handleFilesSelected = useCallback(
    async (files: File[]) => {
      if (files.length === 0 || !canUpload || isUploading || isBusy) return
      // Open the files tab immediately so the user sees instant feedback
      const { setDataSourcesPanelTab, openRightPanel } = useLayoutStore.getState()
      setDataSourcesPanelTab('files')
      openRightPanel('data-sources')
      // uploadFiles validates internally and sets error if invalid
      await uploadFiles(files, WORKSPACE_COLLECTION)
    },
    [canUpload, isBusy, isUploading, uploadFiles]
  )

  const { isDragging, isUnsupportedDrag, dragHandlers } = useFileDragDrop({
    onDrop: handleFilesSelected,
    disabled: !canUpload || isUploading || isBusy,
  })

  const handleFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || [])
      // Reset input so same file can be selected again
      e.target.value = ''
      await handleFilesSelected(files)
    },
    [handleFilesSelected]
  )

  const availableDemoScenarios = useMemo(
    () =>
      getAvailableDemoScenarios(
        scenarios,
        (availableDataSources ?? []).map((source) => source.id)
      ),
    [availableDataSources, scenarios]
  )
  const activeDemoScenario = getActiveDemoScenario(
    message,
    enabledDataSourceIds,
    availableDemoScenarios
  )

  // The open list shows five examples and scrolls for the rest. Their height is measured once the
  // list is laid out (it renders when the picker opens), and kept for the next opening. The list is
  // never taller than the space it has; that is the select's own limit without the 12 px its opening
  // slide keeps back, which would cut the fifth row below the composer of a 900 px high window.
  const [exampleListHeight, setExampleListHeight] = useState<number | null>(null)
  const handlePickerOpenChange = useCallback((open: boolean) => {
    if (!open) return
    window.requestAnimationFrame(() => {
      const list = document.querySelector<HTMLElement>(`[data-testid="${EXAMPLE_LIST}"]`)
      if (list) setExampleListHeight(visibleRowsHeight(list, VISIBLE_EXAMPLE_ROWS))
    })
  }, [])
  const exampleListStyle = useMemo<CSSProperties | undefined>(
    () =>
      exampleListHeight === null
        ? undefined
        : {
            maxHeight: `min(${exampleListHeight}px, var(--max-height))`,
          },
    [exampleListHeight]
  )

  const handleScenarioChange = useCallback(
    (scenarioId: string) => {
      const scenario = availableDemoScenarios.find((candidate) => candidate.id === scenarioId)
      if (!scenario || disabled) return
      // Session creation restores its default source set, so it must happen before
      // applying and persisting the scenario's exact connections.
      if (!currentConversation) ensureSession()
      setEnabledDataSources([...scenario.sourceIds])
      saveDataSourcesToConversation([...scenario.sourceIds])
      messageRef.current = scenario.question
      setMessage(scenario.question)
    },
    [
      availableDemoScenarios,
      currentConversation,
      disabled,
      ensureSession,
      saveDataSourcesToConversation,
      setEnabledDataSources,
    ]
  )

  // Prefill the composer from a staged prompt (e.g. a featured question)
  useEffect(() => {
    if (promptDraft) {
      messageRef.current = promptDraft
      setMessage(promptDraft)
      setPromptDraft(null)
    }
  }, [promptDraft, setPromptDraft])

  const handleSubmit = useCallback(() => {
    if (!message.trim() || disabled) return
    // Files still ingesting cannot answer yet: warn once, then send without them
    if (pendingCount > 0 && !pendingFilesWarningActive) {
      setPendingFilesWarningActive(true)
      return
    }
    setPendingFilesWarningActive(false)
    // Session creation needs the user ID, which is set at startup.
    if (!ensureSession()) return
    messageRef.current = ''
    setMessage('')
    sendMessage(message)
  }, [message, disabled, ensureSession, sendMessage, pendingCount, pendingFilesWarningActive])

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSubmit()
      }
    },
    [handleSubmit]
  )

  const handleValueChange = useCallback(
    (value: string) => {
      // Persist a session as soon as the user starts typing. This keeps
      // logo-triggered "new session" drafts out of history until touched.
      if (!currentConversation && value.trim().length > 0) {
        ensureSession()
      }
      messageRef.current = value
      setMessage(value)
    },
    [currentConversation, ensureSession]
  )

  const rememberSpeechInsertionPoint = useCallback(() => {
    const textArea = textAreaRef.current
    if (!textArea) {
      speechInsertionRef.current = { start: message.length, end: message.length }
      return
    }
    speechInsertionRef.current = {
      start: textArea.selectionStart ?? message.length,
      end: textArea.selectionEnd ?? message.length,
    }
  }, [message.length])

  const insertSpeechTranscript = useCallback(
    (transcript: string) => {
      const spokenText = transcript.trim()
      if (!spokenText) return
      if (!currentConversation) ensureSession()

      // Transcription is asynchronous and the composer stays editable while it
      // runs: insert into the latest draft so a late result never overwrites
      // text typed meanwhile.
      const currentMessage = messageRef.current
      const start = Math.min(currentMessage.length, Math.max(0, speechInsertionRef.current.start))
      const end = Math.min(currentMessage.length, Math.max(start, speechInsertionRef.current.end))
      const before = currentMessage.slice(0, start)
      const after = currentMessage.slice(end)
      const leadingSpace = before.length > 0 && !/\s$/.test(before) ? ' ' : ''
      const trailingSpace = after.length > 0 && !/^\s/.test(after) ? ' ' : ''
      const nextMessage = `${before}${leadingSpace}${spokenText}${trailingSpace}${after}`
      const nextCursor = before.length + leadingSpace.length + spokenText.length

      messageRef.current = nextMessage
      setMessage(nextMessage)
      speechInsertionRef.current = { start: nextCursor, end: nextCursor }
      window.requestAnimationFrame(() => {
        textAreaRef.current?.focus()
        textAreaRef.current?.setSelectionRange(nextCursor, nextCursor)
      })
    },
    [currentConversation, ensureSession]
  )

  const speechInput = useSpeechInput({
    enabled: speechInputConfig.enabled && !disabled,
    maxSeconds: speechInputConfig.maxSeconds,
    onTranscript: insertSpeechTranscript,
  })

  const handleSpeechToggle = useCallback(() => {
    if (speechInput.state === 'recording') {
      void speechInput.stop()
      return
    }
    void speechInput.start()
  }, [speechInput])

  // Data sources counts for indicator, as upstream: the selected pack's enabled connections
  const sourceIds = new Set((availableDataSources ?? []).map((source) => source.id))
  const enabledSourcesCount = enabledDataSourceIds.filter((id) => sourceIds.has(id)).length
  // Replay keeps the original's microphone in the read-only composer; it never records there.
  const showMicrophone = speechInputConfig.enabled || mode === 'replay'

  const toggleDataSources = useCallback(() => {
    const { rightPanel, closeRightPanel, openRightPanel } = useLayoutStore.getState()
    if (rightPanel === 'data-sources') {
      closeRightPanel()
    } else {
      openRightPanel('data-sources')
    }
  }, [])

  return (
    <Flex direction="col" className="mx-auto w-full max-w-4xl px-6 py-4">
      <Flex
        direction="col"
        className={`composer-surface relative rounded-[var(--radius-composer)] border p-3.5 transition-colors ${
          isDragging && isUnsupportedDrag
            ? 'border-error border-dashed'
            : isDragging
              ? 'border-brand border-dashed'
              : ''
        }`}
        data-testid="composer"
        {...(canUpload ? dragHandlers : {})}
      >
        {/* Drag overlay */}
        {isDragging && (
          <div className="bg-surface-raised-90 absolute inset-0 z-10 flex items-center justify-center rounded-[var(--radius-composer)]">
            <Flex direction="col" align="center" gap="2">
              {isUnsupportedDrag ? (
                <Cancel className="text-error h-8 w-8" />
              ) : (
                <Paperclip className="text-brand h-8 w-8" />
              )}
              <Text
                kind="label/semibold/sm"
                className={isUnsupportedDrag ? 'text-error' : 'text-brand'}
              >
                {isUnsupportedDrag ? 'Unsupported file type' : 'Drop files to upload'}
              </Text>
              {isUnsupportedDrag && (
                <Text kind="body/regular/xs" className="text-subtle">
                  Accepts: {describeAcceptedTypes(fileUploadConfig.acceptedTypes)}
                </Text>
              )}
            </Flex>
          </div>
        )}
        {showDemoScenarios && !isRecordedSession && availableDemoScenarios.length > 0 && (
          <div
            className="border-base mb-2 grid grid-cols-1 gap-1.5 border-b pb-2 sm:grid-cols-[auto_minmax(0,1fr)] sm:items-center sm:gap-2"
            data-testid="demo-scenario-control"
          >
            <Flex align="center" gap="1.5" className="shrink-0">
              <ChartFlow className="text-brand h-4 w-4" />
              <Text kind="label/semibold/sm" className="text-secondary">
                Demo scenario
              </Text>
            </Flex>
            <div className="w-full min-w-0 flex-1 sm:max-w-sm sm:justify-self-end">
              <Select
                aria-label="Choose a demo scenario"
                placeholder="Choose an example"
                size="small"
                side="bottom"
                triggerKind="flat"
                value={activeDemoScenario?.id ?? ''}
                onValueChange={handleScenarioChange}
                onOpenChange={handlePickerOpenChange}
                disabled={disabled}
                attributes={{
                  SelectTrigger: { 'data-testid': 'demo-scenario-select' },
                  SelectContent: { 'data-testid': EXAMPLE_LIST, style: exampleListStyle },
                }}
                items={availableDemoScenarios.map((scenario) => ({
                  value: scenario.id,
                  children: scenario.label,
                  slotRight: (
                    <ToolPills
                      pills={scenario.tools.map((pill) => ({ pill }))}
                      className="justify-end"
                    />
                  ),
                  attributes: {
                    SelectItem: {
                      title: scenario.description,
                      'data-scenario-id': scenario.id,
                    },
                  },
                }))}
              />
            </div>
            <span className="sr-only" role="status" aria-live="polite">
              {activeDemoScenario
                ? `${activeDemoScenario.label} loaded; ${activeDemoScenario.sourceIds.length} connection selected.`
                : ''}
            </span>
          </div>
        )}
        {isRecordedSession && (
          <Text kind="label/semibold/xs" className="text-subtle mb-2 px-1">
            Recorded test session · read only
          </Text>
        )}
        {/* Text Input */}
        <div onKeyDown={handleKeyDown}>
          <TextArea
            ref={textAreaRef}
            className="composer-textarea border-0 bg-transparent"
            value={message}
            onValueChange={handleValueChange}
            onSelect={rememberSpeechInsertionPoint}
            placeholder={
              isRecordedSession
                ? 'Recorded test sessions are read only'
                : isBusy
                  ? 'Please wait...'
                  : placeholder
            }
            disabled={disabled}
            resizeable="auto"
            size="medium"
            aria-label="Chat message input"
            slotRight={
              showMicrophone ? (
                <SpeechInputButton
                  state={speechInput.state}
                  disabled={disabled}
                  onBeforeToggle={rememberSpeechInsertionPoint}
                  onToggle={handleSpeechToggle}
                />
              ) : undefined
            }
          />
        </div>

        {speechInputConfig.enabled && (
          <span className="sr-only" role="status" aria-live="polite">
            {getSpeechInputStatusMessage(speechInput.state)}
          </span>
        )}

        {/* Upload Error Display */}
        {uploadError && (
          <Banner kind="inline" status="error" onClose={clearError} className="mt-2">
            {uploadError}
          </Banner>
        )}
        {pendingFilesWarningActive ? (
          <div className="mt-2" data-testid="pending-files-warning">
            <FileUploadBanner type="pending_warning" fileCount={pendingCount} />
          </div>
        ) : pendingCount > 0 && !uploadBannerDismissed ? (
          <div className="mt-2" data-testid="upload-banner">
            <FileUploadBanner
              type="uploaded"
              fileCount={pendingCount}
              onDismiss={() => setUploadBannerDismissed(true)}
            />
          </div>
        ) : null}

        {speechInput.error && (
          <Banner kind="inline" status="error" onClose={speechInput.clearError} className="mt-2">
            {speechInput.error}
          </Banner>
        )}

        {/* Bottom Actions Bar */}
        <Flex align="center" justify="end" gap="1.5" className="border-base mt-3 border-t pt-3">
          {pendingCount > 0 && (
            <span className="text-warning bg-surface-raised-30 border-warning mr-auto inline-flex h-7 items-center rounded-full border px-2.5 text-xs font-medium">
              {pendingCount} pending
            </span>
          )}
          {/* Sources indicator - clickable to toggle data connections */}
          <Button
            kind="tertiary"
            size="tiny"
            onClick={toggleDataSources}
            disabled={isRecordedSession}
            tabIndex={-1}
            aria-label="Toggle data sources connections"
            title="Selected data connections"
          >
            <Flex align="center" gap="1">
              <Globe className="h-3 w-3" />
              <Text kind="label/bold/sm">
                {enabledSourcesCount}/{sourceIds.size}
              </Text>
            </Flex>
          </Button>

          {canUpload && (
            <>
              {/* Files indicator - clickable to open the Files tab */}
              <Button
                kind="tertiary"
                size="tiny"
                onClick={() => {
                  const { setDataSourcesPanelTab, openRightPanel } = useLayoutStore.getState()
                  setDataSourcesPanelTab('files')
                  openRightPanel('data-sources')
                }}
                tabIndex={-1}
                aria-label="Open uploaded files"
                title="Available files"
              >
                <Flex align="center" gap="1">
                  <Document className="h-3 w-3" />
                  <Text kind="label/bold/sm">{attachedFilesCount}</Text>
                </Flex>
              </Button>

              {/* Hidden file input */}
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={fileUploadConfig.acceptedTypes}
                className="hidden"
                tabIndex={-1}
                data-testid="composer-file-input"
                onChange={handleFileChange}
              />

              {/* Attach files */}
              <Button
                kind="tertiary"
                size="small"
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading || isBusy}
                tabIndex={-1}
                aria-label="Attach files"
                title={
                  isBusy
                    ? 'File upload disabled during active operations'
                    : 'Select files to upload'
                }
              >
                <Paperclip className="h-4 w-4" />
              </Button>
            </>
          )}

          {isBusy ? (
            <Button
              kind="secondary"
              size="small"
              color="danger"
              onClick={stop}
              aria-label="Stop generating"
              title="Stop generating"
            >
              <StopCircle className="h-4 w-4" />
            </Button>
          ) : (
            <Button
              kind="primary"
              size="small"
              color={!message.trim() || disabled ? undefined : 'brand'}
              onClick={handleSubmit}
              disabled={!message.trim() || disabled}
              aria-label="Send message"
              title="Send query"
            >
              <Paperplane className="h-4 w-4" />
            </Button>
          )}
        </Flex>
      </Flex>
    </Flex>
  )
})

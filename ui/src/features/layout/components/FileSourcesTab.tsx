// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FileSourcesTab Component
 *
 * Content for the "File Sources" tab in the DataSourcePanel.
 * Displays a list of uploaded file sources with their status.
 * Integrates with file upload system for real-time progress tracking.
 *
 * Every upload lands in the workspace collection (Your data), whatever the
 * session; each card shows its pipeline stage, and a table file's tables open
 * in the data viewer (the execution feature's `DataViewer`).
 */

'use client'

import { type FC, useCallback, useRef, useState } from 'react'
import { Flex, Text, Button, Banner } from '@/adapters/ui'
import { LoadingSpinner } from '@/adapters/ui/icons'
import { FileSourceCard } from './FileSourceCard'
import { DeleteFileConfirmationModal } from './DeleteFileConfirmationModal'
import {
  useFileUpload,
  useDocumentsStore,
  FileUploadZone,
  mapToDisplayStatus,
  WORKSPACE_COLLECTION,
} from '@/features/documents'
import { useAppConfig, useExecutionFeature } from '@/shared/context'

interface FileSourcesTabProps {
  /** Callback when a file is deleted */
  onDeleteFile?: (id: string) => void
}

/**
 * Tab content showing list of uploaded file sources.
 * Connected to the file upload store for real-time updates.
 */
export const FileSourcesTab: FC<FileSourcesTabProps> = ({ onDeleteFile }) => {
  // Upstream's per-session collection is the workspace's one collection here
  const sessionId = WORKSPACE_COLLECTION
  const knowledgeLayerAvailable = true
  const { DataViewer } = useExecutionFeature()
  const [viewingTables, setViewingTables] = useState(false)

  const { fileUpload: fileUploadConfig } = useAppConfig()

  const {
    uploadFiles,
    deleteFile,
    sessionFiles,
    isUploading,
    isPolling,
    error: uploadError,
    clearError,
  } = useFileUpload({ sessionId })

  const activeCollection = useDocumentsStore((state) => state.currentCollectionName)
  const isLoadingFiles = useDocumentsStore((state) => state.isLoadingFiles)
  const loadedSessionId = useDocumentsStore((state) => state.loadedSessionId)
  const isThisSessionProcessing = activeCollection === sessionId && (isUploading || isPolling)

  const hasLoadedForSession = loadedSessionId === sessionId
  const sessionExpectsFiles = !hasLoadedForSession
  const isAwaitingFiles =
    isLoadingFiles || (isThisSessionProcessing && sessionFiles.length === 0) || sessionExpectsFiles

  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false)
  const [fileIdToDelete, setFileIdToDelete] = useState<string | null>(null)

  /**
   * Handle file upload with session auto-creation.
   * Validation is handled internally by uploadFiles.
   */
  const handleUpload = useCallback(
    async (files: File[]) => {
      await uploadFiles(files, sessionId)
    },
    [sessionId, uploadFiles]
  )

  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleAddFileClick = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || [])
      if (files.length > 0) {
        handleUpload(files)
      }
      e.target.value = ''
    },
    [handleUpload]
  )

  const handleDeleteClick = useCallback((id: string) => {
    setFileIdToDelete(id)
    setIsDeleteModalOpen(true)
  }, [])

  const handleConfirmDelete = useCallback(async () => {
    if (fileIdToDelete) {
      await deleteFile(fileIdToDelete)
      onDeleteFile?.(fileIdToDelete)
      setFileIdToDelete(null)
    }
  }, [fileIdToDelete, deleteFile, onDeleteFile])

  const handleModalOpenChange = useCallback((open: boolean) => {
    setIsDeleteModalOpen(open)
    if (!open) {
      setFileIdToDelete(null)
    }
  }, [])

  if (sessionFiles.length === 0) {
    if (isAwaitingFiles) {
      return (
        <Flex direction="col" align="center" justify="center" gap="2" className="flex-1 py-8">
          <LoadingSpinner size="medium" aria-label="Loading files" />
          <Text kind="body/regular/sm" className="text-subtle">
            Checking for files...
          </Text>
        </Flex>
      )
    }

    return (
      <Flex direction="col" gap="4" className="flex-1">
        {!knowledgeLayerAvailable && (
          <Banner kind="inline" status="info" className="mb-6 px-4 py-3">
            Setup backend to enable files.
          </Banner>
        )}

        {knowledgeLayerAvailable && (
          <Flex direction="col" gap="1">
            <Text
              kind="label/semibold/xs"
              className="text-subtle font-mono uppercase tracking-widest"
            >
              No Attached Files
            </Text>
            <Text kind="body/regular/sm" className="text-subtle">
              Upload documents and tables: each is parsed or loaded, then indexed for every question
              you ask in Your data, until you delete it.
            </Text>
          </Flex>
        )}

        {uploadError && (
          <Banner kind="inline" status="error" onClose={clearError}>
            {uploadError}
          </Banner>
        )}

        {knowledgeLayerAvailable && (
          <FileUploadZone
            sessionId={sessionId}
            acceptedTypes={fileUploadConfig.acceptedTypes}
            maxFileSize={fileUploadConfig.maxFileSize}
            maxFileCount={fileUploadConfig.maxFileCount}
            onUpload={handleUpload}
            isUploading={isUploading}
          />
        )}
      </Flex>
    )
  }

  return (
    <Flex direction="col" gap="2" className="flex-1 overflow-y-auto">
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={fileUploadConfig.acceptedTypes}
        className="hidden"
        onChange={handleFileChange}
      />

      {uploadError && (
        <Banner kind="inline" status="error" onClose={clearError}>
          {uploadError}
        </Banner>
      )}

      <Flex align="center" justify="between" className="mb-1">
        <Text kind="label/semibold/xs" className="text-subtle font-mono uppercase tracking-widest">
          Uploaded Files ({sessionFiles.length})
        </Text>
        <Button
          kind="tertiary"
          size="small"
          onClick={handleAddFileClick}
          disabled={isLoadingFiles || !knowledgeLayerAvailable}
          title={
            isLoadingFiles
              ? 'Loading files...'
              : knowledgeLayerAvailable
                ? 'Add files'
                : 'File upload not available'
          }
        >
          + Add File
        </Button>
      </Flex>

      {sessionFiles.map((file) => (
        <FileSourceCard
          key={file.id}
          id={file.id}
          title={file.fileName}
          fileSize={file.fileSize}
          uploadedAt={file.uploadedAt}
          status={mapToDisplayStatus(file.status)}
          errorMessage={file.errorMessage ?? undefined}
          kind={file.kind}
          stage={file.stage}
          lastStage={file.lastStage}
          stageDetail={file.stageDetail}
          progress={file.progress}
          parser={file.parser}
          tables={file.tables}
          warnings={file.warnings}
          onOpenTable={DataViewer ? () => setViewingTables(true) : undefined}
          onDelete={handleDeleteClick}
        />
      ))}

      {DataViewer && viewingTables && <DataViewer onClose={() => setViewingTables(false)} />}

      <DeleteFileConfirmationModal
        open={isDeleteModalOpen}
        onOpenChange={handleModalOpenChange}
        onConfirm={handleConfirmDelete}
      />
    </Flex>
  )
}

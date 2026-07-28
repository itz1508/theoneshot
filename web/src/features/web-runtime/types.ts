/**
 * Web Runtime feature — shared types.
 *
 * Terminal line model note: `@webcontainer/api` exposes a single TTY-merged
 * `output` stream per process, so stdout/stderr cannot be captured
 * separately. Lines are instead tagged by *source channel* (`system`,
 * `install`, `dev`) plus an error flag for lines the feature itself marks.
 */

export type RuntimeStatus =
  | 'idle'
  | 'booting'
  | 'installing'
  | 'starting'
  | 'ready'
  | 'failed'
  | 'stopped'

export type TerminalSource = 'system' | 'install' | 'dev'

export interface TerminalLine {
  id: number
  source: TerminalSource
  text: string
  isError: boolean
}

export type ProjectSource = { kind: 'sample' } | { kind: 'folder'; name: string }

/**
 * Structural typings for the File System Access API — lib.dom does not
 * ship the directory async-iterator surface, and structural types keep
 * the traversal unit-testable with plain object mocks.
 */
export interface FileHandleLike {
  kind: 'file'
  name: string
  getFile(): Promise<File>
}

export interface DirectoryHandleLike {
  kind: 'directory'
  name: string
  values(): AsyncIterable<DirectoryHandleLike | FileHandleLike>
}

declare global {
  interface Window {
    /** File System Access API — Chromium-family browsers only. */
    showDirectoryPicker?: (options?: { mode?: string }) => Promise<DirectoryHandleLike>
  }
}

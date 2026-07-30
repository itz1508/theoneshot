/**
 * FrontendToolExecutor — bridges tool calls from the backend orchestrator
 * to the WebContainer runtime for local execution.
 *
 * Responsibilities:
 * - Route tool calls to the appropriate WebContainer operation
 * - Enforce timeouts (30s reads, 120s shell)
 * - Cap output size (100KB per result)
 * - Normalize errors (structured, never raw stack traces)
 * - Emit streaming events for shell output (local UI only)
 *
 * The executor does NOT make approval decisions — those are handled by
 * BackendChatSource before reaching this layer.
 */

import type { ToolCallEvent, ToolResultPayload } from './types'

/** Maximum output bytes per tool result (100KB). */
const MAX_OUTPUT_BYTES = 100_000

/** Default timeout for read-only operations (30s). */
const TIMEOUT_READ_MS = 30_000

/** Timeout for shell commands (120s). */
const TIMEOUT_SHELL_MS = 120_000

/** Truncation marker appended when output exceeds the cap. */
const TRUNCATION_MARKER = '\n[output truncated]'

/**
 * Minimal interface for the WebContainer filesystem operations.
 * Decoupled from the actual WebContainer SDK so tests can mock it.
 */
export interface ContainerFS {
  readFile(path: string, encoding: 'utf-8'): Promise<string>
  writeFile(path: string, content: string): Promise<void>
  readdir(path: string, options?: { withFileTypes: boolean }): Promise<DirEntry[]>
}

export interface DirEntry {
  name: string
  isFile(): boolean
  isDirectory(): boolean
}

/**
 * Minimal interface for spawning processes in the WebContainer.
 */
export interface ContainerProcess {
  output: ReadableStream<string>
  exit: Promise<number>
}

export interface ContainerSpawn {
  spawn(cmd: string, args: string[]): Promise<ContainerProcess>
}

/** Full container interface needed by the executor. */
export interface WebContainerHandle {
  fs: ContainerFS
  spawn: ContainerSpawn['spawn']
}

/** Callback for streaming shell output lines (UI-local, not sent to backend). */
export type StreamCallback = (chunk: string) => void

export class FrontendToolExecutor {
  private container: WebContainerHandle | null = null
  private onStream: StreamCallback | null = null

  /** Bind the WebContainer instance. Call once when container boots. */
  setContainer(container: WebContainerHandle): void {
    this.container = container
  }

  /** Set the streaming callback for shell output events. */
  setStreamCallback(cb: StreamCallback): void {
    this.onStream = cb
  }

  /**
   * Execute a single frontend tool call.
   * Returns a result payload suitable for POST /v1/chat/continue.
   */
  async execute(call: ToolCallEvent, signal: AbortSignal): Promise<ToolResultPayload> {
    if (!this.container) {
      return {
        call_id: call.call_id,
        tool_name: call.tool_name,
        output: null,
        error: 'WebContainer not available',
        status: 'error',
      }
    }

    try {
      switch (call.tool_name) {
        case 'file_read':
          return await this.executeFileRead(call, signal)
        case 'file_write':
          return await this.executeFileWrite(call, signal)
        case 'list_directory':
          return await this.executeListDirectory(call, signal)
        case 'shell_exec':
          return await this.executeShell(call, signal)
        default:
          return {
            call_id: call.call_id,
            tool_name: call.tool_name,
            output: null,
            error: `Unknown frontend tool: ${call.tool_name}`,
            status: 'error',
          }
      }
    } catch (err: unknown) {
      if (signal.aborted) {
        return {
          call_id: call.call_id,
          tool_name: call.tool_name,
          output: null,
          error: 'Cancelled',
          status: 'cancelled',
        }
      }
      const message = err instanceof Error ? err.message : 'Unknown execution error'
      return {
        call_id: call.call_id,
        tool_name: call.tool_name,
        output: null,
        error: message,
        status: 'error',
      }
    }
  }

  private async executeFileRead(
    call: ToolCallEvent,
    signal: AbortSignal,
  ): Promise<ToolResultPayload> {
    const path = (call.arguments.path as string) || '.'
    const content = await withTimeout(
      this.container!.fs.readFile(path, 'utf-8'),
      TIMEOUT_READ_MS,
      signal,
    )
    return {
      call_id: call.call_id,
      tool_name: call.tool_name,
      output: truncate(content),
      error: null,
      status: 'success',
    }
  }

  private async executeFileWrite(
    call: ToolCallEvent,
    signal: AbortSignal,
  ): Promise<ToolResultPayload> {
    const path = (call.arguments.path as string) || ''
    const content = (call.arguments.content as string) || ''
    await withTimeout(
      this.container!.fs.writeFile(path, content),
      TIMEOUT_READ_MS,
      signal,
    )
    return {
      call_id: call.call_id,
      tool_name: call.tool_name,
      output: JSON.stringify({ written: path, bytes: content.length }),
      error: null,
      status: 'success',
    }
  }

  private async executeListDirectory(
    call: ToolCallEvent,
    signal: AbortSignal,
  ): Promise<ToolResultPayload> {
    const path = (call.arguments.path as string) || '.'
    const entries = await withTimeout(
      this.container!.fs.readdir(path, { withFileTypes: true }),
      TIMEOUT_READ_MS,
      signal,
    )
    const listing = entries.map(e => ({
      name: e.name,
      type: e.isDirectory() ? 'directory' : 'file',
    }))
    return {
      call_id: call.call_id,
      tool_name: call.tool_name,
      output: truncate(JSON.stringify(listing, null, 2)),
      error: null,
      status: 'success',
    }
  }

  private async executeShell(
    call: ToolCallEvent,
    signal: AbortSignal,
  ): Promise<ToolResultPayload> {
    const command = (call.arguments.command as string) || ''
    // Split command into cmd and args (simple split on spaces)
    const parts = command.split(/\s+/)
    const cmd = parts[0] || 'echo'
    const args = parts.slice(1)

    const process = await this.container!.spawn(cmd, args)
    let output = ''
    let truncated = false

    // Read output stream
    const reader = process.output.getReader()
    try {
      while (true) {
        if (signal.aborted) {
          reader.cancel()
          return {
            call_id: call.call_id,
            tool_name: call.tool_name,
            output: truncate(output),
            error: 'Cancelled',
            status: 'cancelled',
          }
        }

        const { done, value } = await Promise.race([
          reader.read(),
          timeout(TIMEOUT_SHELL_MS).then(() => ({ done: true, value: undefined, timedOut: true })),
        ]) as { done: boolean; value: string | undefined; timedOut?: boolean }

        if ((value as unknown as { timedOut?: boolean })?.timedOut) {
          reader.cancel()
          return {
            call_id: call.call_id,
            tool_name: call.tool_name,
            output: truncate(output),
            error: 'Shell command timed out',
            status: 'timeout',
          }
        }

        if (done) break

        if (value) {
          // Emit streaming event for UI
          if (this.onStream) {
            this.onStream(value)
          }

          if (!truncated) {
            output += value
            if (output.length > MAX_OUTPUT_BYTES) {
              truncated = true
            }
          }
        }
      }
    } finally {
      reader.releaseLock()
    }

    const exitCode = await process.exit
    const result = JSON.stringify({
      exitCode,
      output: truncate(output),
    })

    return {
      call_id: call.call_id,
      tool_name: call.tool_name,
      output: result,
      error: exitCode !== 0 ? `Process exited with code ${exitCode}` : null,
      status: exitCode === 0 ? 'success' : 'error',
    }
  }
}

// ─── Utilities ───

function truncate(text: string): string {
  if (text.length <= MAX_OUTPUT_BYTES) return text
  return text.slice(0, MAX_OUTPUT_BYTES - TRUNCATION_MARKER.length) + TRUNCATION_MARKER
}

function timeout(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

async function withTimeout<T>(
  promise: Promise<T>,
  ms: number,
  signal: AbortSignal,
): Promise<T> {
  return Promise.race([
    promise,
    new Promise<never>((_, reject) => {
      const timer = setTimeout(() => reject(new Error('Operation timed out')), ms)
      signal.addEventListener('abort', () => {
        clearTimeout(timer)
        reject(new Error('Cancelled'))
      })
    }),
  ])
}

/**
 * history.ts — typed client-side history.
 *
 * Deliberately in-memory only: nothing is written to any browser
 * persistence API, so sensitive text can never outlive the page
 * unexpectedly. Clearing is explicit. History is never injected
 * into prompts.
 */

import type { HistoryEntry, HistoryStore } from '../types'

const MAX_ENTRIES = 50
const PREVIEW_CHARS = 80

export function toPreview(text: string): string {
  const collapsed = text.replace(/\s+/g, ' ').trim()
  return collapsed.length <= PREVIEW_CHARS
    ? collapsed
    : `${collapsed.slice(0, PREVIEW_CHARS)}…`
}

export class InMemoryHistoryStore implements HistoryStore {
  private entries: HistoryEntry[] = []

  list(): HistoryEntry[] {
    return [...this.entries]
  }

  add(entry: HistoryEntry): void {
    this.entries = [entry, ...this.entries].slice(0, MAX_ENTRIES)
  }

  clear(): void {
    this.entries = []
  }
}

export function createHistoryStore(): HistoryStore {
  return new InMemoryHistoryStore()
}

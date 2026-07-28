/**
 * types.ts — TypeScript mirror of the shared assistant contracts:
 * openai_project/schemas/assistant/{request,response}.schema.json
 *
 * The web client speaks only this envelope, only to the assistant
 * backend. It never talks to a model provider directly.
 */

export type AssistantMode =
  | 'fix_wording'
  | 'draft_three_replies'
  | 'translate_slang_jargon'
  | 'teach_clearly'
  | 'expand_idea'
  | 'visualize_design'

export type AssistantStatus = 'completed' | 'uncertainty' | 'failed'

export type PublicErrorCategory =
  | 'configuration'
  | 'unavailable'
  | 'timeout'
  | 'authentication'
  | 'rate_limited'
  | 'invalid_response'
  | 'unsupported'
  | 'internal'

export interface AssistantRequest {
  request_id: string
  mode: AssistantMode
  text: string
  selected_text?: string | null
  context?: string | null
  tone?: string | null
  workspace_id?: string | null
}

// ---- Per-mode result contracts ----

export interface FixWordingChange {
  original: string
  correction: string
  reason: string
  intentional_possible?: boolean
  /** Grammar-checker detail; absent on model-produced changes. */
  offset?: number | null
  length?: number | null
  rule_id?: string
  replacements?: string[]
}

export interface FixWordingResult {
  corrected_text: string
  /** Semantic shape of the result; execution provenance lives on the envelope. */
  result_kind?: 'model' | 'languagetool'
  changes?: FixWordingChange[]
  no_changes_needed?: boolean
  inferred_intent?: string
  tone?: string
  context?: string
  assumptions?: string[]
  uncertainty?: string[]
}

export interface DraftThreeRepliesResult {
  in_short: string
  brief: string
  thorough: string
  diplomatic: string
  message_purpose: string
  tone: string
  uncertainty?: string[]
}

export interface TranslateSlangJargonResult {
  term: string
  professional_translation: string
  plain_meaning: string
  origin_context: string
  usage_notes?: string[]
  example: { original: string; professional: string }
}

export interface SelectionRequiredResult {
  selection_required: true
  message: string
}

export interface TeachClearlyResult {
  basics: string
  building_from_there?: string[]
  key_insights?: string[]
  common_misconceptions?: string[]
  why_this_matters: string
  check_your_understanding?: string[]
}

export interface ExpandIdeaResult {
  expanded_text: string
  preserved_intent: string
  added_assumptions?: string[]
  uncertainty?: string[]
}

export interface VisualizeDesignResult {
  diagram_code: string
  summary: string
  builder_prompt: string
  warnings?: string[]
}

export interface ErrorResult {
  error: { category: PublicErrorCategory; message: string }
}

export type AssistantResult =
  | FixWordingResult
  | DraftThreeRepliesResult
  | TranslateSlangJargonResult
  | SelectionRequiredResult
  | TeachClearlyResult
  | ExpandIdeaResult
  | VisualizeDesignResult
  | ErrorResult

export interface ProviderInfo {
  id: string
  source: 'local' | 'cloud'
}

export interface AssistantResponse {
  request_id: string
  mode: AssistantMode
  status: AssistantStatus
  result: AssistantResult
  warnings: string[]
  provider: ProviderInfo | null
  usage: Record<string, number> | null
  /**
   * Execution metadata (fix_wording engine selection). `provider` stays
   * provider identity only — fallback state is never encoded into it.
   */
  engine?: 'model' | 'languagetool' | null
  fallback_used?: boolean
  fallback_reason?: string
  uncertainty: string[]
}

// ---- Result narrowing helpers ----

export function isErrorResult(result: AssistantResult): result is ErrorResult {
  return typeof result === 'object' && result !== null && 'error' in result
}

export function isSelectionRequired(
  result: AssistantResult,
): result is SelectionRequiredResult {
  return typeof result === 'object' && result !== null && 'selection_required' in result
}

// ---- History (client-side, in-memory only; no hidden persistence) ----

export interface HistoryEntry {
  request_id: string
  mode: AssistantMode
  status: AssistantStatus
  created_at: string
  /** Short display snippet of the input, kept client-side only. */
  input_preview: string
  response: AssistantResponse
}

export interface HistoryStore {
  list(): HistoryEntry[]
  add(entry: HistoryEntry): void
  clear(): void
}

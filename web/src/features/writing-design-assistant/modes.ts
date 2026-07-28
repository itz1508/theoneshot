/**
 * modes.ts — display metadata for the six assistant modes.
 * The mode list mirrors the backend AssistantMode enum exactly.
 */

import {
  SpellCheck,
  MessagesSquare,
  Languages,
  GraduationCap,
  Sparkles,
  Workflow,
} from 'lucide-react'
import type { AssistantMode } from './types'

export interface ModeDefinition {
  id: AssistantMode
  label: string
  description: string
  icon: typeof SpellCheck
  requiresSelectedText: boolean
}

export const MODES: ModeDefinition[] = [
  {
    id: 'fix_wording',
    label: 'Improve my message',
    description: 'Fix wording and clarify your intent without losing your voice.',
    icon: SpellCheck,
    requiresSelectedText: false,
  },
  {
    id: 'draft_three_replies',
    label: 'Draft replies',
    description: 'Three ready-to-send replies: brief, thorough, diplomatic.',
    icon: MessagesSquare,
    requiresSelectedText: false,
  },
  {
    id: 'translate_slang_jargon',
    label: 'Translate slang',
    description: 'Explain a selected slang or jargon term professionally.',
    icon: Languages,
    requiresSelectedText: true,
  },
  {
    id: 'teach_clearly',
    label: 'Teach clearly',
    description: 'Step-by-step explanation from the basics up.',
    icon: GraduationCap,
    requiresSelectedText: false,
  },
  {
    id: 'expand_idea',
    label: 'Expand idea',
    description: 'Grow a rough idea while keeping your meaning.',
    icon: Sparkles,
    requiresSelectedText: false,
  },
  {
    id: 'visualize_design',
    label: 'Visualize design',
    description: 'Turn a described design into a local diagram.',
    icon: Workflow,
    requiresSelectedText: false,
  },
]

export function modeDefinition(id: AssistantMode): ModeDefinition {
  const found = MODES.find((mode) => mode.id === id)
  if (!found) throw new Error(`Unknown assistant mode: ${id}`)
  return found
}

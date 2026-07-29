/**
 * ModelSelector — model dropdown for the fixed, server-configured provider.
 *
 * Presentational only: the listing is fetched by the feature root. The
 * selector chooses a model *within* the active provider; it never selects
 * the provider itself. Hidden entirely when no listing is available.
 */

import { useId } from 'react'
import type { AssistantModelsResponse } from '../types'
import styles from '../WritingDesignAssistant.module.css'

interface ModelSelectorProps {
  models: AssistantModelsResponse | null
  /** Currently selected model; '' means the provider default. */
  value: string
  disabled?: boolean
  onChange: (model: string) => void
}

export function ModelSelector({ models, value, disabled, onChange }: ModelSelectorProps) {
  const selectId = useId()
  if (!models || models.available_models.length === 0) return null

  return (
    <div className={styles.modelSelectorRow}>
      <label className={styles.fieldLabel} htmlFor={selectId}>
        Model ({models.provider.id})
      </label>
      <select
        id={selectId}
        className={styles.modelSelect}
        value={value || models.current_model}
        disabled={disabled}
        onChange={(event) =>
          onChange(event.target.value === models.current_model ? '' : event.target.value)
        }
      >
        {models.available_models.map((model) => (
          <option key={model} value={model}>
            {model === models.current_model ? `${model} (default)` : model}
          </option>
        ))}
      </select>
      {models.reachable === false ? (
        <span className={styles.modelUnreachable} role="status">
          Model engine unreachable — using configured default.
        </span>
      ) : null}
    </div>
  )
}

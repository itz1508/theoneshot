/**
 * assistant-api.test.ts — service behavior and static safety guarantees:
 * request shape, normalized failures, no direct provider URLs, no
 * window.storage, no CDN Mermaid anywhere in the feature or app entry.
 */

import { describe, expect, it, vi } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import {
  ASSISTANT_ENDPOINT,
  MODELS_ENDPOINT,
  buildRequest,
  fetchAssistantModels,
  resolveAssistantEndpoint,
  submitAssistantRequest,
} from '../services/assistantApi'
import type { AssistantModelsResponse, AssistantResponse } from '../types'

function envelope(overrides: Partial<AssistantResponse> = {}): AssistantResponse {
  return {
    request_id: 'r1',
    mode: 'fix_wording',
    status: 'completed',
    result: { corrected_text: 'Fixed.' },
    warnings: [],
    provider: { id: 'fake-deterministic', source: 'local' },
    usage: null,
    uncertainty: [],
    ...overrides,
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('buildRequest', () => {
  it('produces the contract shape with a generated request_id', () => {
    const request = buildRequest({ mode: 'expand_idea', text: 'grow this' })
    expect(request.request_id).toBeTruthy()
    expect(request.mode).toBe('expand_idea')
    expect(request.text).toBe('grow this')
    expect('selected_text' in request).toBe(false)
    expect('api_key' in request).toBe(false)
  })

  it('includes optional fields only when set', () => {
    const request = buildRequest({
      mode: 'translate_slang_jargon',
      text: 'circle back on this',
      selectedText: 'circle back',
      tone: 'casual',
    })
    expect(request.selected_text).toBe('circle back')
    expect(request.tone).toBe('casual')
    expect('context' in request).toBe(false)
  })

  it('includes the model override only when set, and never a provider', () => {
    const withModel = buildRequest({ mode: 'fix_wording', text: 'hi', model: 'llama3.2:3b' })
    expect(withModel.model).toBe('llama3.2:3b')
    const withoutModel = buildRequest({ mode: 'fix_wording', text: 'hi' })
    expect('model' in withoutModel).toBe(false)
    expect('provider' in withModel).toBe(false)
  })
})

describe('resolveAssistantEndpoint', () => {
  it('returns the relative endpoint when the variable is unset or empty', () => {
    expect(resolveAssistantEndpoint(undefined)).toEqual({ url: ASSISTANT_ENDPOINT })
    expect(resolveAssistantEndpoint('')).toEqual({ url: ASSISTANT_ENDPOINT })
    expect(resolveAssistantEndpoint('   ')).toEqual({ url: ASSISTANT_ENDPOINT })
  })

  it('joins an absolute base URL with the endpoint path', () => {
    expect(resolveAssistantEndpoint('https://backend.example.com')).toEqual({
      url: 'https://backend.example.com/v1/assistant/requests',
    })
    expect(resolveAssistantEndpoint('https://backend.example.com/')).toEqual({
      url: 'https://backend.example.com/v1/assistant/requests',
    })
  })

  it('preserves a path prefix on the base URL', () => {
    expect(resolveAssistantEndpoint('https://gateway.example.com/audisor/')).toEqual({
      url: 'https://gateway.example.com/audisor/v1/assistant/requests',
    })
  })

  it('rejects malformed values as configuration errors', () => {
    expect(resolveAssistantEndpoint('not-a-url')).toEqual({ error: 'configuration' })
    expect(resolveAssistantEndpoint('ftp://x.example.com')).toEqual({ error: 'configuration' })
    expect(resolveAssistantEndpoint('https://x.example.com/?q=1')).toEqual({
      error: 'configuration',
    })
    expect(resolveAssistantEndpoint('https://x.example.com/#frag')).toEqual({
      error: 'configuration',
    })
  })
})

describe('submitAssistantRequest', () => {
  it('posts to the relative assistant endpoint only', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(envelope()))
    await submitAssistantRequest({ mode: 'fix_wording', text: 'hi' }, { fetchImpl })
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe(ASSISTANT_ENDPOINT)
    expect(url).toBe('/v1/assistant/requests')
    const body = JSON.parse((init as RequestInit).body as string)
    expect(body.mode).toBe('fix_wording')
    expect(Object.keys(body)).not.toContain('api_key')
  })

  it('normalizes network failure to an unavailable envelope', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('fetch failed'))
    const response = await submitAssistantRequest({ mode: 'fix_wording', text: 'hi' }, { fetchImpl })
    expect(response.status).toBe('failed')
    expect(response.result).toMatchObject({ error: { category: 'unavailable' } })
  })

  it('normalizes 401 to an authentication failure', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ detail: 'no' }, 401))
    const response = await submitAssistantRequest({ mode: 'fix_wording', text: 'hi' }, { fetchImpl })
    expect(response.status).toBe('failed')
    expect(response.result).toMatchObject({ error: { category: 'authentication' } })
  })

  it('normalizes a malformed body to invalid_response', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(new Response('not json', { status: 200 }))
    const response = await submitAssistantRequest({ mode: 'fix_wording', text: 'hi' }, { fetchImpl })
    expect(response.status).toBe('failed')
    expect(response.result).toMatchObject({ error: { category: 'invalid_response' } })
  })

  it('passes a contract-valid envelope through untouched', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(envelope({ status: 'uncertainty' })))
    const response = await submitAssistantRequest({ mode: 'fix_wording', text: 'hi' }, { fetchImpl })
    expect(response.status).toBe('uncertainty')
    expect(response.provider?.id).toBe('fake-deterministic')
  })

  it('targets the configured external backend when apiBaseUrl is set', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(envelope()))
    await submitAssistantRequest(
      { mode: 'fix_wording', text: 'hi' },
      { fetchImpl, apiBaseUrl: 'https://backend.example.com' },
    )
    expect(fetchImpl.mock.calls[0][0]).toBe(
      'https://backend.example.com/v1/assistant/requests',
    )
  })

  it('returns a configuration envelope without any network call for a malformed base URL', async () => {
    const fetchImpl = vi.fn()
    const response = await submitAssistantRequest(
      { mode: 'fix_wording', text: 'hi' },
      { fetchImpl, apiBaseUrl: 'not-a-url' },
    )
    expect(fetchImpl).not.toHaveBeenCalled()
    expect(response.status).toBe('failed')
    expect(response.result).toMatchObject({ error: { category: 'configuration' } })
  })

  it.each([500, 502])('normalizes HTTP %i from a dead proxy/upstream to unavailable', async (status) => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(new Response('Bad Gateway', { status }))
    const response = await submitAssistantRequest({ mode: 'fix_wording', text: 'hi' }, { fetchImpl })
    expect(response.status).toBe('failed')
    expect(response.result).toMatchObject({ error: { category: 'unavailable' } })
  })

  it('sends the selected model in the request body', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(envelope()))
    await submitAssistantRequest(
      { mode: 'fix_wording', text: 'hi', model: 'qwen2.5:7b' },
      { fetchImpl },
    )
    const body = JSON.parse((fetchImpl.mock.calls[0][1] as RequestInit).body as string)
    expect(body.model).toBe('qwen2.5:7b')
  })
})

describe('fetchAssistantModels', () => {
  function listing(overrides: Partial<AssistantModelsResponse> = {}): AssistantModelsResponse {
    return {
      provider: { id: 'local-openai-compatible', source: 'local' },
      current_model: 'llama3.2:3b',
      available_models: ['llama3.2:3b', 'qwen2.5:7b'],
      reachable: true,
      ...overrides,
    }
  }

  it('GETs the models endpoint and returns a contract-valid listing', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(listing()))
    const result = await fetchAssistantModels({ fetchImpl })
    expect(result).toEqual(listing())
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe(MODELS_ENDPOINT)
    expect((init as RequestInit).method).toBe('GET')
    expect((init as RequestInit).body).toBeUndefined()
  })

  it('targets the configured external backend when apiBaseUrl is set', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(listing()))
    await fetchAssistantModels({ fetchImpl, apiBaseUrl: 'https://backend.example.com' })
    expect(fetchImpl.mock.calls[0][0]).toBe('https://backend.example.com/v1/assistant/models')
  })

  it('returns null on a non-200 status', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ detail: 'no' }, 401))
    expect(await fetchAssistantModels({ fetchImpl })).toBeNull()
  })

  it('returns null on network failure', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('fetch failed'))
    expect(await fetchAssistantModels({ fetchImpl })).toBeNull()
  })

  it('returns null for a malformed or contract-violating body', async () => {
    const malformed = vi.fn().mockResolvedValue(new Response('not json', { status: 200 }))
    expect(await fetchAssistantModels({ fetchImpl: malformed })).toBeNull()
    const wrongShape = vi
      .fn()
      .mockResolvedValue(jsonResponse({ current_model: 1, available_models: 'x' }))
    expect(await fetchAssistantModels({ fetchImpl: wrongShape })).toBeNull()
  })

  it('returns null for a malformed base URL without a network call', async () => {
    const fetchImpl = vi.fn()
    expect(await fetchAssistantModels({ fetchImpl, apiBaseUrl: 'not-a-url' })).toBeNull()
    expect(fetchImpl).not.toHaveBeenCalled()
  })
})

describe('client code safety', () => {
  const featureDir = join(__dirname, '..')

  function collectSources(dir: string): string[] {
    const files: string[] = []
    for (const name of readdirSync(dir)) {
      const path = join(dir, name)
      if (statSync(path).isDirectory()) {
        if (name !== 'tests') files.push(...collectSources(path))
      } else if (/\.(ts|tsx|css)$/.test(name)) {
        files.push(path)
      }
    }
    return files
  }

  const FORBIDDEN = [
    'window.storage',
    'api.openai.com',
    'api.anthropic.com',
    'cdn.jsdelivr',
    'unpkg.com',
    'mermaid.min.js',
    'localStorage',
    'sessionStorage',
  ]

  it('feature sources contain no provider URLs, window.storage, or CDN Mermaid', () => {
    for (const file of collectSources(featureDir)) {
      const source = readFileSync(file, 'utf-8')
      for (const marker of FORBIDDEN) {
        expect(source, `${file} must not contain ${marker}`).not.toContain(marker)
      }
    }
  })

  it('index.html loads no CDN Mermaid script', () => {
    const html = readFileSync(join(__dirname, '..', '..', '..', '..', 'index.html'), 'utf-8')
    expect(html).not.toContain('mermaid.min.js')
    expect(html).not.toContain('cdn.jsdelivr')
    expect(html).not.toContain('unpkg.com')
  })
})

/**
 * sample-project.test.ts — the bundled starter is a real project shape:
 * parseable package.json with vite + dev/build scripts, and an index.html
 * entry. Nothing simulated.
 */

import { describe, expect, it } from 'vitest'
import { SAMPLE_PROJECT_NAME, sampleProjectTree } from '../sampleProject'

function fileContents(name: string): string {
  const node = sampleProjectTree[name] as { file: { contents: string } } | undefined
  expect(node, `${name} must exist in the sample tree`).toBeDefined()
  return node!.file.contents
}

describe('sampleProjectTree', () => {
  it('has a parseable package.json with vite and dev/build scripts', () => {
    const pkg = JSON.parse(fileContents('package.json'))
    expect(pkg.name).toBe(SAMPLE_PROJECT_NAME)
    expect(pkg.devDependencies.vite).toBeTruthy()
    expect(pkg.scripts.dev).toBe('vite')
    expect(pkg.scripts.build).toBe('vite build')
  })

  it('contains an index.html that loads the module entry', () => {
    const html = fileContents('index.html')
    expect(html).toContain('<div id="app">')
    expect(html).toContain('src="/main.js"')
  })

  it('ships the remaining starter files', () => {
    expect(fileContents('main.js')).toContain("import './style.css'")
    expect(fileContents('style.css')).toContain('body')
    expect(fileContents('README.md')).toContain('Sample project')
  })
})

/**
 * file-tree.test.ts — directory-handle traversal with plain object mocks
 * (the structural DirectoryHandleLike/FileHandleLike types exist for
 * exactly this).
 */

import { describe, expect, it } from 'vitest'
import { directoryHandleToTree, isDirectoryPickerSupported, MAX_PROJECT_FILES } from '../runtime/fileTree'
import type { DirectoryHandleLike, FileHandleLike } from '../types'

function file(name: string, contents = ''): FileHandleLike {
  return {
    kind: 'file',
    name,
    getFile: () => Promise.resolve(new File([contents], name)),
  }
}

function dir(name: string, entries: (DirectoryHandleLike | FileHandleLike)[]): DirectoryHandleLike {
  return {
    kind: 'directory',
    name,
    values: () =>
      (async function* () {
        yield* entries
      })(),
  }
}

describe('directoryHandleToTree', () => {
  it('converts nested directories and reads files as Uint8Array', async () => {
    const root = dir('my-app', [
      file('package.json', '{"name":"my-app"}'),
      dir('src', [file('main.js', 'console.log(1)')]),
    ])

    const tree = await directoryHandleToTree(root)

    const pkg = tree['package.json'] as { file: { contents: Uint8Array } }
    expect(pkg.file.contents).toBeInstanceOf(Uint8Array)
    expect(new TextDecoder().decode(pkg.file.contents)).toBe('{"name":"my-app"}')

    const src = tree['src'] as { directory: Record<string, { file: { contents: Uint8Array } }> }
    expect(new TextDecoder().decode(src.directory['main.js'].file.contents)).toBe('console.log(1)')
  })

  it('skips node_modules, .git, dist and .cache', async () => {
    const root = dir('my-app', [
      file('package.json', '{}'),
      dir('node_modules', [file('should-not-appear.js')]),
      dir('.git', [file('HEAD')]),
      dir('dist', [file('bundle.js')]),
      dir('.cache', [file('x')]),
      dir('src', [file('kept.js')]),
    ])

    const tree = await directoryHandleToTree(root)

    expect(Object.keys(tree).sort()).toEqual(['package.json', 'src'])
  })

  it('aborts with a clear error when the root has no package.json', async () => {
    const root = dir('parent-folder', [dir('actual-project', [file('package.json', '{}')])])
    await expect(directoryHandleToTree(root)).rejects.toThrow(/no package\.json at its root/)
  })

  it(`aborts when the project exceeds ${MAX_PROJECT_FILES} files`, async () => {
    const many = Array.from({ length: MAX_PROJECT_FILES + 1 }, (_, i) => file(`f${i}.txt`))
    const root = dir('huge', [file('package.json', '{}'), ...many])
    await expect(directoryHandleToTree(root)).rejects.toThrow(/exceeds 2000 files/)
  })
})

describe('isDirectoryPickerSupported', () => {
  it('is false in environments without showDirectoryPicker (jsdom)', () => {
    expect('showDirectoryPicker' in window).toBe(false)
    expect(isDirectoryPickerSupported()).toBe(false)
  })

  it('is true when the API is present', () => {
    ;(window as { showDirectoryPicker?: unknown }).showDirectoryPicker = () => Promise.reject()
    try {
      expect(isDirectoryPickerSupported()).toBe(true)
    } finally {
      delete (window as { showDirectoryPicker?: unknown }).showDirectoryPicker
    }
  })
})

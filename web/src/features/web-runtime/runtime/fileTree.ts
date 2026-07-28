/**
 * fileTree — converts a File System Access directory handle into a
 * WebContainer FileSystemTree. Files are read into the in-browser
 * container only; nothing is uploaded anywhere.
 */

import type { FileSystemTree } from '@webcontainer/api'
import type { DirectoryHandleLike, FileHandleLike } from '../types'

/** Directories that are regenerated inside the container — never copied. */
const SKIP_DIRECTORIES = new Set(['node_modules', '.git', 'dist', '.cache'])

/** Hard cap so a mistaken "open C:\" pick fails fast and honestly. */
export const MAX_PROJECT_FILES = 2000

export function isDirectoryPickerSupported(): boolean {
  return typeof window !== 'undefined' && 'showDirectoryPicker' in window
}

async function readFileBytes(handle: FileHandleLike): Promise<Uint8Array> {
  const file = await handle.getFile()
  return new Uint8Array(await file.arrayBuffer())
}

/**
 * Recursively converts `handle` into a mountable tree.
 * Throws with a clear message if the folder has no root `package.json`
 * or exceeds {@link MAX_PROJECT_FILES} files.
 */
export async function directoryHandleToTree(handle: DirectoryHandleLike): Promise<FileSystemTree> {
  const counter = { files: 0 }
  const tree = await walkDirectory(handle, counter)
  if (!('package.json' in tree)) {
    throw new Error(
      `"${handle.name}" has no package.json at its root — pick the project folder itself, not a parent directory.`,
    )
  }
  return tree
}

async function walkDirectory(
  handle: DirectoryHandleLike,
  counter: { files: number },
): Promise<FileSystemTree> {
  const tree: FileSystemTree = {}
  for await (const entry of handle.values()) {
    if (entry.kind === 'directory') {
      if (SKIP_DIRECTORIES.has(entry.name)) continue
      tree[entry.name] = { directory: await walkDirectory(entry, counter) }
    } else {
      counter.files += 1
      if (counter.files > MAX_PROJECT_FILES) {
        throw new Error(
          `Project exceeds ${MAX_PROJECT_FILES} files — pick a single project folder (node_modules/dist are already skipped).`,
        )
      }
      tree[entry.name] = { file: { contents: await readFileBytes(entry) } }
    }
  }
  return tree
}

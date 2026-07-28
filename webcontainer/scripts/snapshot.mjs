/**
 * snapshot.mjs — packs the web app (web/) into a JSON file tree the
 * WebContainer host mounts at boot.
 *
 * Output: webcontainer/host/public/files.snapshot.json (generated,
 * git-ignored — regenerate after any web/ change).
 *
 * Includes package.json + package-lock.json for a reproducible in-container
 * install. Excludes node_modules, dist, and all .env* files so no local
 * environment configuration ever leaks into the snapshot.
 */
import { readdirSync, readFileSync, statSync, mkdirSync, writeFileSync } from 'node:fs'
import { join, relative, dirname, extname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const webRoot = join(here, '..', '..', 'web')
const outFile = join(here, '..', 'host', 'public', 'files.snapshot.json')

const EXCLUDED_DIRS = new Set(['node_modules', 'dist', '.pytest_cache', 'coverage'])
const BINARY_EXTENSIONS = new Set([
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico',
  '.woff', '.woff2', '.ttf', '.eot', '.otf', '.pdf',
])

function isExcludedFile(name) {
  return name.startsWith('.env') // never ship local environment files
}

function collect(dir, files) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    const stats = statSync(path)
    if (stats.isDirectory()) {
      if (!EXCLUDED_DIRS.has(name)) collect(path, files)
    } else if (!isExcludedFile(name)) {
      // WebContainer mount paths always use forward slashes.
      const key = relative(webRoot, path).split('\\').join('/')
      if (BINARY_EXTENSIONS.has(extname(name).toLowerCase())) {
        files[key] = { kind: 'binary', base64: readFileSync(path).toString('base64') }
      } else {
        files[key] = { kind: 'text', contents: readFileSync(path, 'utf-8') }
      }
    }
  }
}

const files = {}
collect(webRoot, files)

const fileCount = Object.keys(files).length
if (!files['package.json'] || !files['package-lock.json']) {
  console.error('snapshot: web/package.json or package-lock.json missing — aborting')
  process.exit(1)
}

const snapshot = {
  generatedAt: new Date().toISOString(),
  source: 'web',
  fileCount,
  files,
}

mkdirSync(dirname(outFile), { recursive: true })
writeFileSync(outFile, JSON.stringify(snapshot))
console.log(`snapshot: wrote ${fileCount} files to ${outFile}`)

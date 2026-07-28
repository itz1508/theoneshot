/**
 * webruntime-postbuild-validate.mjs — fixture-driven post-build gate for
 * the in-app Web Runtime feature (mandatory after `npm run build`).
 *
 * Proves against the real built artifact (web/dist):
 *   1. clean production build succeeds (no VITE_ASSISTANT_API_URL in env);
 *   2. dist/index.html exists and references a hashed JS entry;
 *   3. the feature marker lives in a lazy chunk ONLY: some dist JS
 *      contains it AND no chunk referenced by index.html contains it
 *      (Vite lists only entry/preload chunks there) — structural proof
 *      that WebContainer code loads only when the feature is opened;
 *   4. required feature UI markers shipped somewhere in dist JS;
 *   5. forbidden markers absent from every text file in dist/;
 *   6. dist/ serves under COOP/COEP isolation headers (HTTP-checked) —
 *      required for crossOriginIsolated in production.
 *
 * Exit 0 only if every assertion passes. Log: TempProof/web-runtime/webruntime-postbuild.txt
 */
import { spawnSync } from 'node:child_process'
import { createServer } from 'node:http'
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join, dirname, extname, basename } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = join(here, '..', '..')
const webDir = join(repoRoot, 'web')
const distDir = join(webDir, 'dist')
const fixture = JSON.parse(readFileSync(join(here, '..', 'fixtures', 'webruntime-postbuild.fixture.json'), 'utf-8'))
const logDir = join(repoRoot, 'TempProof', 'web-runtime')
const logFile = join(logDir, 'webruntime-postbuild.txt')

const lines = []
let failed = false

function log(line) {
  lines.push(line)
  console.log(line)
}

function assert(name, condition, detail = '') {
  if (condition) {
    log(`PASS  ${name}`)
  } else {
    failed = true
    log(`FAIL  ${name}${detail ? ` — ${detail}` : ''}`)
  }
}

function collectDistFiles() {
  const files = []
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name)
      if (statSync(path).isDirectory()) walk(path)
      else files.push(path)
    }
  }
  walk(distDir)
  return files
}

function readTextFiles(files) {
  const TEXT = new Set(['.html', '.js', '.css', '.svg', '.json', '.txt', '.map'])
  return files
    .filter((file) => TEXT.has(extname(file).toLowerCase()))
    .map((file) => ({ file, contents: readFileSync(file, 'utf-8') }))
}

// ---------------------------------------------------------------- step 1
const cleanEnv = { ...process.env }
delete cleanEnv.VITE_ASSISTANT_API_URL
log('--- step 1: clean npm run build (no VITE_ASSISTANT_API_URL)')
const build = spawnSync('npm', ['run', 'build'], {
  cwd: webDir,
  env: cleanEnv,
  shell: true,
  encoding: 'utf-8',
  timeout: 600000,
})
log((build.stdout || '').split('\n').slice(-6).join('\n'))
assert('1. clean production build succeeds', build.status === 0)

if (build.status !== 0) {
  finish()
} else {
  await validateBuiltArtifact()
}

async function validateBuiltArtifact() {
  // -------------------------------------------------------------- step 2
  const indexPath = join(distDir, 'index.html')
  const indexExists = existsSync(indexPath)
  assert('2a. dist/index.html exists', indexExists)
  const indexHtml = indexExists ? readFileSync(indexPath, 'utf-8') : ''
  assert('2b. index.html references a hashed JS asset', /assets\/[^"']+\.js/.test(indexHtml))

  // -------------------------------------------------------------- step 3
  const textFiles = readTextFiles(collectDistFiles())
  const jsFiles = textFiles.filter(({ file }) => file.endsWith('.js'))
  const markerChunks = jsFiles.filter(({ contents }) => contents.includes(fixture.feature_marker))
  assert(
    '3a. feature marker present in some dist JS chunk',
    markerChunks.length > 0,
    `expected "${fixture.feature_marker}" in a dist JS asset`,
  )

  // Chunks referenced by index.html = entry + preload set; the feature
  // chunk must not be among them (R11 lazy-loading proof).
  const referencedNames = [...indexHtml.matchAll(/assets\/([^"']+\.js)/g)].map((m) => basename(m[1]))
  const referencedWithMarker = markerChunks.filter(({ file }) => referencedNames.includes(basename(file)))
  assert(
    '3b. no chunk referenced by index.html contains the feature marker (lazy chunk only)',
    referencedWithMarker.length === 0,
    referencedWithMarker.map(({ file }) => basename(file)).join(', '),
  )

  // -------------------------------------------------------------- step 4
  for (const marker of fixture.required_markers_bundle) {
    assert(
      `4. required feature marker shipped in dist JS: ${marker}`,
      jsFiles.some(({ contents }) => contents.includes(marker)),
    )
  }

  // -------------------------------------------------------------- step 5
  for (const marker of fixture.forbidden_markers_everywhere) {
    const hits = textFiles.filter(({ contents }) => contents.includes(marker))
    assert(
      `5. forbidden marker absent everywhere: ${marker}`,
      hits.length === 0,
      hits.map(({ file }) => basename(file)).join(', '),
    )
  }
  // Vendor-scoped markers: allowed ONLY inside the chunk that carries the
  // vendor signature (e.g. stackblitz.com ships inside @webcontainer/api
  // itself), and that chunk must additionally stay out of the entry set.
  for (const { marker, vendor_signature: signature } of fixture.vendor_scoped_markers) {
    const hits = textFiles.filter(({ contents }) => contents.includes(marker))
    const outsideVendor = hits.filter(({ contents }) => !contents.includes(signature))
    assert(
      `5v. vendor-scoped marker "${marker}" only where "${signature}" is present`,
      outsideVendor.length === 0,
      outsideVendor.map(({ file }) => basename(file)).join(', '),
    )
    const vendorInEntry = hits.filter(({ file }) => referencedNames.includes(basename(file)))
    assert(
      `5v. vendor chunk containing "${marker}" is lazy (not referenced by index.html)`,
      vendorInEntry.length === 0,
      vendorInEntry.map(({ file }) => basename(file)).join(', '),
    )
  }

  // -------------------------------------------------------------- step 6
  // Serve dist with the isolation headers production hosting must send
  // (validator-owned static server: deterministic startup/shutdown on Windows).
  const server = createServer((request, response) => {
    const urlPath = request.url === '/' ? '/index.html' : request.url.split('?')[0]
    const filePath = join(distDir, urlPath)
    if (!filePath.startsWith(distDir) || !existsSync(filePath) || statSync(filePath).isDirectory()) {
      response.writeHead(404)
      response.end('not found')
      return
    }
    response.writeHead(200, {
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'require-corp',
    })
    response.end(readFileSync(filePath))
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  const httpResponse = await fetch(`http://127.0.0.1:${port}/`)
  assert('6a. served index returns 200', httpResponse.status === 200)
  assert(
    '6b. Cross-Origin-Opener-Policy header present',
    httpResponse.headers.get('cross-origin-opener-policy') === 'same-origin',
  )
  assert(
    '6c. Cross-Origin-Embedder-Policy header present',
    httpResponse.headers.get('cross-origin-embedder-policy') === 'require-corp',
  )
  server.close()

  finish()
}

function finish() {
  log('')
  log(failed ? 'WEB RUNTIME POST-BUILD VALIDATION FAILED' : 'WEB RUNTIME POST-BUILD VALIDATION PASSED')
  mkdirSync(logDir, { recursive: true })
  writeFileSync(logFile, `${new Date().toISOString()}\n${lines.join('\n')}\n`)
  process.exit(failed ? 1 : 0)
}

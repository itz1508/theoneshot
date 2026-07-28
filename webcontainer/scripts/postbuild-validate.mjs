/**
 * postbuild-validate.mjs — fixture-driven validation gate that runs AFTER
 * a production build of the web app.
 *
 * Proves against the real built artifact (web/dist):
 *   1. build succeeds with VITE_ASSISTANT_API_URL baked in (fixture URL);
 *   2. dist/index.html exists and references hashed assets;
 *   3. the fixture endpoint reaches the JS bundle (env → resolveAssistantEndpoint → artifact);
 *   4. no forbidden markers leak into dist/;
 *   5. dist/ serves under the COOP/COEP isolation headers (HTTP-checked);
 *   6. rebuilding WITHOUT the variable removes the fixture origin (no stale baking).
 *
 * Exit 0 only if every assertion passes. Log: TempProof/webcontainer-deploy/postbuild-validate.txt
 */
import { spawnSync } from 'node:child_process'
import { createServer } from 'node:http'
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join, dirname, extname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = join(here, '..', '..')
const webDir = join(repoRoot, 'web')
const distDir = join(webDir, 'dist')
const fixture = JSON.parse(readFileSync(join(here, '..', 'fixtures', 'postbuild.fixture.json'), 'utf-8'))
const logDir = join(repoRoot, 'TempProof', 'webcontainer-deploy')
const logFile = join(logDir, 'postbuild-validate.txt')

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

function runBuild(env, label) {
  log(`--- ${label}`)
  const result = spawnSync('npm', ['run', 'build'], {
    cwd: webDir,
    env,
    shell: true,
    encoding: 'utf-8',
    timeout: 600000,
  })
  const tail = (result.stdout || '').split('\n').slice(-6).join('\n')
  log(tail)
  return result.status === 0
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
const buildOk = runBuild(
  { ...cleanEnv, VITE_ASSISTANT_API_URL: fixture.backend_url },
  `step 1: npm run build with VITE_ASSISTANT_API_URL=${fixture.backend_url}`,
)
assert('1. build with fixture URL succeeds', buildOk)

if (!buildOk) {
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
  assert(
    '2b. index.html references a hashed JS asset',
    /assets\/[^"']+\.js/.test(indexHtml),
  )

  // -------------------------------------------------------------- step 3
  const distFiles = collectDistFiles()
  const textFiles = readTextFiles(distFiles)
  const jsFiles = textFiles.filter(({ file }) => file.endsWith('.js'))
  assert(
    '3. fixture endpoint origin is baked into the JS bundle',
    jsFiles.some(({ contents }) => contents.includes(fixture.backend_url)),
    `expected ${fixture.backend_url} in a dist JS asset`,
  )

  // -------------------------------------------------------------- step 4
  for (const marker of fixture.forbidden_markers_everywhere) {
    const hits = textFiles.filter(({ contents }) => contents.includes(marker))
    assert(
      `4. forbidden marker absent everywhere: ${marker}`,
      hits.length === 0,
      hits.map(({ file }) => file).join(', '),
    )
  }
  for (const marker of fixture.forbidden_markers_html) {
    assert(`4. forbidden marker absent from index.html: ${marker}`, !indexHtml.includes(marker))
  }

  // -------------------------------------------------------------- step 5
  // Serve dist with the same isolation headers host/vite.config.js uses
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
  assert('5a. served index returns 200', httpResponse.status === 200)
  assert(
    '5b. Cross-Origin-Opener-Policy header present',
    httpResponse.headers.get('cross-origin-opener-policy') === 'same-origin',
  )
  assert(
    '5c. Cross-Origin-Embedder-Policy header present',
    httpResponse.headers.get('cross-origin-embedder-policy') === 'require-corp',
  )
  server.close()

  // -------------------------------------------------------------- step 6
  const rebuildOk = runBuild(cleanEnv, 'step 6: npm run build WITHOUT VITE_ASSISTANT_API_URL')
  assert('6a. rebuild without the variable succeeds', rebuildOk)
  if (rebuildOk) {
    const cleanJs = readTextFiles(collectDistFiles()).filter(({ file }) => file.endsWith('.js'))
    assert(
      '6b. fixture origin absent after clean rebuild (no stale baking)',
      cleanJs.every(({ contents }) => !contents.includes(fixture.backend_url)),
    )
  }

  finish()
}

function finish() {
  log('')
  log(failed ? 'POST-BUILD VALIDATION FAILED' : 'POST-BUILD VALIDATION PASSED')
  mkdirSync(logDir, { recursive: true })
  writeFileSync(logFile, `${new Date().toISOString()}\n${lines.join('\n')}\n`)
  process.exit(failed ? 1 : 0)
}

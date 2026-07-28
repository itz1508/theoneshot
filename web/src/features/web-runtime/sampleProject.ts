/**
 * Sample project — a real, buildable Vite vanilla starter mounted by
 * default. It is honestly labelled "Sample project" in the UI; nothing
 * about it is simulated output. The package name doubles as the
 * post-build gate's feature marker (lazy-chunk proof).
 */

import type { FileSystemTree } from '@webcontainer/api'

export const SAMPLE_PROJECT_NAME = 'audisor-web-runtime-sample'

const packageJson = {
  name: SAMPLE_PROJECT_NAME,
  private: true,
  version: '0.1.0',
  type: 'module',
  scripts: {
    dev: 'vite',
    build: 'vite build',
    preview: 'vite preview',
  },
  devDependencies: {
    vite: '^6.3.5',
  },
}

const indexHtml = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Sample project — Audisor Web Runtime</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/main.js"></script>
  </body>
</html>
`

const mainJs = `import './style.css'

document.querySelector('#app').innerHTML = \`
  <main class="card">
    <h1>Sample project</h1>
    <p>
      This is the bundled Audisor Web Runtime starter — a real Vite
      project running inside a WebContainer in your browser.
    </p>
    <p>
      Replace it with your own application via
      <strong>Open project folder</strong>.
    </p>
    <button id="counter" type="button">count is 0</button>
  </main>
\`

let count = 0
document.querySelector('#counter').addEventListener('click', (event) => {
  count += 1
  event.target.textContent = \`count is \${count}\`
})
`

const styleCss = `:root {
  font-family: system-ui, sans-serif;
  color-scheme: dark;
  background: #16181d;
  color: #e6e6e6;
}

body {
  margin: 0;
  display: grid;
  place-items: center;
  min-height: 100vh;
}

.card {
  max-width: 32rem;
  padding: 2rem;
  border: 1px solid #33363f;
  border-radius: 12px;
  background: #1d2026;
}

button {
  padding: 0.5rem 1rem;
  border-radius: 8px;
  border: 1px solid #444;
  background: #2a2e37;
  color: inherit;
  cursor: pointer;
}
`

const readmeMd = `# Sample project — replace with your own via Open project folder

A minimal Vite vanilla starter bundled with the Audisor Web Runtime
feature. It is a real project: \`npm install\` and \`npm run dev\` run
inside the WebContainer exactly as they would on a local machine.
`

export const sampleProjectTree: FileSystemTree = {
  'package.json': { file: { contents: JSON.stringify(packageJson, null, 2) + '\n' } },
  'index.html': { file: { contents: indexHtml } },
  'main.js': { file: { contents: mainJs } },
  'style.css': { file: { contents: styleCss } },
  'README.md': { file: { contents: readmeMd } },
}

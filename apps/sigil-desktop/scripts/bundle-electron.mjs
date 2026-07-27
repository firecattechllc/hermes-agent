import { build } from 'esbuild'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'

const commit = execFileSync('git', ['rev-parse', '--short=9', 'HEAD'], {
  encoding: 'utf8'
}).trim()
const buildTime = new Date()
  .toISOString()
  .replaceAll('-', '')
  .replaceAll(':', '')
  .replace('T', '.')
  .slice(0, 15)
const buildId = `${commit}-${buildTime}`
const packageVersion = JSON.parse(
  fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8')
).version
const identityDefinitions = {
  __SIGIL_BUILD_ID__: JSON.stringify(buildId),
  __SIGIL_BUILD_COMMIT__: JSON.stringify(commit),
  __SIGIL_BUILD_TIME__: JSON.stringify(buildTime),
  __SIGIL_VERSION__: JSON.stringify(packageVersion)
}

await build({
  bundle: true,
  entryPoints: ['electron/main.ts'],
  external: ['electron'],
  format: 'esm',
  outfile: 'dist/electron-main.mjs',
  platform: 'node',
  define: identityDefinitions,
  sourcemap: true
})

await build({
  bundle: true,
  entryPoints: ['electron/preload.ts'],
  external: ['electron'],
  format: 'cjs',
  outfile: 'dist/electron-preload.cjs',
  platform: 'node',
  define: identityDefinitions
})

console.log(`Sigil build identity: ${buildId}`)

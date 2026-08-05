#!/usr/bin/env node

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'

const release = path.resolve(import.meta.dirname, '../release')
const packageMetadata = JSON.parse(
  fs.readFileSync(path.resolve(import.meta.dirname, '../package.json'), 'utf8')
)
const version = packageMetadata.version
const appPath = path.join(release, 'mac-arm64', 'Sigil.app')
const dmg = path.join(release, `Sigil-${version}-mac-arm64.dmg`)
const zip = path.join(release, `Sigil-${version}-mac-arm64.zip`)
const run = (command, args) => {
  const result = spawnSync(command, args, { encoding: 'utf8' })
  return { command: [command, ...args].join(' '), passed: result.status === 0, exit_code: result.status }
}
const signature = fs.existsSync(appPath)
  ? run('codesign', ['--verify', '--deep', '--strict', appPath])
  : { command: 'codesign', passed: false, exit_code: null }
const gatekeeper = fs.existsSync(appPath)
  ? run('spctl', ['--assess', '--type', 'execute', appPath])
  : { command: 'spctl', passed: false, exit_code: null }
const stapleApp = fs.existsSync(appPath)
  ? run('xcrun', ['stapler', 'validate', appPath])
  : { command: 'xcrun stapler validate app', passed: false, exit_code: null }
const stapleDmg = fs.existsSync(dmg)
  ? run('xcrun', ['stapler', 'validate', dmg])
  : { command: 'xcrun stapler validate dmg', passed: false, exit_code: null }
const signed = signature.passed && gatekeeper.passed
const notarized = stapleApp.passed || stapleDmg.passed
const report = {
  schema_version: 1,
  version,
  classification: notarized ? 'notarized' : signed ? 'signed-only' : 'unsigned-test',
  signed,
  notarized,
  stapled: stapleApp.passed && stapleDmg.passed,
  gatekeeper_accepted: gatekeeper.passed,
  artifacts: {
    app: fs.existsSync(appPath),
    dmg: fs.existsSync(dmg),
    zip: fs.existsSync(zip),
    metadata: fs.existsSync(path.join(release, 'latest-mac.yml'))
  },
  checks: { signature, gatekeeper, staple_app: stapleApp, staple_dmg: stapleDmg }
}
fs.mkdirSync(path.join(release, 'certification'), { recursive: true })
fs.writeFileSync(
  path.join(release, 'certification', `v${version}-release.json`),
  JSON.stringify(report, null, 2) + '\n'
)
process.stdout.write(JSON.stringify(report, null, 2) + '\n')
if (process.argv[2] === 'verify' && !fs.existsSync(appPath)) process.exit(2)

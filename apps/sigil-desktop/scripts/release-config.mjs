#!/usr/bin/env node

import { execFileSync, spawnSync } from 'node:child_process'
import process from 'node:process'

const mode = process.argv[2] ?? 'check'
const identityOutput = spawnSync('security', ['find-identity', '-v', '-p', 'codesigning'], {
  encoding: 'utf8'
})
const hasDeveloperId = /Developer ID Application:/.test(identityOutput.stdout ?? '')
const hasAppleIdCredentials = ['APPLE_ID', 'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID']
  .every(name => Boolean(process.env[name]))
const hasApiCredentials = ['APPLE_API_KEY', 'APPLE_API_KEY_ID', 'APPLE_API_ISSUER']
  .every(name => Boolean(process.env[name]))
const hasNotaryCredentials = hasAppleIdCredentials || hasApiCredentials

if (mode === 'check') {
  process.stdout.write(JSON.stringify({
    github_release_repository: 'firecattechllc/hermes-agent',
    signing_identity_available: hasDeveloperId,
    notarization_credentials_available: hasNotaryCredentials,
    ordinary_build_publishes: false
  }, null, 2) + '\n')
  process.exit(0)
}

if (mode === 'unsigned-test') {
  process.env.CSC_IDENTITY_AUTO_DISCOVERY = 'false'
  execFileSync('npm', ['run', 'build'], { stdio: 'inherit', env: process.env })
  execFileSync('npx', ['electron-builder', '--mac', '--arm64', '--publish', 'never'], {
    stdio: 'inherit',
    env: process.env
  })
  process.exit(0)
}

if (mode === 'notarized') {
  if (!hasDeveloperId || !hasNotaryCredentials) {
    process.stderr.write(
      'Notarized release refused: Developer ID Application identity and Apple notarization credentials are both required.\n'
    )
    process.exit(2)
  }
  execFileSync('npm', ['run', 'build'], { stdio: 'inherit' })
  execFileSync('npx', ['electron-builder', '--mac', '--arm64', '--publish', 'never'], {
    stdio: 'inherit'
  })
  process.exit(0)
}

process.stderr.write(`Unknown release mode: ${mode}\n`)
process.exit(2)

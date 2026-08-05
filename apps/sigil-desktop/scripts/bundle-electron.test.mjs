import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

import { describe, expect, it } from 'vitest'

const appDirectory = path.resolve(import.meta.dirname, '..')

describe('Electron main-process bundle', () => {
  it('keeps CommonJS updater packages external to the ESM output', () => {
    execFileSync(process.execPath, ['scripts/bundle-electron.mjs'], {
      cwd: appDirectory,
      stdio: 'pipe'
    })

    const bundle = fs.readFileSync(
      path.join(appDirectory, 'dist/electron-main.mjs'),
      'utf8'
    )

    expect(bundle).toContain('import("electron-updater")')
    expect(bundle).not.toMatch(
      /node_modules[\\/](?:electron-updater|graceful-fs|fs-extra)[\\/]/
    )
    expect(bundle).not.toContain('Dynamic require of "')
  })
})

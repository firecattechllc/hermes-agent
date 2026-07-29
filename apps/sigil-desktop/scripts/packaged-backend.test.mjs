import { execFileSync } from 'node:child_process'
import path from 'node:path'
import process from 'node:process'
import { describe, expect, it } from 'vitest'

const appDirectory = path.resolve(import.meta.dirname, '..')

describe('packaged Python backend release gate', () => {
  it('compiles, imports, and serves runtime_snapshot before packaging', () => {
    const output = execFileSync(
      process.execPath,
      ['scripts/test-packaged-backend.mjs'],
      {
        cwd: appDirectory,
        encoding: 'utf8'
      }
    )

    expect(output).toContain('PASS: py_compile')
    expect(output).toContain('PASS: imported packaged sigil module')
    expect(output).toContain(
      'PASS: runtime_snapshot bridge returned valid JSON with ok=true'
    )
  })
})

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import process from 'node:process'
import { describe, expect, it } from 'vitest'
import {
  CERTIFIED_MACOS_PYTHON,
  discoverPackagedPython
} from './packaged-python.mjs'

const appDirectory = path.resolve(import.meta.dirname, '..')

describe('packaged Python backend release gate', () => {
  it('does not require Homebrew Python on Linux CI', () => {
    const attempts = []
    const python = discoverPackagedPython({
      platform: 'linux',
      environment: {},
      execute(command, args) {
        attempts.push([command, args])
        return { status: command === 'python3' ? 0 : 1 }
      }
    })

    expect(python).toBe('python3')
    expect(attempts).toEqual([['python3', ['--version']]])
    expect(attempts.flat()).not.toContain(CERTIFIED_MACOS_PYTHON)
  })

  it('honors the packaged Python environment override', () => {
    const attempts = []
    const python = discoverPackagedPython({
      platform: 'linux',
      environment: {
        SIGIL_PACKAGED_PYTHON: '/ci/python'
      },
      execute(command, args) {
        attempts.push([command, args])
        return { status: command === '/ci/python' ? 0 : 1 }
      }
    })

    expect(python).toBe('/ci/python')
    expect(attempts).toEqual([['/ci/python', ['--version']]])
  })

  it('prefers the certified runtime for macOS packaging', () => {
    const attempts = []
    const python = discoverPackagedPython({
      platform: 'darwin',
      environment: {
        SIGIL_PACKAGED_PYTHON: '/fallback/python'
      },
      execute(command, args) {
        attempts.push([command, args])
        return { status: 0 }
      }
    })

    expect(python).toBe(CERTIFIED_MACOS_PYTHON)
    expect(attempts).toEqual([[CERTIFIED_MACOS_PYTHON, ['--version']]])
  })

  it('fails clearly when no candidate can execute --version', () => {
    expect(() =>
      discoverPackagedPython({
        platform: 'linux',
        environment: {},
        execute() {
          return { status: null }
        }
      })
    ).toThrow(
      'No usable Python interpreter found (attempted: python3, python). ' +
      'Set SIGIL_PACKAGED_PYTHON or PYTHON to an interpreter that successfully runs --version.'
    )
  })

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

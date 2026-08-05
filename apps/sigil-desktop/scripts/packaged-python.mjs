import { spawnSync } from 'node:child_process'

export const CERTIFIED_MACOS_PYTHON =
  '/opt/homebrew/opt/python@3.11/bin/python3.11'

export function packagedPythonCandidates({
  platform = process.platform,
  environment = process.env
} = {}) {
  const candidates = []

  if (platform === 'darwin') {
    candidates.push({
      command: CERTIFIED_MACOS_PYTHON,
      label: 'certified macOS Python 3.11'
    })
  }

  if (environment.SIGIL_PACKAGED_PYTHON) {
    candidates.push({
      command: environment.SIGIL_PACKAGED_PYTHON,
      label: 'SIGIL_PACKAGED_PYTHON'
    })
  }
  if (environment.PYTHON) {
    candidates.push({ command: environment.PYTHON, label: 'PYTHON' })
  }

  candidates.push(
    { command: 'python3', label: 'python3' },
    { command: 'python', label: 'python' }
  )

  return candidates.filter(
    (candidate, index, allCandidates) =>
      allCandidates.findIndex(
        otherCandidate => otherCandidate.command === candidate.command
      ) === index
  )
}

export function discoverPackagedPython({
  platform = process.platform,
  environment = process.env,
  execute = spawnSync
} = {}) {
  const candidates = packagedPythonCandidates({ platform, environment })

  for (const candidate of candidates) {
    const result = execute(candidate.command, ['--version'], {
      encoding: 'utf8',
      env: environment
    })
    if (result.status === 0) {
      return candidate.command
    }
  }

  const attempted = candidates.map(candidate => candidate.label).join(', ')
  throw new Error(
    `No usable Python interpreter found (attempted: ${attempted}). ` +
    'Set SIGIL_PACKAGED_PYTHON or PYTHON to an interpreter that successfully runs --version.'
  )
}

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

const root = join(import.meta.dirname, '..')

describe('governed ecosystem visibility desktop wiring', () => {
  it('uses fixed read-only IPC channels for computer use, Hermes WebUI, and Paperclip', () => {
    const main = readFileSync(join(root, 'electron', 'main.ts'), 'utf8')
    const preload = readFileSync(join(root, 'electron', 'preload.ts'), 'utf8')
    const panel = readFileSync(join(root, 'src', 'mission-control', 'governed-visibility-panel.tsx'), 'utf8')

    expect(main).toContain("SIGIL_COMPUTER_USE_VISIBILITY_CHANNEL = 'sigil:get-computer-use-visibility'")
    expect(main).toContain("SIGIL_HERMES_WEBUI_STATUS_CHANNEL = 'sigil:get-hermes-webui-status'")
    expect(main).toContain("SIGIL_HERMES_WEBUI_DEEP_LINK_CHANNEL = 'sigil:get-hermes-webui-deep-link'")
    expect(main).toContain("SIGIL_PAPERCLIP_STATUS_CHANNEL = 'sigil:get-paperclip-status'")

    expect(preload).toContain('getComputerUseVisibility')
    expect(preload).toContain('getHermesWebUIStatus')
    expect(preload).toContain('getHermesWebUIDeepLink')
    expect(preload).toContain('getPaperclipStatus')

    expect(panel).toContain('Read-only evidence')
    expect(panel).toContain('Disabled by policy unless')
    expect(panel).not.toContain('submitOrder')
    expect(panel).not.toContain('APCA_API_SECRET_KEY')
  })

  it('exposes the Ecosystem navigation destination backed by the visibility panel', () => {
    const missionControl = readFileSync(join(root, 'src', 'mission-control', 'index.tsx'), 'utf8')

    expect(missionControl).toContain("ecosystem: 'Ecosystem'")
    expect(missionControl).toContain('GovernedVisibilityPanel')
  })
})

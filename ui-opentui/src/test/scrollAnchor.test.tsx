/**
 * Scroll-anchor sequencing (view/scrollAnchor.tsx). The jitter itself is a
 * paint-timing artifact (verified live), but the MECHANISM is pinnable
 * headlessly: `around(toggle)` must suspend the scrollbox's stickyScroll
 * BEFORE the toggle's layout pass (so the sticky re-pin never engages — no
 * transient bottom-pinned frame to flicker through), hold scrollTop across
 * the toggle, and restore stickyScroll afterwards with the pin-on-new-content
 * behavior intact when the view is still at the bottom.
 */
import type { ScrollBoxRenderable } from '@opentui/core'
import { createSignal, For, Show } from 'solid-js'
import { describe, expect, test } from 'vitest'

import { ScrollAnchorProvider, useScrollAnchor } from '../view/scrollAnchor.tsx'
import { renderProbe, type RenderProbe } from './lib/render.ts'

/** Sticky-bottom scrollbox + anchored toggleable block, instruments exposed. */
function mountHarness(probeRows = 30) {
  let sb: ScrollBoxRenderable | undefined
  let anchor: ((toggle: () => void) => void) | undefined
  const [scroll, setScroll] = createSignal<ScrollBoxRenderable>()
  const [expanded, setExpanded] = createSignal(false)
  const [extra, setExtra] = createSignal(0) // post-toggle "streamed" rows

  function Grab() {
    anchor = useScrollAnchor()
    return null
  }

  const node = () => (
    <scrollbox
      ref={(el: ScrollBoxRenderable) => {
        sb = el
        setScroll(el)
      }}
      style={{ height: 10, width: 40 }}
      stickyScroll
      stickyStart="bottom"
    >
      <ScrollAnchorProvider scroll={scroll}>
        <Grab />
        <For each={Array.from({ length: probeRows }, (_, i) => i)}>{i => <text>{`row-${i}`}</text>}</For>
        <Show when={expanded()}>
          <For each={Array.from({ length: 10 }, (_, i) => i)}>{i => <text>{`body-${i}`}</text>}</For>
        </Show>
        <For each={Array.from({ length: extra() }, (_, i) => i)}>{i => <text>{`new-${i}`}</text>}</For>
      </ScrollAnchorProvider>
    </scrollbox>
  )

  return {
    node,
    sb: () => sb!,
    anchor: () => anchor!,
    setExpanded,
    setExtra
  }
}

async function settle(probe: RenderProbe, passes = 3) {
  for (let i = 0; i < passes; i++) await probe.settle()
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

describe('scroll anchor — stickyScroll suspension (expand/collapse jitter fix)', () => {
  test('expanding while pinned at the bottom: sticky suspended, scrollTop never re-pins, restored after', async () => {
    const h = mountHarness()
    const probe = await renderProbe(h.node, { width: 40, height: 12 })
    try {
      await settle(probe)
      const sb = h.sb()
      expect(sb.stickyScroll).toBe(true)
      const pinned = sb.scrollTop
      expect(pinned).toBeGreaterThan(0) // 30 rows in a 10-high box → pinned at bottom

      h.anchor()(() => h.setExpanded(true))
      // suspended SYNCHRONOUSLY, before any layout pass could sticky-pin
      expect(sb.stickyScroll).toBe(false)

      // across the settle window the viewport must NEVER jump below the
      // anchored offset (the old code painted a bottom-pinned frame here)
      for (let i = 0; i < 5; i++) {
        await probe.settle()
        expect(sb.scrollTop).toBe(pinned)
      }

      await sleep(150) // > RESTORE_MS
      await settle(probe)
      expect(sb.stickyScroll).toBe(true) // restored
      expect(sb.scrollTop).toBe(pinned) // viewport still held
      // content DID grow below (the expansion is real)
      expect(sb.scrollHeight).toBeGreaterThan(30)

      // mid-content now → new content must NOT yank the view (manual-scroll
      // semantics, same end state as the old anchor)
      h.setExtra(3)
      await settle(probe)
      expect(sb.scrollTop).toBe(pinned)
    } finally {
      probe.destroy()
    }
  })

  test('collapsing at the bottom clamps to the new bottom and sticky pinning resumes for new content', async () => {
    const h = mountHarness()
    const probe = await renderProbe(h.node, { width: 40, height: 12 })
    try {
      await settle(probe)
      const sb = h.sb()
      // expand first (anchored), then scroll back to the bottom to re-pin
      h.anchor()(() => h.setExpanded(true))
      await sleep(150)
      await settle(probe)
      sb.scrollTo(sb.scrollHeight) // user returns to the bottom
      await settle(probe)
      const bottom = sb.scrollTop

      h.anchor()(() => h.setExpanded(false))
      expect(sb.stickyScroll).toBe(false)
      await settle(probe)
      // content shrank: the scrollbar clamps to the NEW max (no sticky needed)
      expect(sb.scrollTop).toBeLessThan(bottom)
      const clamped = sb.scrollTop

      await sleep(150)
      await settle(probe)
      expect(sb.stickyScroll).toBe(true)
      // still at the (new) bottom → sticky re-engages: appended rows pin
      h.setExtra(4)
      await settle(probe)
      expect(sb.scrollTop).toBeGreaterThan(clamped)
    } finally {
      probe.destroy()
    }
  })

  test('rapid double-toggle restores the ORIGINAL stickyScroll once (not our own false)', async () => {
    const h = mountHarness()
    const probe = await renderProbe(h.node, { width: 40, height: 12 })
    try {
      await settle(probe)
      const sb = h.sb()
      h.anchor()(() => h.setExpanded(true))
      expect(sb.stickyScroll).toBe(false)
      // second toggle lands INSIDE the suspension window
      h.anchor()(() => h.setExpanded(false))
      expect(sb.stickyScroll).toBe(false)
      await sleep(150)
      await settle(probe)
      expect(sb.stickyScroll).toBe(true) // the original value, not the suspended false
    } finally {
      probe.destroy()
    }
  })

  test('far from the bottom (scrolled up): toggle holds the viewport (the original anchor guarantee)', async () => {
    const h = mountHarness()
    const probe = await renderProbe(h.node, { width: 40, height: 12 })
    try {
      await settle(probe)
      const sb = h.sb()
      sb.scrollTo(5) // scroll away from the bottom
      await settle(probe)
      expect(sb.scrollTop).toBe(5)

      h.anchor()(() => h.setExpanded(true))
      for (let i = 0; i < 5; i++) {
        await probe.settle()
        expect(sb.scrollTop).toBe(5)
      }
      await sleep(150)
      await settle(probe)
      expect(sb.stickyScroll).toBe(true)
      expect(sb.scrollTop).toBe(5)
    } finally {
      probe.destroy()
    }
  })
})

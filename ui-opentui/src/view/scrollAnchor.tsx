/**
 * Scroll anchoring for collapse/expand toggles (item #4). The transcript
 * <scrollbox> has stickyScroll+stickyStart="bottom": on a content-height change
 * it re-pins to the bottom whenever the user hasn't manually scrolled away
 * (@opentui/core ScrollBox `recalculateBarProps`: `if (stickyStart &&
 * !_hasManualScroll) applyStickyStart`). So expanding a tool/thinking block
 * while at the bottom yanks the viewport to the NEW bottom — scrolling the
 * header you just clicked up off-screen.
 *
 * Fix: SUSPEND stickyScroll for the duration of the toggle (it's a runtime
 * get/set property on ScrollBoxRenderable, recomputing its state on set). With
 * sticky off, the toggle's layout passes leave scrollTop untouched: the clicked
 * element's document position is unchanged (content grows/shrinks BELOW it), so
 * the header stays on the same screen row and the expansion is simply revealed
 * beneath it; a collapse past the new bottom clamps naturally (ScrollBar's
 * `scrollSize` setter re-clamps `scrollPosition`). stickyScroll is restored a
 * few frames later, once the content height has settled; the setter then
 * recomputes the manual-scroll state from the ACTUAL position — still at the
 * bottom → keeps pinning for new content; mid-content → behaves like a manual
 * scroll-away until the user returns to the bottom (same end state as before).
 *
 * Why not hold scrollTop after the fact (the previous approach): re-asserting
 * the saved offset over 4×16ms timers FIGHTS the sticky re-pin frame-by-frame —
 * the pin paints (viewport jumps down), the re-assert paints (jumps back up) —
 * a visible jitter on rows near the bottom, where the pin actually engages
 * (reproduced live: a transient fully-bottom-pinned frame between two anchored
 * ones on every expand). Suppressing the pin BEFORE the layout pass means no
 * correcting after paint, so there is nothing left to flicker.
 */
import { type Accessor, createContext, type JSX, onCleanup, useContext } from 'solid-js'

import type { ScrollBoxRenderable } from '@opentui/core'

type AnchorFn = (toggle: () => void) => void

const Ctx = createContext<AnchorFn>()

/**
 * How long stickyScroll stays suspended after a toggle. The content-height
 * change (and any text-rewrap settling) lands over the next render pass or
 * two; ~3 frames at the ~30fps render loop covers it (matches the old hold
 * window). Restoring "too late" is harmless: the setter recomputes state from
 * the actual position, and a mid-stream toggle leaves the view un-pinned
 * either way (you're no longer at the bottom after the content grew).
 */
const RESTORE_MS = 100

export function ScrollAnchorProvider(props: {
  scroll: Accessor<ScrollBoxRenderable | undefined>
  children: JSX.Element
}) {
  let timer: ReturnType<typeof setTimeout> | undefined
  let saved = true // the scrollbox's own stickyScroll while suspended
  const around: AnchorFn = toggle => {
    const sb = props.scroll()
    if (!sb) {
      toggle()
      return
    }
    // Rapid re-toggle while still suspended: keep the ORIGINAL saved value
    // (reading sb.stickyScroll now would capture our own `false`).
    if (timer === undefined) saved = sb.stickyScroll
    else clearTimeout(timer)
    sb.stickyScroll = false
    toggle()
    timer = setTimeout(() => {
      timer = undefined
      try {
        sb.stickyScroll = saved
      } catch {
        /* renderable torn down */
      }
    }, RESTORE_MS)
  }
  onCleanup(() => clearTimeout(timer))
  return <Ctx.Provider value={around}>{props.children}</Ctx.Provider>
}

/** Wrap a collapse/expand toggle so the viewport stays put (no-op outside a provider). */
export function useScrollAnchor(): AnchorFn {
  return useContext(Ctx) ?? (toggle => toggle())
}

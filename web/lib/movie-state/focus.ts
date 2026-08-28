/**
 * Where focus goes after a movie-state write settles.
 *
 * A mutation can move a row, remove it, or swap the control that started it,
 * and a write that leaves focus on `<body>` loses a keyboard or screen-reader
 * viewer's place entirely. Every surface answers that the same way — the
 * control the reader used, then the row it belongs to, then the collection the
 * row is in — so the walk lives here rather than being re-derived per route.
 *
 * Two attempts, in that order, because the two surfaces need different timing.
 * A control left focusable through the write (`aria-disabled` rather than
 * `disabled`) can take focus immediately, and doing it synchronously keeps the
 * viewer from being dropped for a frame. A control that was genuinely disabled
 * cannot take focus until React has re-rendered it, so the walk is repeated
 * after the next frame. Only the first target is tried synchronously: falling
 * through to the row while the control is still disabled would move the reader
 * somewhere they did not ask to be.
 */

export type FocusTarget = HTMLElement | string | null | undefined;

function resolve(target: FocusTarget): HTMLElement | null {
  if (!target) return null;
  if (typeof target !== "string") {
    // A control that has been unmounted is no longer worth focusing.
    return target.isConnected ? target : null;
  }
  return document.getElementById(target);
}

function tryFocus(target: FocusTarget, options: FocusOptions): boolean {
  const element = resolve(target);
  if (!element) return false;
  element.focus(options);
  return document.activeElement === element;
}

function walk(chain: FocusTarget[], options: FocusOptions): void {
  if (typeof document === "undefined") return;
  if (tryFocus(chain[0], options)) return;
  if (typeof window === "undefined" || !window.requestAnimationFrame) return;
  window.requestAnimationFrame(() => {
    for (const target of chain) {
      if (tryFocus(target, options)) return;
    }
  });
}

/** Focuses the first target that will take focus, preferring the control. */
export function restoreFocus(...chain: FocusTarget[]): void {
  walk(chain, {});
}

/**
 * The same walk for a caller that has already decided where the page should be
 * scrolled to.
 *
 * `focus()` scrolls its element into view by default, which is the right
 * default for a control the viewer is already looking at and the wrong one
 * directly after a `scrollIntoView`: the browser resolves the focus scroll
 * immediately, against the position the smooth one has not reached yet, and
 * the page lands somewhere neither the caller nor the viewer chose.
 */
export function restoreFocusInPlace(...chain: FocusTarget[]): void {
  walk(chain, { preventScroll: true });
}

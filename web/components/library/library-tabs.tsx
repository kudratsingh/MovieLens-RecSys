"use client";

import type { LibraryCounts } from "@/lib/api";
import { LIBRARY_TABS, type LibraryTab } from "@/lib/library/url-state";
import "./library-tabs.css";

/*
 * `history` is the tab's identity everywhere it is addressable — the URL value,
 * the API value, the `LibraryTab` type. Only what the reader sees is `Seen`,
 * because "History" named a chronological receipt and the tab is a place to
 * look back at what you have watched. One rename in a label map, and every
 * string derived from it follows.
 */
const LABELS: Record<LibraryTab, string> = {
  rated: "Rated",
  watchlist: "Watchlist",
  history: "Seen",
};

export function libraryTabLabel(tab: LibraryTab): string {
  return LABELS[tab];
}

export function libraryTabId(tab: LibraryTab): string {
  return `library-tab-${tab}`;
}

export function libraryPanelId(tab: LibraryTab): string {
  return `library-panel-${tab}`;
}

/**
 * The three collections as a keyboard-operable tablist.
 *
 * Counts come from whichever Library read answered most recently — the API
 * returns all three on every response — so they are omitted rather than
 * guessed at before the first read lands. A tab showing `0` when nothing has
 * been read yet would be an invented number.
 */
export function LibraryTabs({
  active,
  counts,
  onSelect,
}: {
  active: LibraryTab;
  counts: LibraryCounts | null;
  onSelect: (tab: LibraryTab) => void;
}) {
  function move(event: React.KeyboardEvent<HTMLDivElement>) {
    const index = LIBRARY_TABS.indexOf(active);
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % LIBRARY_TABS.length;
    else if (event.key === "ArrowLeft")
      next = (index - 1 + LIBRARY_TABS.length) % LIBRARY_TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = LIBRARY_TABS.length - 1;
    else return;

    event.preventDefault();
    const tab = LIBRARY_TABS[next];
    onSelect(tab);
    // The roving tabindex only pays off if focus follows the selection.
    document.getElementById(libraryTabId(tab))?.focus();
  }

  return (
    <div
      aria-label="Library collections"
      className="library-tabs"
      onKeyDown={move}
      role="tablist"
    >
      {LIBRARY_TABS.map((tab) => (
        <button
          // Only the selected panel is mounted, so only the selected tab can
          // legitimately point at one.
          aria-controls={active === tab ? libraryPanelId(tab) : undefined}
          aria-selected={active === tab}
          className="library-tab"
          id={libraryTabId(tab)}
          key={tab}
          onClick={() => onSelect(tab)}
          role="tab"
          tabIndex={active === tab ? 0 : -1}
          type="button"
        >
          {LABELS[tab]}
          {counts ? <span className="library-tab-count">{counts[tab]}</span> : null}
        </button>
      ))}
    </div>
  );
}

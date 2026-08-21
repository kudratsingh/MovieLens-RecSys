import Link from "next/link";

import { Icon } from "@/components/ui/icons";
import "./quick-picks-entry.css";

/**
 * The way into Quick Picks, deliberately not a fourth navigation slot.
 *
 * The route contract is explicit that the shell carries three primary routes
 * and that Quick Picks stays a Discover entry point rather than claiming
 * permanent space — a rapid classification queue is something a viewer chooses
 * to enter, not a place they are meant to live. So it sits where the intent
 * actually arises: beside the ranked rail when nothing in it appeals, and
 * inside the empty state when there is no ranking to read at all.
 *
 * It is a plain link with a labelled action and a one-line reason, which keeps
 * it keyboard reachable and thumb-sized without a gesture or a hover.
 */
export function QuickPicksEntry({
  href,
  note = "One movie at a time. Watchlist, watched, or not for me — and every choice is undoable.",
}: {
  href: string;
  note?: string;
}) {
  return (
    <p className="quick-picks-entry">
      <Link className="button-secondary quick-picks-entry-action" href={href}>
        <Icon name="spark" />
        Rate a few in Quick picks
      </Link>
      <span className="quick-picks-entry-note">{note}</span>
    </p>
  );
}

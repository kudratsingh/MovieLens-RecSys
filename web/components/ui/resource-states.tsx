import { Icon } from "@/components/ui/icons";
import type { ResourceResult } from "@/lib/movie-types";
import "./resource-states.css";

/**
 * The shared empty and error blocks.
 *
 * Both share one shell: a mark, a title, a sentence that says what happened,
 * and a single row of actions that spans the block. The actions live in their
 * own row rather than flowing into the grid because a state can offer more
 * than one way out — Discover's empty state offers Browse *and* Quick Picks —
 * and auto-placing them dropped the first action into the icon's narrow column
 * and the second beside it, which read as the wrong order on a phone.
 */

export function ResourceBlock<T>({
  result,
  children,
  label,
  onRetry,
}: {
  result: ResourceResult<T>;
  children: (data: T) => React.ReactNode;
  label: string;
  onRetry?: () => void;
}) {
  if (result.status === "error") {
    return <ErrorState label={label} message={result.message} onRetry={onRetry} />;
  }
  return children(result.data);
}

export function ErrorState({
  label,
  message,
  onRetry,
}: {
  label: string;
  message: string;
  /**
   * Offered only when the caller can actually re-run the thing that failed.
   * The button used to render unconditionally with no handler at all, which
   * is worse than no button: it invites the one action the viewer has, and
   * then does nothing with it.
   */
  onRetry?: () => void;
}) {
  return (
    <section aria-label={`${label} error`} className="resource-state resource-error" role="alert">
      <span className="resource-state-icon" aria-hidden="true">
        !
      </span>
      <div>
        <p className="resource-state-title">{label} is taking a night off</p>
        <p>{message}</p>
      </div>
      <div className="resource-state-actions">
        {onRetry ? (
          <button className="button-secondary" onClick={onRetry} type="button">
            Try again
          </button>
        ) : null}
      </div>
    </section>
  );
}

export function EmptyState({
  title,
  message,
  action,
}: {
  title: string;
  message: string;
  action?: React.ReactNode;
}) {
  return (
    <section className="resource-state resource-empty">
      <span className="resource-state-icon" aria-hidden="true">
        <Icon name="spark" />
      </span>
      <div>
        <p className="resource-state-title">{title}</p>
        <p>{message}</p>
      </div>
      <div className="resource-state-actions">{action}</div>
    </section>
  );
}

export function PosterSkeleton({ count = 5 }: { count?: number }) {
  return (
    <div aria-label="Loading movies" aria-live="polite" className="skeleton-rail">
      <span className="visually-hidden">Loading movies</span>
      {Array.from({ length: count }, (_, index) => (
        <div aria-hidden="true" className="skeleton-card" key={index}>
          <div className="skeleton-poster" />
          <div className="skeleton-line skeleton-line-strong" />
          <div className="skeleton-line" />
        </div>
      ))}
    </div>
  );
}

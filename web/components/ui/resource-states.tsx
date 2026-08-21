import { Icon } from "@/components/ui/icons";
import type { ResourceResult } from "@/lib/movie-types";
import "./resource-states.css";

export function ResourceBlock<T>({
  result,
  children,
  label,
}: {
  result: ResourceResult<T>;
  children: (data: T) => React.ReactNode;
  label: string;
}) {
  if (result.status === "error") {
    return <ErrorState label={label} message={result.message} />;
  }
  return children(result.data);
}

export function ErrorState({ label, message }: { label: string; message: string }) {
  return (
    <section aria-label={`${label} error`} className="resource-state resource-error" role="alert">
      <span className="resource-state-icon" aria-hidden="true">
        !
      </span>
      <div>
        <p className="resource-state-title">{label} is taking a night off</p>
        <p>{message}</p>
      </div>
      <button className="button-secondary" type="button">
        Try again
      </button>
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
      {action}
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

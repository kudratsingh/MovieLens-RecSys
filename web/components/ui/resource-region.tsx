import Link from "next/link";

import { Icon } from "@/components/ui/icons";
import { EmptyState } from "@/components/ui/resource-states";
import { RESOURCE_LABELS } from "@/lib/resources/definitions";
import type {
  ResourceFailure,
  ResourceState,
} from "@/lib/resources/state";
import "./resource-states.css";

/**
 * Renders one region's state.
 *
 * Regions are the unit of failure. A route composes several of them so a dead
 * recommendation rail leaves the catalog and Library beside it untouched, and
 * so technical evidence can fail without standing between the viewer and the
 * first movie.
 *
 * This module has no `"use client"` directive on purpose: a server-rendered
 * region works without shipping JavaScript, and a client region can still pass
 * `onRetry` when it owns a refetch.
 */

const PROBLEM_HEADLINE: Record<ResourceFailure["status"], string> = {
  "auth-expired": "Your session expired",
  forbidden: "This session cannot open",
  "not-found": "Not found",
  "upstream-error": "Could not be loaded",
};

const REASON_DETAIL: Record<ResourceFailure["reason"], string> = {
  "session-expired": "Sign in again to load it.",
  forbidden:
    "The signed-in actor's role or tenant does not cover this persona's data.",
  "not-found": "There is no such record for the selected persona.",
  "bad-request": "The recommendation API rejected the request as malformed.",
  "rate-limited": "Too many requests reached the recommendation API. Try again shortly.",
  server: "The recommendation API returned an error.",
  timeout: "The recommendation API did not answer in time.",
  network: "The recommendation API could not be reached.",
  "invalid-json": "The recommendation API returned a response that was not JSON.",
  "invalid-payload": "The response did not match the published API contract.",
};

/**
 * Exposed so a region that writes its own headline — a failed mutation, say —
 * can still explain the cause in the same words a failed read would use.
 */
export function resourceReasonDetail(failure: ResourceFailure): string {
  return REASON_DETAIL[failure.reason];
}

export function resourceProblemHeadline(
  failure: ResourceFailure,
  label: string,
): string {
  return failure.status === "auth-expired"
    ? PROBLEM_HEADLINE["auth-expired"]
    : `${label} ${PROBLEM_HEADLINE[failure.status].toLowerCase()}`;
}

export function ResourceLoading({
  label,
  lines = 3,
}: {
  label: string;
  lines?: number;
}) {
  return (
    <div aria-live="polite" className="resource-loading" role="status">
      <span className="visually-hidden">Loading {label}</span>
      {Array.from({ length: lines }, (_, index) => (
        <div
          aria-hidden="true"
          className={`skeleton-line${index === 0 ? " skeleton-line-strong" : ""}`}
          key={index}
        />
      ))}
    </div>
  );
}

export function ResourceRetrying({ label }: { label: string }) {
  return (
    <div aria-live="polite" className="resource-state resource-retrying" role="status">
      <span className="resource-state-icon" aria-hidden="true">
        <Icon name="arrow" />
      </span>
      <div>
        <p className="resource-state-title">Retrying {label}</p>
        <p>The previous attempt failed. Asking the recommendation API again.</p>
      </div>
    </div>
  );
}

export function ResourceProblem({
  failure,
  label,
  onRetry,
  reauthenticateHref = "/",
}: {
  failure: ResourceFailure;
  label: string;
  onRetry?: () => void;
  reauthenticateHref?: string;
}) {
  const headline = resourceProblemHeadline(failure, label);
  return (
    <section
      aria-label={`${label} ${failure.status}`}
      className={`resource-state resource-error resource-${failure.status}`}
      role="alert"
    >
      <span className="resource-state-icon" aria-hidden="true">
        {failure.status === "not-found" ? <Icon name="search" /> : "!"}
      </span>
      <div>
        <p className="resource-state-title">{headline}</p>
        <p>{REASON_DETAIL[failure.reason]}</p>
      </div>
      <div className="resource-state-actions">
        {failure.status === "auth-expired" ? (
          <Link className="button-primary" href={reauthenticateHref}>
            Sign in again
          </Link>
        ) : null}
        {failure.retryable && onRetry ? (
          <button className="button-secondary" onClick={onRetry} type="button">
            Try again
          </button>
        ) : null}
      </div>
      <p className="resource-state-meta">Request {failure.requestId}</p>
    </section>
  );
}

export function ResourceRegion<T>({
  state,
  children,
  label,
  onRetry,
  reauthenticateHref,
  loading,
  empty,
}: {
  state: ResourceState<T>;
  children: (data: T) => React.ReactNode;
  /** Defaults to the registry label for the resource. */
  label?: string;
  onRetry?: () => void;
  reauthenticateHref?: string;
  /** Route-specific skeleton, e.g. a poster rail rather than text lines. */
  loading?: React.ReactNode;
  empty?: React.ReactNode;
}) {
  const regionLabel = label ?? RESOURCE_LABELS[state.resource];

  switch (state.status) {
    case "loading":
      return <>{loading ?? <ResourceLoading label={regionLabel} />}</>;
    case "retry":
      return <ResourceRetrying label={regionLabel} />;
    case "ready":
      return <>{children(state.data)}</>;
    case "empty":
      return (
        <>
          {empty ?? (
            <EmptyState
              message="There is nothing recorded here for the selected persona yet."
              title={`No ${regionLabel.toLowerCase()} yet`}
            />
          )}
        </>
      );
    default:
      return (
        <ResourceProblem
          failure={state}
          label={regionLabel}
          onRetry={onRetry}
          reauthenticateHref={reauthenticateHref}
        />
      );
  }
}

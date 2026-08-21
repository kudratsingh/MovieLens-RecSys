"use client";

import { useCallback, useState } from "react";

import { ResourceRegion } from "@/components/ui/resource-region";
import type {
  OnlineUserFeatures,
  RecommendationAuditItem,
  RecommendationAuditResponse,
} from "@/lib/api";
import { AUDITS, FEATURES } from "@/lib/resources/definitions";
import {
  featureRows,
  formatRankScore,
  rankScoreCaveat,
} from "@/lib/discover/evidence";
import { readBffResource } from "@/lib/resources/browser";
import { loadingState, type ResourceState } from "@/lib/resources/state";

export type PreloadedTechnicalEvidence = {
  audits: ResourceState<RecommendationAuditResponse>;
  features: ResourceState<OnlineUserFeatures>;
};

/**
 * The second — and last — step of the technical disclosure.
 *
 * Audits and online features are the heaviest reads on the route and nobody is
 * waiting on them to decide what to watch, so they are not part of the server
 * render. They load when a reader asks, from the same-origin BFF, through the
 * same resource-state model as everything else. A failure here is contained:
 * the movie, its reason, and its serving policy are already on screen.
 */
export function TechnicalEvidence({
  userId,
  movieId,
  requestId,
  scoreScale = null,
  preloaded,
}: {
  userId: number;
  movieId: number;
  /** Correlation ID of the recommendation read, used to pick its audit row. */
  requestId: string | null;
  /** The scale the response said its item scores are on, when it named one. */
  scoreScale?: string | null;
  /** Supplied by the recorded harness and by tests; otherwise fetched. */
  preloaded?: PreloadedTechnicalEvidence | null;
}) {
  const [open, setOpen] = useState(false);
  const [audits, setAudits] = useState<ResourceState<RecommendationAuditResponse> | null>(
    preloaded?.audits ?? null,
  );
  const [features, setFeatures] = useState<ResourceState<OnlineUserFeatures> | null>(
    preloaded?.features ?? null,
  );

  const loadAudits = useCallback(async () => {
    setAudits(loadingState("audits"));
    setAudits(
      await readBffResource(AUDITS, `/api/users/${userId}/audits?limit=5`, { requestId }),
    );
  }, [requestId, userId]);

  const loadFeatures = useCallback(async () => {
    setFeatures(loadingState("features"));
    setFeatures(
      await readBffResource(FEATURES, `/api/users/${userId}/features`, { requestId }),
    );
  }, [requestId, userId]);

  function reveal() {
    setOpen(true);
    if (preloaded) return;
    void loadAudits();
    void loadFeatures();
  }

  if (!open) {
    return (
      <div className="evidence-advanced">
        <button className="button-secondary" onClick={reveal} type="button">
          Show prediction audit
        </button>
        <p className="evidence-hint">
          Loads the recorded audit row and the online feature values for this persona.
        </p>
      </div>
    );
  }

  return (
    <div className="evidence-advanced" data-testid="technical-evidence">
      <section aria-labelledby="audit-heading">
        <h3 className="evidence-heading" id="audit-heading">
          Prediction audit
        </h3>
        <ResourceRegion
          onRetry={() => void loadAudits()}
          state={audits ?? loadingState("audits")}
        >
          {(data) => (
            <AuditDetail
              data={data}
              movieId={movieId}
              requestId={requestId}
              scoreScale={scoreScale}
            />
          )}
        </ResourceRegion>
      </section>
      <section aria-labelledby="features-heading">
        <h3 className="evidence-heading" id="features-heading">
          Online features
        </h3>
        <ResourceRegion
          onRetry={() => void loadFeatures()}
          state={features ?? loadingState("features")}
        >
          {(data) => <FeatureDetail data={data} />}
        </ResourceRegion>
      </section>
    </div>
  );
}

/**
 * The ID that ties an audit row to the response on screen. Bundle 6 adds an
 * explicit `correlation_id`; until then the audit's own request ID is the only
 * candidate, so both are accepted and the explicit one wins.
 */
function auditCorrelationId(audit: RecommendationAuditItem): string {
  const explicit = (audit as { correlation_id?: unknown }).correlation_id;
  return typeof explicit === "string" && explicit ? explicit : audit.request_id;
}

function matchesResponse(audit: RecommendationAuditItem, requestId: string): boolean {
  return auditCorrelationId(audit) === requestId || audit.request_id === requestId;
}

function selectAudit(
  data: RecommendationAuditResponse,
  requestId: string | null,
): RecommendationAuditItem | null {
  // Prefer the row for the exact response on screen; otherwise the newest one,
  // clearly labelled as a different request rather than silently substituted.
  const matched = requestId
    ? data.items.find((audit) => matchesResponse(audit, requestId))
    : undefined;
  return matched ?? data.items[0] ?? null;
}

function AuditDetail({
  data,
  movieId,
  requestId,
  scoreScale,
}: {
  data: RecommendationAuditResponse;
  movieId: number;
  requestId: string | null;
  scoreScale: string | null;
}) {
  const audit = selectAudit(data, requestId);
  if (!audit) {
    return <p className="evidence-hint">No prediction audit was recorded for this persona.</p>;
  }
  const prediction = audit.predictions.find((item) => item.movie_id === movieId) ?? null;
  const isThisResponse = requestId !== null && matchesResponse(audit, requestId);

  return (
    <div className="evidence-details">
      {!isThisResponse ? (
        <p className="evidence-hint">
          Showing the most recent recorded audit for this persona, which is not the
          request that produced the list on screen.
        </p>
      ) : null}
      <dl>
        <div><dt>Policy</dt><dd>{audit.policy}</dd></div>
        <div><dt>Audit reason</dt><dd>{audit.reason}</dd></div>
        <div><dt>Fallback reason</dt><dd>{audit.fallback_reason ?? "none recorded"}</dd></div>
        <div>
          <dt>Positive signals</dt>
          <dd>{audit.positive_signal_count} counted, {audit.excluded_count} excluded</dd>
        </div>
        <div><dt>Filter policy</dt><dd>{audit.filter_policy}</dd></div>
        <div>
          <dt>Candidate sources</dt>
          <dd>{candidateSources(audit.candidate_sources)}</dd>
        </div>
        <div><dt>Candidates</dt><dd>{audit.candidate_version}</dd></div>
        <div><dt>Ranker</dt><dd>{audit.ranker_version}</dd></div>
        <div><dt>Features</dt><dd>{audit.feature_version}</dd></div>
        <div>
          <dt>Feature event time</dt>
          <dd>{audit.feature_event_time ?? "no features read"}</dd>
        </div>
        <div>
          <dt>Input state</dt>
          <dd>revision {audit.input_state_revision} · {audit.input_state_hash}</dd>
        </div>
        <div><dt>Request</dt><dd>{audit.request_id}</dd></div>
        <div><dt>Recorded</dt><dd>{audit.created_at}</dd></div>
        <div>
          <dt>Latency</dt>
          <dd>
            {audit.latency_ms.toFixed(1)} ms total · {audit.candidate_latency_ms.toFixed(1)} candidates ·{" "}
            {audit.feature_latency_ms.toFixed(1)} features · {audit.ranker_latency_ms.toFixed(1)} ranker
          </dd>
        </div>
      </dl>
      {prediction ? (
        <>
          <dl>
            <div>
              <dt>Rank score</dt>
              <dd>{formatRankScore(prediction.score)}</dd>
            </div>
            <div>
              <dt>Retrieved by</dt>
              <dd>{prediction.candidate_source}</dd>
            </div>
            {featureRows(prediction.features).map((row) => (
              <div key={row.term}>
                <dt>{row.term}</dt>
                <dd>{row.detail}</dd>
              </div>
            ))}
          </dl>
          <p className="evidence-caveat">{rankScoreCaveat(scoreScale)}</p>
        </>
      ) : (
        <p className="evidence-hint">
          This audit recorded no per-movie prediction for the selected title.
        </p>
      )}
    </div>
  );
}

/** `{ "item-item-cosine": 500 }` reads as `item-item-cosine (500)`. */
function candidateSources(sources: Record<string, number>): string {
  const entries = Object.entries(sources);
  if (!entries.length) return "none recorded";
  return entries
    .sort(([, left], [, right]) => right - left)
    .map(([name, count]) => `${name} (${count})`)
    .join(" · ");
}

function FeatureDetail({ data }: { data: OnlineUserFeatures }) {
  const rows: { term: string; detail: string }[] = [
    { term: "Source", detail: data.source },
    { term: "Feature timestamp", detail: data.feature_timestamp },
  ];
  if (data.user_interaction_count !== null) {
    rows.push({ term: "Interactions", detail: String(data.user_interaction_count) });
  }
  if (data.user_days_active !== null) {
    rows.push({ term: "Days active", detail: String(data.user_days_active) });
  }
  if (data.user_days_since_last_interaction !== null) {
    rows.push({
      term: "Days since last interaction",
      detail: String(data.user_days_since_last_interaction),
    });
  }

  return (
    <div className="evidence-details">
      <dl>
        {rows.map((row) => (
          <div key={row.term}>
            <dt>{row.term}</dt>
            <dd>{row.detail}</dd>
          </div>
        ))}
      </dl>
      <p className="evidence-caveat">
        These values come from the materialized feature snapshot. Feedback recorded a
        moment ago is not in them until the next materialization run.
      </p>
    </div>
  );
}

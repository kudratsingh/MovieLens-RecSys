"use client";

import {
  TechnicalEvidence,
  type PreloadedTechnicalEvidence,
} from "@/components/discover/technical-evidence";
import { Drawer } from "@/components/ui/drawer";
import type { RecommendationItem, RecommendationResponse } from "@/lib/api";
import { rankScoreCaveat, recommendationEvidence } from "@/lib/discover/evidence";
import { displayTitle } from "@/lib/discover/movie-card";

/**
 * `Why this?` — the first of at most two deliberate actions to reach evidence.
 *
 * Everything in the drawer comes from the recommendation response that is
 * already on screen, so opening it costs nothing and cannot fail. The heavier
 * audit and feature reads sit one further click in, inside `TechnicalEvidence`.
 *
 * What is deliberately absent matters as much as what is here. There is no
 * match percentage, and no "because you liked" line: the response names no
 * contributing title, and star magnitude is not a learned input, so either
 * would be an invention. The API's own `reason` string is shown verbatim.
 */
export function WhyThis({
  response,
  item,
  requestId,
  userId,
  preloadedEvidence,
}: {
  response: RecommendationResponse;
  item: RecommendationItem;
  requestId: string | null;
  userId: number;
  preloadedEvidence?: PreloadedTechnicalEvidence | null;
}) {
  const evidence = recommendationEvidence(response, item, requestId);
  const title = displayTitle(item.title, item.release_year);

  return (
    <Drawer
      buttonLabel="Why this?"
      eyebrow="Model evidence"
      title={`Why ${title}?`}
    >
      <div className="evidence-details">
        <p className="evidence-reason">
          {evidence.reason ?? "The recommendation API sent no item-level reason for this title."}
        </p>
        <p className="evidence-policy">
          <strong>{evidence.policy.label}.</strong> {evidence.policy.summary}
        </p>
        {evidence.policy.note ? (
          <p className="evidence-hint">{evidence.policy.note}</p>
        ) : null}
        <dl>
          {evidence.rows.map((row) => (
            <div key={row.term}>
              <dt>{row.term}</dt>
              <dd>{row.detail}</dd>
            </div>
          ))}
        </dl>
        <p className="evidence-caveat">{rankScoreCaveat(evidence.policy.scoreScale)}</p>
        <TechnicalEvidence
          movieId={item.movie_id}
          scoreScale={evidence.policy.scoreScale}
          preloaded={preloadedEvidence}
          requestId={requestId}
          userId={userId}
        />
      </div>
    </Drawer>
  );
}

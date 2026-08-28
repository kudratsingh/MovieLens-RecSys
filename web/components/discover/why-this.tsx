"use client";

import {
  TechnicalEvidence,
  type PreloadedTechnicalEvidence,
} from "@/components/discover/technical-evidence";
import { Drawer } from "@/components/ui/drawer";
import type { RecommendationItem, RecommendationResponse } from "@/lib/api";
import { rankScoreCaveat, recommendationEvidence } from "@/lib/discover/evidence";
import { displayTitle } from "@/lib/movie-types";
import { plainServingExplanation } from "@/lib/discover/policy";

/**
 * `Why this?` — the first of at most two deliberate actions to reach evidence.
 *
 * Everything above the `Model evidence` heading comes from the recommendation
 * response that is already on screen, so opening it costs nothing and cannot
 * fail. The heavier audit and feature reads sit one further click in, inside
 * `TechnicalEvidence`.
 *
 * The reading order is the point of this component. The evidence itself was
 * always right — the policy string, the model versions, the counts, the request
 * id — and the route contract asks for exactly those. But the product's primary
 * viewer is someone choosing a film, and for them "why this?" was being answered
 * with `item-item-cosine+lightgbm`. So the drawer opens with one plain sentence
 * built only from values the response carries, and the tables follow it
 * unchanged, under a heading, in the same number of actions as before.
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
        <p className="evidence-plain">{plainServingExplanation(response)}</p>
        <p className="evidence-reason">
          {evidence.reason ?? "The recommendation API sent no item-level reason for this title."}
        </p>
        <h3 className="evidence-heading">Model evidence</h3>
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

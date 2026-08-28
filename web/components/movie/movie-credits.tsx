"use client";

/**
 * Directors and top-billed cast, from the enriched detail record.
 *
 * The cast row scrolls horizontally at narrow widths rather than wrapping into
 * a block: six people are reference material next to the decision this route
 * exists for, and a wrapped grid of six portraits would push the rating panel
 * off a 390px screen. It follows the rail's rule for a scroll container — a
 * named, focusable region — because a scrollable area that no key can reach is
 * a section a keyboard viewer simply cannot read.
 *
 * A missing portrait gets a monogram rather than a silhouette, matching how a
 * missing poster is handled: the gap is named by the thing that is missing, not
 * papered over with a generic placeholder.
 */

import Image from "next/image";
import { useState } from "react";

import { personInitials, type MovieCastMember } from "@/lib/movie-details";
// Styled by `movie-detail-view.css`, which the detail view imports: this is a
// section of that route rather than a free-standing primitive, and splitting one
// page's layout across two stylesheets buys nothing until a second caller exists.

export function MovieCredits({
  directors,
  cast,
  headingId,
}: {
  directors: readonly string[];
  cast: readonly MovieCastMember[];
  headingId: string;
}) {
  if (directors.length === 0 && cast.length === 0) return null;

  return (
    <section aria-labelledby={headingId} className="movie-credits">
      <h2 className="rule-label" id={headingId}>
        Cast and crew
      </h2>

      {directors.length > 0 ? (
        <p className="movie-credits-directors">
          <span className="movie-credits-role">
            {directors.length === 1 ? "Director" : "Directors"}
          </span>
          {directors.join(", ")}
        </p>
      ) : null}

      {cast.length > 0 ? (
        <ul aria-label="Top-billed cast" className="movie-cast-row" tabIndex={0}>
          {cast.map((member, index) => (
            <li className="movie-cast-member" key={`${member.name}-${index}`}>
              <CastPortrait member={member} />
              <span className="movie-cast-name">{member.name}</span>
              {member.character ? (
                <span className="movie-cast-character">{member.character}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/**
 * The portrait, with the same failure rule the posters use: remember which
 * source failed rather than that something failed, so a monogram never ends up
 * over artwork that loads perfectly well.
 */
function CastPortrait({ member }: { member: MovieCastMember }) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const source = member.profile_url;
  const showImage = Boolean(source) && failedSrc !== source;

  return (
    <span className="movie-cast-portrait">
      {showImage && source ? (
        <Image alt="" fill onError={() => setFailedSrc(source)} sizes="72px" src={source} />
      ) : (
        <span aria-hidden="true">{personInitials(member.name)}</span>
      )}
    </span>
  );
}

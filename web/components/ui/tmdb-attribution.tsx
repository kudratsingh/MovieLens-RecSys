import Image from "next/image";

/**
 * The sentence TMDB's terms require, written once.
 *
 * It had three copies before this — the legacy dashboard, the movie page, and
 * nowhere else — and the product routes that show TMDB posters on every card
 * had none. A required notice with several hand-maintained copies drifts on the
 * first edit, so the wording lives here and every surface renders it from this
 * constant.
 */
export const TMDB_ATTRIBUTION_NOTICE =
  "This product uses the TMDB API but is not endorsed or certified by TMDB.";

/** The mark at the size TMDB publishes it for inline use. */
const LOGO_WIDTH = 100;
const LOGO_HEIGHT = 13;

/**
 * The TMDB mark, a link to themoviedb.org, and the non-endorsement sentence.
 *
 * Two placements use it, and they are not interchangeable. The shell renders it
 * once per page as the product-wide notice — posters, backdrops, scores and
 * cast all come from TMDB, so the notice belongs to the product rather than to
 * whichever route happens to show the most of it. Movie detail additionally
 * scopes a copy to its enriched block with a `lead`, which the design contract
 * asks for explicitly and says does not stand in for the shell-level one.
 *
 * `className` rather than a variant prop: each placement owns its own layout,
 * and the component owns the mark, the link and the words.
 */
export function TmdbAttribution({
  className = "tmdb-attribution",
  lead,
}: {
  className?: string;
  /** A sentence scoping the notice to one block, e.g. `Details from TMDB.` */
  lead?: string;
}) {
  return (
    <p className={className}>
      <a href="https://www.themoviedb.org" rel="noopener noreferrer" target="_blank">
        <Image alt="TMDB" height={LOGO_HEIGHT} src="/tmdb-logo.svg" width={LOGO_WIDTH} />
      </a>
      <span>{lead ? `${lead} ${TMDB_ATTRIBUTION_NOTICE}` : TMDB_ATTRIBUTION_NOTICE}</span>
    </p>
  );
}

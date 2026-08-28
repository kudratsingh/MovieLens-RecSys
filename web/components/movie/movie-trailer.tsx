"use client";

/**
 * The trailer, behind one deliberate press.
 *
 * The promise this component exists to keep is narrow and testable: **no
 * request reaches YouTube until the viewer asks for one.** A page that embeds a
 * player on render hands every viewer of every movie page to a third party
 * before they have expressed any interest at all, and the usual "lite" trick —
 * showing YouTube's own thumbnail behind a play button — makes that request
 * anyway, just for an image. So the plate is drawn from artwork this product
 * already holds (the TMDB backdrop, or the poster when there is no backdrop),
 * and the iframe is constructed only in the branch that runs after the click.
 *
 * `youtube-nocookie.com` is then the host that does not set a tracking cookie
 * for a viewer who never plays anything, which is the second half of the same
 * promise.
 *
 * The control is a button, not a link or a bare image, so it is keyboard
 * operable for free; closing returns focus to it, because the alternative is a
 * viewer who pressed Escape landing at the top of the document.
 */

import Image from "next/image";
import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/ui/icons";
import { trailerEmbedUrl, type MovieTrailer } from "@/lib/movie-details";
// Styled by `movie-detail-view.css`, which the detail view imports: this is a
// section of that route rather than a free-standing primitive, and splitting one
// page's layout across two stylesheets buys nothing until a second caller exists.

export function MovieTrailerSection({
  trailer,
  title,
  stillUrl,
  headingId,
}: {
  trailer: MovieTrailer;
  /** The display title, used in the plate label and the frame's own name. */
  title: string;
  /** Backdrop or poster: local artwork standing in for the video still. */
  stillUrl: string | null;
  headingId: string;
}) {
  const [playing, setPlaying] = useState(false);
  const [stillFailed, setStillFailed] = useState(false);
  const playRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef(false);

  useEffect(() => {
    if (playing) {
      // The plate the press came from is gone, so focus has to be placed
      // deliberately: on `Close trailer`, which is both the counterpart action
      // and the only thing that keeps Escape inside this section — a keydown
      // on `<body>` never reaches the handler below.
      closeRef.current?.focus();
      return;
    }
    if (returnFocus.current) {
      returnFocus.current = false;
      playRef.current?.focus();
    }
  }, [playing]);

  function close() {
    returnFocus.current = true;
    setPlaying(false);
  }

  return (
    <section
      aria-labelledby={headingId}
      className="movie-trailer"
      onKeyDown={(event) => {
        if (event.key === "Escape" && playing) {
          event.preventDefault();
          close();
        }
      }}
    >
      <h2 className="rule-label" id={headingId}>
        Trailer
      </h2>

      {playing ? (
        <div className="movie-trailer-frame">
          <iframe
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
            src={trailerEmbedUrl(trailer)}
            title={`${trailer.name} — ${title}`}
          />
        </div>
      ) : (
        <button
          className="movie-trailer-plate"
          onClick={() => setPlaying(true)}
          ref={playRef}
          type="button"
        >
          {stillUrl && !stillFailed ? (
            <Image
              alt=""
              className="movie-trailer-still"
              fill
              onError={() => setStillFailed(true)}
              sizes="(max-width: 900px) 100vw, 60rem"
              src={stillUrl}
            />
          ) : null}
          <span className="movie-trailer-scrim" aria-hidden="true" />
          <span className="movie-trailer-cue">
            <span aria-hidden="true" className="movie-trailer-badge">
              <PlayGlyph />
            </span>
            <span className="movie-trailer-label">Play trailer</span>
            {/* The button's name says which movie without the visible label
                having to repeat a title that is already the page's heading. */}
            <span className="visually-hidden"> for {title}</span>
            <span className="movie-trailer-name">{trailer.name}</span>
          </span>
        </button>
      )}

      {playing ? (
        <p className="movie-trailer-note">
          <button className="button-quiet" onClick={close} ref={closeRef} type="button">
            <Icon name="close" />
            Close trailer
          </button>
        </p>
      ) : (
        <p className="movie-trailer-note">
          Nothing loads from YouTube until you press play.
        </p>
      )}
    </section>
  );
}

function PlayGlyph() {
  return (
    <svg aria-hidden="true" height="22" viewBox="0 0 24 24" width="22">
      <path d="M8 5.5v13l11-6.5z" fill="currentColor" />
    </svg>
  );
}

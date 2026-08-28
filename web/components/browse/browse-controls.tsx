"use client";

/**
 * Search, genre, decade, and sort for Browse.
 *
 * These map one-to-one onto the catalog endpoint's parameters, which is why
 * genre is single-select: the endpoint takes one `genre`, and a multi-select
 * chip row would promise a filter the query cannot express. Facets carry no
 * counts for the same reason — the endpoint returns `next_cursor` and
 * `has_more`, never a total, and a count on a chip would be invented.
 *
 * Filters live in the Bundle 4 sheet at every width so there is exactly one
 * set of controls in the accessibility tree; search, sort, and the active
 * filter chips stay visible beside it, so the current cut is always readable
 * without opening anything.
 */

import { useId, useState } from "react";

import { Drawer } from "@/components/ui/drawer";
import { Icon } from "@/components/ui/icons";
import {
  BROWSE_DECADES,
  BROWSE_GENRES,
  BROWSE_SORT_LABELS,
} from "@/lib/browse/facets";
import { BROWSE_SORTS, type BrowseQuery, type BrowseSort } from "@/lib/browse/query";
import "./browse-explorer.css";

export type BrowseFilterPatch = Partial<Omit<BrowseQuery, "cursor">>;

export function BrowseControls({
  query,
  onChange,
  onClear,
  hasFilters,
}: {
  query: BrowseQuery;
  onChange: (patch: BrowseFilterPatch) => void;
  onClear: () => void;
  hasFilters: boolean;
}) {
  const [draft, setDraft] = useState(query.q);
  const [syncedQuery, setSyncedQuery] = useState(query.q);
  const searchId = useId();
  const sortId = useId();

  // The URL stays authoritative: a cleared filter set or a restored deep link
  // has to be reflected in the field the viewer is looking at. Adjusting during
  // render rather than in an effect avoids a frame showing the stale draft.
  if (syncedQuery !== query.q) {
    setSyncedQuery(query.q);
    setDraft(query.q);
  }

  const activeDecade = BROWSE_DECADES.find(
    (decade) => decade.yearFrom === query.yearFrom && decade.yearTo === query.yearTo,
  );

  return (
    <>
      <div className="browse-toolbar">
        <form
          className="browse-search"
          onSubmit={(event) => {
            event.preventDefault();
            onChange({ q: draft.trim() });
          }}
          role="search"
        >
          <label className="search-field" htmlFor={searchId}>
            <span className="visually-hidden">Search movie titles</span>
            <Icon name="search" />
            <input
              id={searchId}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Search titles"
              type="search"
              value={draft}
            />
          </label>
          <button className="button-primary" type="submit">
            Search
          </button>
        </form>
        <Drawer
          buttonClassName="button-secondary"
          buttonLabel="Filters"
          eyebrow="Narrow the catalog"
          title="Filters"
        >
          <fieldset className="filter-options">
            <legend>Genre</legend>
            <div className="filter-chips">
              {BROWSE_GENRES.map((genre) => (
                <button
                  aria-pressed={query.genre === genre}
                  key={genre}
                  onClick={() =>
                    onChange({ genre: query.genre === genre ? null : genre })
                  }
                  type="button"
                >
                  {genre}
                </button>
              ))}
            </div>
          </fieldset>
          <fieldset className="filter-options">
            <legend>Release decade</legend>
            <div className="filter-chips">
              {BROWSE_DECADES.map((decade) => {
                const active = activeDecade?.label === decade.label;
                return (
                  <button
                    aria-pressed={active}
                    key={decade.label}
                    onClick={() =>
                      onChange({
                        yearFrom: active ? null : decade.yearFrom,
                        yearTo: active ? null : decade.yearTo,
                      })
                    }
                    type="button"
                  >
                    {decade.label}
                  </button>
                );
              })}
            </div>
          </fieldset>
          <p className="filter-note">
            One genre at a time: the catalog endpoint takes a single genre, and
            these chips show what it can actually answer.
          </p>
          <button className="button-secondary" onClick={onClear} type="button">
            Clear all filters
          </button>
        </Drawer>
      </div>

      <div className="browse-active">
        {hasFilters ? (
          <div aria-label="Active filters" className="active-filters">
            {query.q ? (
              <button onClick={() => onChange({ q: "" })} type="button">
                “{query.q}” <span aria-hidden="true">×</span>
                <span className="visually-hidden">Clear the title search</span>
              </button>
            ) : null}
            {query.genre ? (
              <button onClick={() => onChange({ genre: null })} type="button">
                {query.genre} <span aria-hidden="true">×</span>
                <span className="visually-hidden">Remove the genre filter</span>
              </button>
            ) : null}
            {activeDecade ? (
              <button
                onClick={() => onChange({ yearFrom: null, yearTo: null })}
                type="button"
              >
                {activeDecade.label} <span aria-hidden="true">×</span>
                <span className="visually-hidden">Remove the decade filter</span>
              </button>
            ) : null}
            {/*
              The sort is not a chip. Every chip in this row is a removable
              narrowing of the result set, and a sort removes nothing — it
              reorders. Offering `Most watched here ×` read as a filter the
              viewer had somehow applied, and the select below already shows
              which ordering is active.
            */}
          </div>
        ) : null}

        <label className="browse-sort" htmlFor={sortId}>
          <span>Sort</span>
          <select
            id={sortId}
            onChange={(event) =>
              onChange({ sort: event.target.value as BrowseSort })
            }
            value={query.sort}
          >
            {BROWSE_SORTS.map((sort) => (
              <option key={sort} value={sort}>
                {BROWSE_SORT_LABELS[sort]}
              </option>
            ))}
          </select>
        </label>
      </div>
    </>
  );
}

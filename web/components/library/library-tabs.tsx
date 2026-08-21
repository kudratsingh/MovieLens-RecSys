"use client";

import { useState } from "react";

import { MovieCollection } from "@/components/movie/movie-collection";
import { EmptyState } from "@/components/ui/resource-states";
import type { LibraryCollection } from "@/lib/movie-types";
import "./library-tabs.css";

type Tab = keyof LibraryCollection;

const labels: Record<Tab, string> = {
  rated: "Rated",
  watchlist: "Watchlist",
  history: "History",
};

export function LibraryTabs({ collection, initialTab = "rated" }: { collection: LibraryCollection; initialTab?: Tab }) {
  const [active, setActive] = useState<Tab>(initialTab);
  const items = collection[active];

  return (
    <section className="library-shell">
      <div aria-label="Library collections" className="library-tabs" role="tablist">
        {(Object.keys(labels) as Tab[]).map((tab) => (
          <button
            aria-controls={`library-panel-${tab}`}
            aria-selected={active === tab}
            id={`library-tab-${tab}`}
            key={tab}
            onClick={() => setActive(tab)}
            role="tab"
            tabIndex={active === tab ? 0 : -1}
            type="button"
          >
            {labels[tab]} <span>{collection[tab].length}</span>
          </button>
        ))}
      </div>

      <div
        aria-labelledby={`library-tab-${active}`}
        className="library-panel"
        id={`library-panel-${active}`}
        role="tabpanel"
      >
        <header className="collection-heading">
          <div>
            <p className="eyebrow">{labels[active]} collection</p>
            <h2 className="section-title">
              {active === "rated" ? "Your strongest signals" : active === "watchlist" ? "Saved for later" : "Recently watched"}
            </h2>
          </div>
          <p>Exploring as Action Fan · recorded fixture</p>
        </header>

        {items.length ? (
          <MovieCollection compact label={`${labels[active]} movies`} movies={items} />
        ) : (
          <EmptyState
            message={`Movies added to ${labels[active].toLowerCase()} will collect here.`}
            title={`No ${labels[active].toLowerCase()} movies yet`}
          />
        )}
      </div>
    </section>
  );
}

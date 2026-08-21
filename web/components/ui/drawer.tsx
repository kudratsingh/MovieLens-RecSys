"use client";

import { useEffect, useId, useRef, useState } from "react";

import { Icon } from "@/components/ui/icons";
import "./drawer.css";

export function Drawer({
  buttonLabel,
  buttonClassName = "button-quiet",
  title,
  eyebrow,
  children,
}: {
  buttonLabel: string;
  buttonClassName?: string;
  title: string;
  eyebrow?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
      if (event.key !== "Tab") return;
      const drawer = closeRef.current?.closest("[role=dialog]");
      const focusable = drawer?.querySelectorAll<HTMLElement>(
        'button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
      trigger?.focus();
    };
  }, [open]);

  return (
    <>
      <button
        aria-expanded={open}
        className={buttonClassName}
        onClick={() => setOpen(true)}
        ref={triggerRef}
        type="button"
      >
        {buttonLabel}
      </button>
      {open ? (
        <div className="drawer-layer">
          <button
            aria-label={`Dismiss ${title}`}
            className="drawer-scrim"
            onClick={() => setOpen(false)}
            type="button"
          />
          <section aria-labelledby={titleId} aria-modal="true" className="drawer-panel" role="dialog">
            <div className="drawer-handle" aria-hidden="true" />
            <header className="drawer-header">
              <div>
                {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
                <h2 className="section-title" id={titleId}>
                  {title}
                </h2>
              </div>
              <button
                aria-label={`Close ${title}`}
                className="icon-button"
                onClick={() => setOpen(false)}
                ref={closeRef}
                type="button"
              >
                <Icon name="close" />
              </button>
            </header>
            <div className="drawer-content">{children}</div>
          </section>
        </div>
      ) : null}
    </>
  );
}

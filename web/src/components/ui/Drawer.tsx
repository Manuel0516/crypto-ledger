import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { useEscClose, useScrollLock } from "./overlay";
import { cx } from "../../lib/format";

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: string;
  eyebrow?: string;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}

/**
 * Side panel that slides in from the right on desktop and from the bottom on
 * mobile ("bottom sheet"). Used for the event detail view (plan §9, §91).
 */
export function Drawer({ open, onClose, title, eyebrow, children, footer, wide = false }: DrawerProps) {
  useScrollLock(open);
  useEscClose(open, onClose);
  if (!open) return null;

  // Portaled to document.body for the same reason as Dialog — never a
  // descendant of the app shell, so the shell's own sticky/fixed chrome
  // can't end up painted above it on short viewports.
  return createPortal(
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/45 animate-fade-in" onClick={onClose} aria-hidden="true" />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cx(
          "absolute inset-x-0 bottom-0 z-10 flex flex-col rounded-t-2xl bg-surface shadow-modal",
          "animate-slide-up-sheet",
          "max-h-[calc(100dvh-2.5rem)] sm:max-h-none sm:inset-y-0 sm:right-0 sm:left-auto sm:h-full sm:rounded-none sm:animate-slide-in-right",
          wide ? "sm:w-[560px]" : "sm:w-[460px]",
          "border-t sm:border-t-0 sm:border-l border-line",
        )}
      >
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-line px-5 py-4 sm:px-6">
          <div className="min-w-0">
            {eyebrow && <p className="font-mono text-[10px] uppercase tracking-widest text-faint">{eyebrow}</p>}
            <h2 className="truncate text-lg font-semibold tracking-tight text-ink">{title}</h2>
          </div>
          <button
            onClick={onClose}
            aria-label="Close panel"
            className="grid size-8 shrink-0 place-items-center rounded-lg text-soft transition-colors hover:bg-base hover:text-ink"
          >
            <CloseX />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">{children}</div>
        {footer && (
          <div className="shrink-0 border-t border-line px-5 pb-[calc(1rem+env(safe-area-inset-bottom,0px))] pt-4 sm:px-6 sm:pb-4">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

function CloseX() {
  return (
    <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}
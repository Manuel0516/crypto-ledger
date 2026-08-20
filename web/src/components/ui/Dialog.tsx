import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { useEscClose, useScrollLock } from "./overlay";
import { cx } from "../../lib/format";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  eyebrow?: string;
  children: ReactNode;
  footer?: ReactNode;
  narrow?: boolean;
}

export function Dialog({ open, onClose, title, eyebrow, children, footer, narrow = false }: DialogProps) {
  useScrollLock(open);
  useEscClose(open, onClose);
  if (!open) return null;

  // Rendered to a portal on document.body — this must never be a normal
  // descendant of the app shell. The shell's own sticky header/bottom nav
  // live in sibling stacking contexts; nesting the dialog inside <main>
  // left it vulnerable to being painted under them on short viewports
  // despite a "winning" z-index, since z-index only resolves stacking
  // within a shared context, not through it.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center sm:p-6">
      <div className="absolute inset-0 bg-black/50 animate-fade-in" onClick={onClose} aria-hidden="true" />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        className={cx(
          "relative z-10 flex w-full flex-col rounded-t-2xl border border-line bg-surface shadow-modal animate-slide-up",
          "sm:rounded-2xl sm:mx-auto",
          narrow ? "sm:max-w-md" : "sm:max-w-lg",
          "max-h-[calc(100dvh-2.5rem)] sm:max-h-[85dvh]",
        )}
      >
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-line px-5 py-4 sm:px-6">
          <div className="min-w-0">
            {eyebrow && <p className="font-mono text-[10px] uppercase tracking-widest text-faint">{eyebrow}</p>}
            <h2 id="dialog-title" className="truncate text-lg font-semibold tracking-tight text-ink">
              {title}
            </h2>
          </div>
          <button
            onClick={onClose}
            aria-label="Close dialog"
            className="grid size-8 shrink-0 place-items-center rounded-lg text-soft transition-colors hover:bg-base hover:text-ink"
          >
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-6 pt-6 sm:px-6 sm:pb-5 sm:pt-5">{children}</div>
        {footer && (
          <div className="flex shrink-0 justify-end gap-2.5 border-t border-line px-5 pb-[calc(1rem+env(safe-area-inset-bottom,0px))] pt-4 sm:px-6 sm:pb-4">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
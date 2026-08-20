import type { ReactNode } from "react";
import { cx } from "../../lib/format";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  text?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ icon, title, text, action, className }: EmptyStateProps) {
  return (
    <div className={cx("flex flex-col items-center justify-center gap-2 py-14 text-center", className)}>
      {icon && (
        <div className="mb-1 grid size-12 place-items-center rounded-2xl bg-base text-soft">{icon}</div>
      )}
      <p className="text-sm font-semibold text-ink">{title}</p>
      {text && <p className="max-w-sm text-xs text-soft">{text}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="fade-in rounded-card border border-bad/25 bg-bad-soft px-5 py-4 text-sm text-bad">
      <p className="font-semibold">Could not load this view</p>
      <p className="mt-1 opacity-90">{message}</p>
    </div>
  );
}

export function PageError({ message }: { message: string }) {
  return <ErrorState message={message} />;
}
import type { ReactNode } from "react";
import { cx } from "../../lib/format";

interface SectionHeaderProps {
  eyebrow?: string;
  title: ReactNode;
  subtitle?: string;
  action?: ReactNode;
  className?: string;
}

export function SectionHeader({ eyebrow, title, subtitle, action, className }: SectionHeaderProps) {
  return (
    <div className={cx("flex items-end justify-between gap-4", className)}>
      <div className="min-w-0">
        {eyebrow && <p className="font-mono text-[10px] uppercase tracking-widest text-faint">{eyebrow}</p>}
        <h2 className="mt-0.5 text-lg font-semibold tracking-tight text-ink">{title}</h2>
        {subtitle && <p className="mt-1 text-xs text-soft">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}
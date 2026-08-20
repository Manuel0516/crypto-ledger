import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "../../lib/format";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  interactive?: boolean;
  padded?: boolean;
  elevated?: boolean;
}

/**
 * Base surface component. The visual identity of the whole app inherits this
 * so any future component foundation swap keeps one consistent look (plan §6).
 */
export function Card({ children, className, interactive = false, padded = true, elevated = false, ...rest }: CardProps) {
  return (
    <div
      className={cx(
        "rounded-card bg-surface",
        elevated ? "shadow-card" : "border border-line",
        padded && "p-5",
        interactive &&
          "transition-all duration-100 cursor-pointer hover:border-line-strong hover:shadow-card active:scale-[.995]",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  eyebrow,
  title,
  subtitle,
  action,
}: {
  eyebrow?: string;
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-start justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <p className="font-mono text-[10px] uppercase tracking-widest text-faint">{eyebrow}</p>}
        {title && <h3 className="mt-0.5 text-[15px] font-semibold tracking-tight text-ink">{title}</h3>}
        {subtitle && <p className="mt-1 text-xs text-soft">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}
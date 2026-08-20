import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cx } from "../../lib/format";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "accent-soft";
export type ButtonSize = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  loading?: boolean;
  block?: boolean;
}

const base =
  "inline-flex items-center justify-center gap-2 rounded-btn font-semibold whitespace-nowrap transition-all duration-100 select-none disabled:opacity-50 disabled:pointer-events-none";

const sizes: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-xs gap-1.5",
  md: "h-9.5 px-4 text-sm gap-2",
};

const variants: Record<ButtonVariant, string> = {
  primary: "bg-button-primary text-white hover:bg-button-primary-hover shadow-sm active:scale-[.98]",
  "accent-soft": "bg-accent-soft text-accent hover:brightness-95 active:scale-[.98]",
  secondary: "bg-surface text-ink border border-line hover:border-line-strong hover:bg-base active:scale-[.98]",
  ghost: "text-soft hover:text-ink hover:bg-base",
  danger: "bg-bad-soft text-bad hover:brightness-95 active:scale-[.98]",
};

export function Button({
  children,
  variant = "secondary",
  size = "md",
  icon,
  loading = false,
  block = false,
  className,
  disabled,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={cx(base, sizes[size], variants[variant], block && "w-full", className)}
      {...rest}
    >
      {loading ? <Spinner className="size-3.5" /> : icon}
      {children}
    </button>
  );
}

export function Spinner({ className = "size-4" }: { className?: string }) {
  return (
    <svg className={cx("animate-spin", className)} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
      <path
        d="M12 2a10 10 0 0 1 10 10"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        className="opacity-80"
      />
    </svg>
  );
}
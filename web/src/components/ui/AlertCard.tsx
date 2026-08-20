import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { cx } from "../../lib/format";

export type AlertTone = "info" | "success" | "warning" | "danger";

const toneStyles: Record<AlertTone, { wrapper: string; icon: string; Icon: typeof Info }> = {
  info: { wrapper: "border-accent/25 bg-accent-soft/50 text-ink", icon: "text-accent", Icon: Info },
  success: { wrapper: "border-good/25 bg-good-soft text-good", icon: "text-good", Icon: CheckCircle2 },
  warning: { wrapper: "border-warn/30 bg-warn-soft text-ink", icon: "text-warn", Icon: AlertTriangle },
  danger: { wrapper: "border-bad/25 bg-bad-soft text-bad", icon: "text-bad", Icon: AlertTriangle },
};

interface AlertCardProps {
  children: ReactNode;
  tone?: AlertTone;
  title?: string;
  icon?: ReactNode;
  className?: string;
  role?: "alert" | "status";
}

/** Shared inline alert surface for warnings, errors, confirmations, and status messages. */
export function AlertCard({ children, tone = "info", title, icon, className, role }: AlertCardProps) {
  const style = toneStyles[tone];
  const Icon = style.Icon;
  return (
    <div role={role} className={cx("flex items-start gap-3 rounded-lg border px-3.5 py-3 text-sm", style.wrapper, className)}>
      <span className={cx("mt-0.5 shrink-0", style.icon)}>{icon ?? <Icon size={18} />}</span>
      <div className="min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        <div className={title ? "mt-0.5" : undefined}>{children}</div>
      </div>
    </div>
  );
}

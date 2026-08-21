import { CheckCircle2, RefreshCcw, ShieldCheck, TriangleAlert } from "lucide-react";
import type { Readiness } from "../../types";
import { getJson } from "../../lib/api";
import { useData } from "../../hooks/useData";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ErrorState } from "../ui/EmptyState";
import { Skeleton } from "../ui/Skeleton";
import { cx, formatNumber } from "../../lib/format";

/** Readiness checklist shown on the Reports page (plan §95). */
export function ReportReadiness({ refreshToken }: { refreshToken: number }) {
  const { data, loading, error, refresh } = useData<Readiness>(() => getJson("/api/reports/readiness"), [refreshToken]);

  if (loading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-12" />
        <Skeleton className="h-12" />
        <Skeleton className="h-12" />
      </div>
    );
  }
  if (error) return <ErrorState message={error} />;
  if (!data) return null;

  const checks = [
    { label: "Activities", value: `${formatNumber(data.activity_count ?? data.events, 0)} recorded`, ok: true },
    { label: "Priced activities", value: `${formatNumber(data.priced_activity_count ?? data.prices, 0)} available`, ok: true },
    { label: "Warnings", value: `${formatNumber(data.warning_count ?? data.unresolved_issues, 0)} to review`, ok: (data.warning_count ?? data.unresolved_issues) === 0 },
    { label: "Incomplete activities", value: `${formatNumber(data.incomplete_activity_count ?? 0, 0)}`, ok: (data.incomplete_activity_count ?? 0) === 0 },
  ];

  return (
    <div className={cx("rounded-card border bg-surface p-5", data.ready ? "border-line" : "border-warn/30")}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <span
            className={cx(
              "grid size-10 place-items-center rounded-xl",
              data.ready ? "bg-good-soft text-good" : "bg-warn-soft text-warn",
            )}
          >
            {data.ready ? <ShieldCheck size={20} /> : <TriangleAlert size={20} />}
          </span>
          <div>
            <h3 className="text-sm font-semibold text-ink">{data.ready ? "Ready to report" : "Review recommended"}</h3>
            <p className="mt-0.5 text-xs text-soft">{formatNumber(data.activity_count ?? data.events, 0)} activities in the canonical ledger</p>
          </div>
        </div>
        <Badge tone={data.ready ? "success" : "warning"}>{data.ready ? "Ready" : "Not ready"}</Badge>
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        {checks.map((check) => (
          <div key={check.label} className="flex items-center gap-2.5 border-t border-line pt-3">
            {check.ok ? (
              <CheckCircle2 size={16} className="shrink-0 text-good" />
            ) : (
              <TriangleAlert size={16} className="shrink-0 text-warn" />
            )}
            <div>
              <p className="text-sm font-medium text-ink">{check.label}</p>
              <p className="text-[11px] text-soft">{check.value}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-5 border-t border-line pt-4">
        <Button size="sm" variant="ghost" icon={<RefreshCcw size={14} />} onClick={() => void refresh()}>
          Re-validate ledger
        </Button>
      </div>
    </div>
  );
}

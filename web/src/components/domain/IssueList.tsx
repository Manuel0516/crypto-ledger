import { useState } from "react";
import { AlertTriangle, CheckCircle2, RefreshCw, ShieldAlert, XCircle } from "lucide-react";
import type { Issue } from "../../types";
import { getJson, postJson } from "../../lib/api";
import { useData } from "../../hooks/useData";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { Skeleton } from "../ui/Skeleton";
import { cx } from "../../lib/format";

export function IssueIcon({ severity }: { severity: string }) {
  if (severity === "high") return <XCircle size={18} className="text-bad" />;
  if (severity === "critical") return <ShieldAlert size={18} className="text-bad" />;
  return <AlertTriangle size={18} className="text-warn" />;
}

interface IssueListProps {
  onOpenEvent?: (id: number) => void;
  onResolved?: () => void;
}

export function IssueList({ onOpenEvent, onResolved }: IssueListProps) {
  const { data, loading, error, refresh } = useData<Issue[]>(() => getJson("/api/issues"), []);
  const [retryingPricing, setRetryingPricing] = useState(false);
  const [pricingError, setPricingError] = useState("");

  const resolve = async (issue: Issue) => {
    await postJson(`/api/issues/${issue.id}/resolve`, {});
    await refresh();
    onResolved?.();
  };

  const retryPricing = async () => {
    setRetryingPricing(true);
    setPricingError("");
    try {
      await postJson("/api/issues/retry-pricing", {});
      await refresh();
      onResolved?.();
    } catch (reason) {
      setPricingError(reason instanceof Error ? reason.message : "Could not retry asset pricing");
    } finally {
      setRetryingPricing(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-2.5">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-16" />
        ))}
      </div>
    );
  }

  if (error) return <p className="text-xs text-bad">{error}</p>;
  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<CheckCircle2 size={22} className="text-good" />}
        title="No open issues"
        text="Uncertainty is never silently guessed — and right now, there is nothing to resolve."
      />
    );
  }

  const hasPricingIssues = data.some((issue) => issue.title === "Unknown asset — no price source" || issue.title === "Missing price");

  return (
    <div className="space-y-2.5">
      {hasPricingIssues && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-line bg-base px-3.5 py-3">
          <p className="text-xs text-soft">Retry market-data lookup for unresolved assets and missing prices.</p>
          <Button size="sm" variant="secondary" icon={<RefreshCw size={13} />} loading={retryingPricing} disabled={retryingPricing} onClick={() => void retryPricing()}>
            Retry pricing
          </Button>
        </div>
      )}
      {pricingError && <p className="text-xs text-bad">{pricingError}</p>}
      {data.map((issue) => (
        <IssueRow key={issue.id} issue={issue} onOpenEvent={onOpenEvent} onResolve={resolve} />
      ))}
    </div>
  );
}

function IssueRow({
  issue,
  onOpenEvent,
  onResolve,
}: {
  issue: Issue;
  onOpenEvent?: (id: number) => void;
  onResolve: (issue: Issue) => Promise<void>;
}) {
  const [working, setWorking] = useState<"resolve" | null>(null);
  const [error, setError] = useState("");

  const run = async () => {
    setWorking("resolve");
    setError("");
    try {
      await onResolve(issue);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "That didn't work — try again");
    } finally {
      setWorking(null);
    }
  };

  return (
    <div className="flex items-start gap-3 rounded-lg border border-warn/25 bg-warn-soft/50 px-3.5 py-3">
      <IssueIcon severity={issue.severity} />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-ink">{issue.title}</p>
        <p className="mt-0.5 text-xs text-soft">{issue.detail}</p>
        {error && <p className="mt-1 text-xs text-bad">{error}</p>}
      </div>
      <div className="flex shrink-0 flex-col gap-1.5">
        {issue.event_id != null && (
          <Button size="sm" variant="ghost" onClick={() => onOpenEvent?.(issue.event_id as number)}>
            View event
          </Button>
        )}
        <Button size="sm" variant="accent-soft" loading={working === "resolve"} disabled={working !== null} onClick={() => void run()}>
          Resolve issue
        </Button>
      </div>
    </div>
  );
}

export function issueCountTone(count: number) {
  return cx(count === 0 ? "text-good" : count < 3 ? "text-warn" : "text-bad");
}

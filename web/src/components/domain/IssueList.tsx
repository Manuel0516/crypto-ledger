import { useState } from "react";
import { AlertTriangle, CheckCircle2, Link2, ShieldAlert, XCircle } from "lucide-react";
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

  const resolve = async (issue: Issue) => {
    await postJson(`/api/issues/${issue.id}/resolve`, {});
    await refresh();
    onResolved?.();
  };

  const link = async (issue: Issue) => {
    await postJson(`/api/issues/${issue.id}/link`, {});
    await refresh();
    onResolved?.();
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

  return (
    <div className="space-y-2.5">
      {data.map((issue) => (
        <IssueRow key={issue.id} issue={issue} onOpenEvent={onOpenEvent} onResolve={resolve} onLink={link} />
      ))}
    </div>
  );
}

function IssueRow({
  issue,
  onOpenEvent,
  onResolve,
  onLink,
}: {
  issue: Issue;
  onOpenEvent?: (id: number) => void;
  onResolve: (issue: Issue) => Promise<void>;
  onLink: (issue: Issue) => Promise<void>;
}) {
  const [working, setWorking] = useState<"resolve" | "link" | null>(null);
  const [error, setError] = useState("");

  const run = async (kind: "resolve" | "link") => {
    setWorking(kind);
    setError("");
    try {
      await (kind === "link" ? onLink(issue) : onResolve(issue));
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
        {issue.linkable && <Button size="sm" variant="accent-soft" icon={<Link2 size={13} />} loading={working === "link"} disabled={working !== null} onClick={() => void run("link")}>
          Link accounts
        </Button>}
        <Button size="sm" variant={issue.linkable ? "secondary" : "accent-soft"} loading={working === "resolve"} disabled={working !== null} onClick={() => void run("resolve")}>
          Resolve issue
        </Button>
      </div>
    </div>
  );
}

export function issueCountTone(count: number) {
  return cx(count === 0 ? "text-good" : count < 3 ? "text-warn" : "text-bad");
}

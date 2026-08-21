import { useMemo } from "react";
import {
  Activity as ActivityIcon,
  AlertTriangle,
  ChevronRight,
  CheckCircle2,
  Plus,
  RefreshCw,
  WalletCards,
} from "lucide-react";
import type { Account, AssetHolding, EventListResponse, Issue, OverviewData, Page } from "../types";
import { getJson } from "../lib/api";
import { useData } from "../hooks/useData";
import { Card } from "../components/ui/Card";
import { SectionHeader } from "../components/ui/SectionHeader";
import { Button } from "../components/ui/Button";
import { PageError, EmptyState } from "../components/ui/EmptyState";
import { Skeleton } from "../components/ui/Skeleton";
import { PortfolioValue } from "../components/domain/PortfolioValue";
import { deriveLedgerStats } from "../lib/ledgerStats";
import { HoldingCard } from "../components/domain/HoldingCard";
import { ActivityCard } from "../components/domain/ActivityList";
import { SourceHealth } from "../components/domain/SourceHealth";
import { BackupStatus } from "../components/domain/BackupStatus";
import { IssueList } from "../components/domain/IssueList";
import { formatCrypto, formatNumber, relativeTime, cx } from "../lib/format";

interface OverviewPageProps {
  onOpenEvent: (id: number) => void;
  navigate: (page: Page) => void;
}

export function OverviewPage({ onOpenEvent, navigate }: OverviewPageProps) {
  const { data: overview, loading: overviewLoading, error: overviewError } = useData<OverviewData>(() => getJson("/api/overview"), []);
  const { data: eventsData, loading: eventsLoading, error: eventsError } = useData<EventListResponse>(
    () => getJson("/api/events?limit=50"),
    [],
  );
  const events = eventsData?.items ?? [];
  const { data: issues, refresh: refreshIssues } = useData<Issue[]>(() => getJson("/api/issues"), []);
  const { data: accounts, loading: accountsLoading } = useData<Account[]>(() => getJson("/api/accounts"), []);
  const { refresh: refreshOverview } = useData<OverviewData>(() => getJson("/api/overview"), []);

  const stats = useMemo(() => deriveLedgerStats(events), [events]);

  const flows = useMemo(() => {
    const map = new Map<string, number>();
    const dayAgo = Date.now() - 24 * 60 * 60 * 1000;
    for (const event of events) {
      if (new Date(event.occurred_at).getTime() < dayAgo) continue;
      const direction = event.direction === "-" ? -1 : 1;
      map.set(event.asset_symbol, (map.get(event.asset_symbol) ?? 0) + Number(event.primary_amount) * direction);
    }
    return map;
  }, [events]);

  const holdingsTotal = useMemo(
    () => (overview?.assets ?? []).reduce((sum, asset) => sum + asset.value_display, 0),
    [overview],
  );

  const lastSync = overview?.last_sync ?? null;
  const openIssues = issues?.length ?? overview?.issues ?? 0;

  if (overviewError) return <PageError message={overviewError} />;

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Status strip */}
      <div className="flex flex-wrap items-center gap-2">
        <StatusChip
          tone={lastSync ? "good" : "neutral"}
          icon={lastSync ? <RefreshCw size={12} /> : undefined}
          label={lastSync ? `Synced ${relativeTime(lastSync)}` : "No source synced yet"}
        />
        <StatusChip
          tone={openIssues === 0 ? "good" : openIssues < 3 ? "warn" : "bad"}
          icon={openIssues === 0 ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />}
          label={openIssues === 0 ? "No open issues" : `${openIssues} open issue${openIssues === 1 ? "" : "s"}`}
        />
      </div>

      {/* Hero */}
      {overviewLoading || !overview ? (
        <Skeleton className="h-56" />
      ) : (
        <PortfolioValue
          displayValue={overview.portfolio_display}
          displayCurrency={overview.display_currency}
          secondaryValue={overview.display_currency === "EUR" ? overview.portfolio_sek : overview.portfolio_eur}
          secondaryCurrency={overview.display_currency === "EUR" ? "SEK" : "EUR"}
          assets={(overview.assets ?? []).length}
          stats={stats}
        />
      )}

      {/* Holdings */}
      <section>
        <SectionHeader
          eyebrow="Your holdings"
          title="Assets"
          subtitle="Net position per asset, valued once at the latest cached price · 24h movement from the ledger"
        />
        <div className="mt-3">
          {overviewLoading ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <Skeleton className="h-40" />
              <Skeleton className="h-40" />
            </div>
          ) : (overview?.assets ?? []).length === 0 ? (
            <Card>
              <EmptyState
                icon={<WalletCards size={22} />}
                title="No holdings yet"
                text="Once sources synchronize, your net positions will appear here with allocation and 24h movement."
              />
            </Card>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {(overview?.assets ?? []).map((asset) => (
                <HoldingCard
                  // asset.id, not symbol: two Asset rows can share a symbol
                  // (same coin tracked under different networks) — id is
                  // the real unique key.
                  key={asset.id}
                  asset={asset}
                  allocationPct={allocationOf(asset, holdingsTotal)}
                  flow24h={flowLabel(flows.get(asset.symbol), asset.symbol)}
                />
              ))}
            </div>
          )}
        </div>
      </section>

      {/* Recent activity + right rail */}
      <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
        <section className="min-w-0">
          <SectionHeader
            eyebrow="Latest records"
            title="Recent activity"
            subtitle={
              stats.total > 0
                ? `${formatNumber(stats.total, 0)} records · ${stats.incoming} in / ${stats.outgoing} out`
                : undefined
            }
            action={
              <Button size="sm" variant="ghost" icon={<Plus size={14} />} onClick={() => navigate("Activity")}>
                Add activity
              </Button>
            }
          />
          <Card className="mt-3 !p-0 overflow-hidden">
            {eventsLoading ? (
              <div className="p-5">
                <Skeleton className="h-40" />
              </div>
            ) : (events).length === 0 ? (
              <EmptyState
                icon={<ActivityIcon size={22} />}
                title="No activity recorded"
                text="Synchronize sources or add a manual event to start the ledger."
              />
            ) : eventsError ? (
              <p className="p-5 text-xs text-bad">{eventsError}</p>
            ) : (
              <>
                <div className="divide-y divide-line">
                  {(events).slice(0, 5).map((event) => (
                    <ActivityCard key={event.id} event={event} onOpen={onOpenEvent} forceVisible />
                  ))}
                </div>
                <div className="border-t border-line px-5 py-3">
                  <button
                    onClick={() => navigate("Activity")}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-accent hover:text-accent-hover"
                  >
                    View all activity <ChevronRight size={14} />
                  </button>
                </div>
              </>
            )}
          </Card>
        </section>

        <div className="space-y-4 self-start">
          <SourceHealth
            accounts={accounts ?? []}
            loading={accountsLoading}
            onManage={() => navigate("Linked Accounts")}
            onAdd={() => navigate("Linked Accounts")}
          />
          <BackupStatus />
        </div>
      </div>

      {/* Issues */}
      {openIssues > 0 && (
        <section>
          <SectionHeader
            eyebrow="Reconciliation"
            title="Needs review"
            subtitle="Uncertainty is surfaced explicitly — never silently guessed"
            action={
              <Button
                size="sm"
                variant={openIssues >= 3 ? "danger" : "accent-soft"}
                icon={<AlertTriangle size={14} />}
              >
                {openIssues} open
              </Button>
            }
          />
          <div className="mt-3">
            <Card>
              <IssueList
                onOpenEvent={onOpenEvent}
                onResolved={() => {
                  void refreshIssues();
                  void refreshOverview();
                }}
              />
            </Card>
          </div>
        </section>
      )}
    </div>
  );
}

function allocationOf(asset: AssetHolding, total: number): number {
  if (total <= 0) return 0;
  return Math.round((asset.value_display / total) * 1000) / 10;
}

function flowLabel(net: number | undefined, symbol: string): string | null {
  if (net === undefined || Math.abs(net) < 1e-8) return null;
  const sign = net > 0 ? "+" : "-";
  return `${sign}${formatCrypto(Math.abs(net), symbol)}`;
}

function StatusChip({
  tone,
  icon,
  label,
}: {
  tone: "good" | "warn" | "bad" | "neutral";
  icon?: React.ReactNode;
  label: string;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full border bg-surface px-3 py-1.5 text-[12px] font-medium",
        tone === "good" && "border-line text-good",
        tone === "warn" && "border-warn/30 text-warn",
        tone === "bad" && "border-bad/30 text-bad",
        tone === "neutral" && "border-line text-soft",
      )}
    >
      {icon}
      {label}
    </span>
  );
}

import { useMemo, useState } from "react";
import { Download, EyeOff, Plus, Search, SlidersHorizontal, X } from "lucide-react";
import type { Account, AssetVisibility, EventListResponse } from "../types";
import { EVENT_TYPES } from "../types";
import { getJson, patchJson, triggerDownload } from "../lib/api";
import { useData } from "../hooks/useData";
import { Card } from "../components/ui/Card";
import { PageError, EmptyState } from "../components/ui/EmptyState";
import { Skeleton } from "../components/ui/Skeleton";
import { ActivityTableHeader, ActivityRow, ActivityCard } from "../components/domain/ActivityList";
import { Button } from "../components/ui/Button";
import { Pagination } from "../components/ui/Pagination";
import { ManualEventDialog } from "../features/activity/ManualEventDialog";
import { cx } from "../lib/format";

const PAGE_SIZE = 25;
const ALL = "ALL";

interface ActivityPageProps {
  onOpenEvent: (id: number) => void;
}

export function ActivityPage({ onOpenEvent }: ActivityPageProps) {
  const { data: accounts } = useData<Account[]>(() => getJson("/api/accounts?include_archived=true"), []);
  const [query, setQuery] = useState("");
  const [type, setType] = useState(ALL);
  const [asset, setAsset] = useState("");
  const [network, setNetwork] = useState("");
  const [source, setSource] = useState("");
  const [accountId, setAccountId] = useState(ALL);
  const [resolution, setResolution] = useState(ALL);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [underThreshold, setUnderThreshold] = useState(false);
  const [showJunk, setShowJunk] = useState(false);
  const [showManual, setShowManual] = useState(false);
  const [showFilters, setShowFilters] = useState(false);

  const requestUrl = useMemo(() => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), page: String(page) });
    if (query.trim()) params.set("search", query.trim());
    if (type !== ALL) params.set("event_type", type);
    if (asset.trim()) params.set("asset", asset.trim());
    if (network.trim()) params.set("network", network.trim());
    if (source.trim()) params.set("source", source.trim());
    if (accountId !== ALL) params.set("account_id", accountId);
    if (resolution !== ALL) params.set("resolved", resolution === "resolved" ? "true" : "false");
    if (dateFrom) params.set("date_from", `${dateFrom}T00:00:00Z`);
    if (dateTo) params.set("date_to", `${dateTo}T23:59:59Z`);
    if (underThreshold) params.set("under_threshold", "true");
    return `/api/events?${params.toString()}`;
  }, [page, query, type, asset, network, source, accountId, resolution, dateFrom, dateTo, underThreshold]);
  const { data, loading, error, refresh } = useData<EventListResponse>(() => getJson(requestUrl), [requestUrl]);
  const { data: junkAssets, loading: junkLoading, error: junkError, refresh: refreshJunk } = useData<AssetVisibility[]>(() => getJson("/api/assets?blocked_only=true"), []);
  const events = data?.items ?? [];

  const resetPagination = () => setPage(1);
  const activeAdvancedCount = [asset, network, source, accountId, resolution].filter((value) => value !== ALL && value !== "").length + (dateFrom ? 1 : 0) + (dateTo ? 1 : 0);
  const anyFilterActive = Boolean(query) || type !== ALL || activeAdvancedCount > 0;

  const clearFilters = () => {
    setQuery(""); setType(ALL); setAsset(""); setNetwork(""); setSource(""); setAccountId(ALL); setResolution(ALL); setDateFrom(""); setDateTo(""); resetPagination();
  };
  const onSaved = () => { setShowManual(false); resetPagination(); void refresh(); void refreshJunk(); };
  const selectActivityView = (next: "activity" | "under_threshold" | "junk") => {
    setShowJunk(next === "junk");
    setUnderThreshold(next === "under_threshold");
    resetPagination();
  };

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="max-w-xl text-sm text-soft">A chronological, tax-neutral record of what economically happened. Interpretation for a specific tax year happens later, in the reporting layer.</p>
        {!showJunk && <div className="flex gap-2.5"><Button icon={<Download size={15} />} onClick={() => triggerDownload("/api/export/ledger.csv")}>Export CSV</Button><Button variant="primary" icon={<Plus size={16} />} onClick={() => setShowManual(true)}>Add event</Button></div>}
      </div>

      {!showJunk && <div className="flex flex-wrap items-center gap-2.5">
        <label className="flex h-10 min-w-0 flex-1 items-center gap-2.5 rounded-field border border-line bg-surface px-3 text-soft transition-colors has-focus:border-accent">
          <Search size={15} className="shrink-0" />
          <input value={query} onChange={(event) => { setQuery(event.target.value); resetPagination(); }} placeholder="Search wallet, address, hash, exchange ID…" className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-faint" aria-label="Search activity" />
          {query && <button onClick={() => { setQuery(""); resetPagination(); }} aria-label="Clear search" className="text-faint hover:text-ink"><X size={14} /></button>}
        </label>
        <select value={type} onChange={(event) => { setType(event.target.value); resetPagination(); }} aria-label="Filter event type" className="h-10 cursor-pointer rounded-field border border-line bg-surface px-3 text-sm text-ink outline-none transition-colors focus:border-accent">
          <option value={ALL}>All types</option>
          {Object.entries(EVENT_TYPES).map(([value, meta]) => <option key={value} value={value}>{meta.label}</option>)}
        </select>
        <Button size="md" variant={showFilters ? "accent-soft" : "secondary"} icon={<SlidersHorizontal size={15} />} onClick={() => setShowFilters((value) => !value)}>Filters{activeAdvancedCount > 0 ? ` (${activeAdvancedCount})` : ""}</Button>
        {anyFilterActive && <Button size="md" variant="ghost" icon={<X size={15} />} onClick={clearFilters}>Clear</Button>}
      </div>}

      {!showJunk && showFilters && (
        <Card className="!py-4"><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <FilterSelect label="Account" value={accountId} onChange={(value) => { setAccountId(value); resetPagination(); }}><option value={ALL}>All accounts</option>{(accounts ?? []).map((account) => <option key={account.id} value={String(account.id)}>{account.name}</option>)}</FilterSelect>
          <FilterInput label="Asset symbol" placeholder="BTC" value={asset} onChange={(value) => { setAsset(value.toUpperCase()); resetPagination(); }} />
          <FilterInput label="Network" placeholder="Ethereum" value={network} onChange={(value) => { setNetwork(value); resetPagination(); }} />
          <FilterInput label="Wallet / address" placeholder="Wallet name or address…" value={source} onChange={(value) => { setSource(value); resetPagination(); }} />
          <FilterSelect label="Issue status" value={resolution} onChange={(value) => { setResolution(value); resetPagination(); }}><option value={ALL}>Resolved + unresolved</option><option value="unresolved">Unresolved only</option><option value="resolved">No open issues</option></FilterSelect>
          <div className="grid grid-cols-2 gap-2"><DateFilter label="From" value={dateFrom} onChange={(value) => { setDateFrom(value); resetPagination(); }} /><DateFilter label="To" value={dateTo} onChange={(value) => { setDateTo(value); resetPagination(); }} /></div>
        </div></Card>
      )}

      <div className="flex gap-1 border-b border-line" role="tablist" aria-label="Activity views">
        <button type="button" role="tab" aria-selected={!underThreshold && !showJunk} onClick={() => selectActivityView("activity")} className={cx("border-b-2 px-1 pb-2 text-sm font-semibold transition-colors", !underThreshold && !showJunk ? "border-accent text-ink" : "border-transparent text-soft hover:text-ink")}>Activity</button>
        <button type="button" role="tab" aria-selected={underThreshold} onClick={() => selectActivityView("under_threshold")} className={cx("border-b-2 px-1 pb-2 text-sm font-semibold transition-colors", underThreshold ? "border-accent text-ink" : "border-transparent text-soft hover:text-ink")}>Activity under threshold</button>
        <button type="button" role="tab" aria-selected={showJunk} onClick={() => selectActivityView("junk")} className={cx("border-b-2 px-1 pb-2 text-sm font-semibold transition-colors", showJunk ? "border-accent text-ink" : "border-transparent text-soft hover:text-ink")}>Junk{(junkAssets ?? []).length > 0 ? ` (${junkAssets?.length})` : ""}</button>
      </div>

      {showJunk ? <JunkAssets assets={junkAssets ?? []} loading={junkLoading} error={junkError} onChanged={() => { void refreshJunk(); void refresh(); }} /> : <>
        <p className="text-xs text-faint">{data?.total ?? 0} event{data?.total === 1 ? "" : "s"}{underThreshold ? " · excluded by threshold" : anyFilterActive ? " · filtered" : ""}</p>
        {error ? <PageError message={error} /> : loading ? <Card><div className="p-4"><Skeleton className="h-64" /></div></Card> : events.length === 0 ? (
          <Card><EmptyState icon={<Search size={22} />} title={data?.total ? "No matching events" : underThreshold ? "No activity under threshold" : "No events yet"} text={data?.total ? "Try a different search or clear the filters." : underThreshold ? "Lower the configured threshold to include more activity in the main view and reports." : "Synchronize a source or add a manual event to start the ledger."} action={!data?.total && !underThreshold ? <Button variant="primary" size="sm" icon={<Plus size={14} />} onClick={() => setShowManual(true)}>Add first event</Button> : undefined} /></Card>
        ) : (
          <Card className="!p-0 overflow-hidden"><div className="hidden md:block"><ActivityTableHeader />{events.map((event) => <ActivityRow key={event.id} event={event} onOpen={onOpenEvent} />)}</div><div className="divide-y divide-line md:hidden">{events.map((event) => <ActivityCard key={event.id} event={event} onOpen={onOpenEvent} />)}</div></Card>
        )}
        <Pagination page={page} pageCount={Math.ceil((data?.total ?? 0) / PAGE_SIZE)} onPageChange={setPage} />
      </>}
      {showManual && <ManualEventDialog onClose={() => setShowManual(false)} onSaved={onSaved} />}
    </div>
  );
}

function JunkAssets({ assets, loading, error, onChanged }: { assets: AssetVisibility[]; loading: boolean; error: string; onChanged: () => void }) {
  const [busy, setBusy] = useState<number | null>(null);
  const unclassify = async (asset: AssetVisibility) => {
    setBusy(asset.id);
    try {
      await patchJson(`/api/assets/${asset.id}`, { is_blocked: false });
      onChanged();
    } finally {
      setBusy(null);
    }
  };
  if (error) return <PageError message={error} />;
  if (loading) return <Card><div className="p-4"><Skeleton className="h-40" /></div></Card>;
  return <Card className="!p-0 overflow-hidden">
    {assets.length === 0 ? <EmptyState icon={<EyeOff size={22} />} title="No classified junk" text="Assets you block will appear here for review. Their raw evidence remains preserved." /> : <div className="divide-y divide-line">{assets.map((asset) => <div key={asset.id} className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><p className="font-semibold text-ink">{asset.symbol}</p>{asset.spam_suspected && <span className="rounded-full bg-info-soft px-2 py-0.5 text-[10px] font-medium text-info">Mass distribution signal</span>}</div><p className="mt-0.5 text-xs text-soft">{asset.name}{asset.network ? ` · ${asset.network}` : ""}</p>{asset.contract_address && <p className="mt-1 break-all font-mono text-[10px] text-faint">{asset.contract_address}</p>}<p className="mt-1 text-[11px] text-faint">{asset.event_count} recorded event{asset.event_count === 1 ? "" : "s"}</p></div><Button size="sm" variant="secondary" loading={busy === asset.id} onClick={() => void unclassify(asset)}>Not junk — unclassify</Button></div>)}</div>}
  </Card>;
}

function FilterSelect({ label, value, onChange, children }: { label: string; value: string; onChange: (value: string) => void; children: React.ReactNode }) {
  return <label className="grid gap-1"><span className="text-[11px] font-medium text-faint">{label}</span><select value={value} onChange={(event) => onChange(event.target.value)} className={cx("h-9 cursor-pointer rounded-field border border-line bg-surface px-2.5 text-xs text-ink outline-none transition-colors focus:border-accent")}>{children}</select></label>;
}
function FilterInput({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (value: string) => void; placeholder: string }) {
  return <label className="grid gap-1"><span className="text-[11px] font-medium text-faint">{label}</span><input value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} className="h-9 rounded-field border border-line bg-surface px-2.5 text-xs text-ink outline-none placeholder:text-faint focus:border-accent" /></label>;
}
function DateFilter({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label className="grid gap-1"><span className="text-[11px] font-medium text-faint">{label}</span><input type="date" value={value} onChange={(event) => onChange(event.target.value)} className="h-9 rounded-field border border-line bg-surface px-2 text-xs text-ink outline-none focus:border-accent" /></label>;
}

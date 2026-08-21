import { useEffect, useState } from "react";
import { Archive, BadgeDollarSign, Check, ChevronDown, Database, Eye, EyeOff, FileText, HardDrive, KeyRound, Languages, Pencil, Plus, RefreshCw, Settings2, ShieldCheck, SlidersHorizontal, TimerReset, Trash2, Waypoints } from "lucide-react";
import type { Account, AppSettings, Page, SecretInventoryItem, SyncResult, TaxCountry, TaxLanguage } from "../types";
import { API_BASE, deleteJson, getJson, patchJson, postJson, triggerDownload } from "../lib/api";
import { Card, CardHeader } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Field, Input, Select } from "../components/ui/Field";
import { StatusPill } from "../components/domain/StatusPill";
import { connectorSummary, connectorTypeMeta } from "../features/accounts/connectorTypes";
import { BackupStatus } from "../components/domain/BackupStatus";
import { useConfirmDialog } from "../components/ui/ConfirmDialog";
import { useData } from "../hooks/useData";
import { relativeTime } from "../lib/format";

type SettingsTab = "general" | "currencies" | "pricing" | "sync" | "backups" | "security" | "data" | "tax" | "advanced";
type SettingsPatch = Partial<Omit<AppSettings, "evidence_retention_policy">>;

interface SettingsStatus {
  database: string;
  price_provider_api_key_configured: boolean;
  backup_encryption_configured: boolean;
  application_secret_configured: boolean;
  evidence_retention: string;
  supported_price_providers: string[];
}

const TABS: Array<{ id: SettingsTab; label: string; icon: React.ReactNode }> = [
  { id: "general", label: "General", icon: <Settings2 size={15} /> },
  { id: "currencies", label: "Currencies", icon: <BadgeDollarSign size={15} /> },
  { id: "pricing", label: "Price providers", icon: <Waypoints size={15} /> },
  { id: "sync", label: "Synchronization", icon: <RefreshCw size={15} /> },
  { id: "backups", label: "Backups", icon: <HardDrive size={15} /> },
  { id: "security", label: "Security", icon: <KeyRound size={15} /> },
  { id: "data", label: "Data", icon: <Database size={15} /> },
  { id: "tax", label: "Tax integrations", icon: <Languages size={15} /> },
  { id: "advanced", label: "Advanced", icon: <SlidersHorizontal size={15} /> },
];

interface SettingsPageProps { navigate: (page: Page) => void; }
interface SectionProps { settings: AppSettings; save: (patch: SettingsPatch) => Promise<void>; navigate: (page: Page) => void; }

export function SettingsPage({ navigate }: SettingsPageProps) {
  const [tab, setTab] = useState<SettingsTab>("general");
  const { data: settings, error, refresh, setData } = useData<AppSettings>(() => getJson("/api/settings"), []);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [actionError, setActionError] = useState("");
  const { confirm, confirmDialog } = useConfirmDialog();

  const save = async (patch: SettingsPatch) => {
    setSaving(true); setFeedback(""); setActionError("");
    try {
      const next = await patchJson<AppSettings>("/api/settings", patch);
      setData(next);
      window.dispatchEvent(new CustomEvent("crypto-ledger:settings-changed", { detail: next }));
      setFeedback("Settings saved.");
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Could not save settings");
    } finally { setSaving(false); }
  };

  const reset = async () => {
    if (!(await confirm({ title: "Restore default settings?", message: "App-wide preferences will be reset. Linked accounts, source credentials, backups, and ledger data will not be changed.", confirmLabel: "Restore defaults", destructive: true }))) return;
    setSaving(true); setFeedback(""); setActionError("");
    try {
      const next = await postJson<AppSettings>("/api/settings/reset", {});
      setData(next);
      window.dispatchEvent(new CustomEvent("crypto-ledger:settings-changed", { detail: next }));
      setFeedback("Settings restored to defaults.");
    } catch (reason) { setActionError(reason instanceof Error ? reason.message : "Could not reset settings"); }
    finally { setSaving(false); }
  };

  if (!settings) return <Card><p className="text-sm text-soft">Loading settings…</p></Card>;
  const props: SectionProps = { settings, save, navigate };
  return <div className="space-y-6 animate-fade-in">
    <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between"><div><p className="font-mono text-[10px] uppercase tracking-widest text-faint">Application preferences</p><h2 className="mt-1 text-xl font-semibold tracking-tight text-ink">Settings</h2><p className="mt-1 max-w-2xl text-sm text-soft">Configure app-wide behavior here. Individual wallets and exchange credentials remain in Linked Accounts.</p></div><Button size="sm" variant="ghost" icon={<TimerReset size={14} />} loading={saving} onClick={() => void reset()}>Restore defaults</Button></div>
    {(error || actionError || feedback) && <p className={error || actionError ? "rounded-lg bg-bad-soft px-3 py-2 text-xs text-bad" : "rounded-lg bg-good-soft px-3 py-2 text-xs text-good"}>{error || actionError || feedback}</p>}
    <div className="grid gap-5 lg:grid-cols-[220px_minmax(0,1fr)] lg:items-start"><SettingsNavigator active={tab} onChange={setTab} /><div className="min-w-0">{tab === "general" && <GeneralSection {...props} />}{tab === "currencies" && <CurrenciesSection {...props} />}{tab === "pricing" && <PricingSection {...props} />}{tab === "sync" && <SyncSection {...props} />}{tab === "backups" && <BackupSection {...props} />}{tab === "security" && <SecuritySection />}{tab === "data" && <DataSection />}{tab === "tax" && <TaxSection {...props} />}{tab === "advanced" && <AdvancedSection {...props} refresh={refresh} />}</div></div>
    {confirmDialog}
  </div>;
}

function SettingsNavigator({ active, onChange }: { active: SettingsTab; onChange: (tab: SettingsTab) => void }) {
  const [open, setOpen] = useState(false);
  const selected = TABS.find((item) => item.id === active) ?? TABS[0];
  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [open]);
  return <><div className="relative lg:hidden"><p className="mb-2 px-1 text-xs font-semibold text-ink">Settings section</p><button type="button" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((value) => !value)} className="flex h-11 w-full items-center gap-3 rounded-xl border border-line bg-surface px-3 text-left shadow-sm transition-colors hover:border-line-strong"><span className="grid size-7 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent">{selected.icon}</span><span className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">{selected.label}</span><ChevronDown size={17} className={open ? "rotate-180 text-accent transition-transform" : "text-soft transition-transform"} /></button>{open && <div role="listbox" aria-label="Settings sections" className="absolute inset-x-0 top-full z-30 mt-2 max-h-[min(65dvh,26rem)] overflow-y-auto rounded-xl border border-line-strong bg-surface p-1.5 shadow-modal animate-fade-in">{TABS.map((item) => <button type="button" role="option" aria-selected={active === item.id} key={item.id} onClick={() => { onChange(item.id); setOpen(false); }} className={active === item.id ? "flex w-full items-center gap-3 rounded-lg bg-accent-soft px-3 py-2.5 text-left text-sm font-semibold text-accent" : "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm text-soft hover:bg-base hover:text-ink"}><span className={active === item.id ? "text-accent" : "text-faint"}>{item.icon}</span><span className="min-w-0 flex-1">{item.label}</span>{active === item.id && <Check size={16} />}</button>)}</div>}</div><nav className="hidden rounded-card border border-line bg-surface p-2 lg:block" aria-label="Settings sections">{TABS.map((item) => <button key={item.id} onClick={() => onChange(item.id)} className={active === item.id ? "flex w-full items-center gap-2.5 rounded-lg bg-accent-soft px-3 py-2.5 text-left text-sm font-semibold text-accent" : "flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm text-soft hover:bg-base hover:text-ink"}>{item.icon}{item.label}</button>)}</nav></>;
}

function GeneralSection({ settings, save }: SectionProps) {
  const [theme, setTheme] = useState(settings.ui_theme);
  const [timezone, setTimezone] = useState(settings.default_timezone);
  const [minimumActivityValue, setMinimumActivityValue] = useState(String(settings.minimum_activity_value));
  const [minimumActivityCurrency, setMinimumActivityCurrency] = useState(settings.minimum_activity_currency);
  useEffect(() => { setTheme(settings.ui_theme); setTimezone(settings.default_timezone); setMinimumActivityValue(String(settings.minimum_activity_value)); setMinimumActivityCurrency(settings.minimum_activity_currency); }, [settings.ui_theme, settings.default_timezone, settings.minimum_activity_value, settings.minimum_activity_currency]);
  const applyTheme = (value: AppSettings["ui_theme"], selectedTimezone: string) => {
    const resolved = value === "system" ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : value;
    document.documentElement.dataset.theme = resolved; document.documentElement.style.colorScheme = resolved; window.localStorage.setItem("crypto-ledger:theme", resolved); window.localStorage.setItem("crypto-ledger:timezone", selectedTimezone);
  };
  return <div className="grid max-w-3xl gap-4 lg:grid-cols-2"><Card><CardHeader eyebrow="Appearance" title="Display preferences" subtitle="These defaults travel with this self-hosted app." /><div className="grid gap-3"><Field label="Theme" htmlFor="theme"><Select id="theme" value={theme} onChange={(event) => setTheme(event.target.value as AppSettings["ui_theme"])}><option value="system">Follow system</option><option value="light">Light</option><option value="dark">Dark</option></Select></Field><Field label="Timezone" htmlFor="timezone" hint="Used for timestamps throughout the app. Use an IANA value, for example Europe/Stockholm."><Input id="timezone" value={timezone} onChange={(event) => setTimezone(event.target.value)} placeholder="Europe/Stockholm" /></Field><div className="grid gap-3 sm:grid-cols-2"><Field label="Minimum activity value" htmlFor="minimum-activity-value" hint={`Activities below this amount are hidden. Unpriced events stay visible.`}><Input id="minimum-activity-value" type="number" min="0" step="0.01" value={minimumActivityValue} onChange={(event) => setMinimumActivityValue(event.target.value)} /></Field><Field label="Threshold currency" htmlFor="minimum-activity-currency" hint="Choose one of the configured valuation currencies."><Select id="minimum-activity-currency" value={minimumActivityCurrency} onChange={(event) => setMinimumActivityCurrency(event.target.value)}>{settings.valuation_currencies.map((currency) => <option key={currency} value={currency}>{currency}</option>)}</Select></Field></div><Button size="sm" variant="primary" onClick={() => { applyTheme(theme, timezone); void save({ ui_theme: theme, default_timezone: timezone, minimum_activity_value: Number(minimumActivityValue), minimum_activity_currency: minimumActivityCurrency }); }}>Save display preferences</Button></div></Card><Card><CardHeader eyebrow="Scope" title="What belongs where" subtitle="Keeps global configuration separate from financial-source data." /><div className="space-y-3 text-xs text-soft"><p><span className="font-semibold text-ink">Settings</span> controls app-wide behavior, schedules, providers, reporting, and recovery policy.</p><p><span className="font-semibold text-ink">Activity filter</span> hides only tiny priced events from the interface; it never deletes canonical ledger evidence or exports.</p><p><span className="font-semibold text-ink">Linked Accounts</span> controls each wallet, exchange, address, network, and encrypted credential.</p><p><span className="font-semibold text-ink">Deployment</span> owns database paths, encryption keys, and host/network exposure. Those are deliberately not editable here.</p></div></Card></div>;
}

function CurrenciesSection({ settings, save }: SectionProps) {
  const [displayCurrency, setDisplayCurrency] = useState(settings.display_currency);
  const [valuationCurrencies, setValuationCurrencies] = useState(settings.valuation_currencies.join(", "));
  const [newCurrency, setNewCurrency] = useState("");
  useEffect(() => { setDisplayCurrency(settings.display_currency); setValuationCurrencies(settings.valuation_currencies.join(", ")); }, [settings.display_currency, settings.valuation_currencies]);
  const currencies = Array.from(new Set(valuationCurrencies.split(",").map((value) => value.trim().toUpperCase()).filter(Boolean)));
  const addCurrency = () => { const next = newCurrency.trim().toUpperCase(); if (/^[A-Z]{3}$/.test(next) && !currencies.includes(next)) setValuationCurrencies([...currencies, next].join(", ")); setNewCurrency(""); };
  return <div className="max-w-2xl space-y-4"><Card><CardHeader eyebrow="Valuation" title="Currencies" subtitle="Every new event is valued in the configured quote currencies." /><div className="grid gap-3 sm:grid-cols-2"><Field label="Display currency" htmlFor="display-currency"><Select id="display-currency" value={displayCurrency} onChange={(event) => setDisplayCurrency(event.target.value)}>{currencies.map((currency) => <option key={currency} value={currency}>{currency}</option>)}</Select></Field><Field label="Add valuation currency" htmlFor="new-currency" hint="Use a three-letter ISO code."><div className="flex gap-2"><Input id="new-currency" value={newCurrency} onChange={(event) => setNewCurrency(event.target.value.toUpperCase())} placeholder="USD" maxLength={3} /><Button size="sm" variant="secondary" icon={<Plus size={14} />} onClick={addCurrency}>Add</Button></div></Field></div><div className="mt-4 flex flex-wrap items-center gap-2">{currencies.map((currency) => <Badge key={currency} tone={currency === displayCurrency ? "info" : "neutral"}>{currency}{!(["EUR", "SEK"] as string[]).includes(currency) && <button aria-label={`Remove ${currency}`} className="ml-1" onClick={() => setValuationCurrencies(currencies.filter((item) => item !== currency).join(", "))}>×</button>}</Badge>)}</div><Button className="mt-4" size="sm" variant="primary" onClick={() => void save({ display_currency: currencies.includes(displayCurrency) ? displayCurrency : currencies[0], valuation_currencies: currencies })}>Save currencies</Button></Card><Card><CardHeader eyebrow="Integrity" title="Required history" subtitle="EUR and SEK cannot be removed while the current ledger and tax adapters require them." /><p className="text-xs text-soft">Additional currencies are collected for new valuations. Existing historical valuations remain intact; use a valuation refresh/backfill to populate a newly added currency for older events.</p></Card></div>;
}

function PricingSection({ settings, save }: SectionProps) {
  const { data: status } = useData<SettingsStatus>(() => getJson("/api/settings/status"), []);
  const [provider, setProvider] = useState(settings.price_provider);
  const [timeout, setTimeoutValue] = useState(String(settings.price_timeout_seconds));
  const [apiKey, setApiKey] = useState(""); const [keyFeedback, setKeyFeedback] = useState("");
  useEffect(() => { setProvider(settings.price_provider); setTimeoutValue(String(settings.price_timeout_seconds)); }, [settings.price_provider, settings.price_timeout_seconds]);
  const storeKey = async () => { if (!apiKey.trim()) return; await postJson("/api/settings/secrets/price-provider-key", { value: apiKey }); setApiKey(""); setKeyFeedback("Encrypted API key saved."); };
  return <div className="max-w-3xl space-y-4"><Card><CardHeader eyebrow="Market data" title="Price provider" subtitle="Provider responses are cached locally with their provenance." /><div className="grid gap-3 sm:grid-cols-2"><Field label="Active provider" htmlFor="price-provider"><Select id="price-provider" value={provider} onChange={(event) => setProvider(event.target.value)}>{(status?.supported_price_providers ?? ["coingecko"]).map((item) => <option key={item} value={item}>{item === "coingecko" ? "CoinGecko" : item}</option>)}</Select></Field><Field label="Request timeout (seconds)" htmlFor="price-timeout"><Input id="price-timeout" type="number" min="3" max="60" value={timeout} onChange={(event) => setTimeoutValue(event.target.value)} /></Field></div><div className="mt-4 flex flex-wrap items-center gap-2"><Button size="sm" variant="primary" onClick={() => void save({ price_provider: provider, price_timeout_seconds: Number(timeout) })}>Save price settings</Button><Badge tone={settings.price_provider_api_key_configured ? "success" : "neutral"} dot>{settings.price_provider_api_key_configured ? "API key configured" : "Public API mode"}</Badge></div></Card><Card><CardHeader eyebrow="Provider credentials" title="CoinGecko API key" subtitle="A key saved here is encrypted using the host-managed application key." /><div className="flex flex-col gap-3 sm:flex-row sm:items-end"><Field className="flex-1" label="API key" htmlFor="provider-api-key"><Input id="provider-api-key" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={settings.price_provider_api_key_configured ? "Replace existing key" : "Paste API key"} /></Field><Button size="sm" variant="secondary" onClick={() => void storeKey()}>Save encrypted key</Button></div>{keyFeedback && <p className="mt-3 text-xs text-good">{keyFeedback}</p>}<p className="mt-3 text-xs text-soft">Reveal or permanently delete this app-managed key in Security. CoinGecko credentials are never read from environment variables.</p></Card></div>;
}

function SyncSection({ settings, save, navigate }: SectionProps) {
  const { data: accounts, refresh: refreshAccounts } = useData<Account[]>(() => getJson("/api/accounts"), []);
  const initialUnit = settings.sync_interval_minutes % 1440 === 0 ? "days" : settings.sync_interval_minutes % 60 === 0 ? "hours" : "minutes";
  const initialValue = settings.sync_interval_minutes / (initialUnit === "days" ? 1440 : initialUnit === "hours" ? 60 : 1);
  const [enabled, setEnabled] = useState(settings.sync_enabled); const [interval, setInterval] = useState(String(initialValue)); const [unit, setUnit] = useState<"minutes" | "hours" | "days">(initialUnit); const [runningAll, setRunningAll] = useState(false); const [feedback, setFeedback] = useState("");
  useEffect(() => { const nextUnit = settings.sync_interval_minutes % 1440 === 0 ? "days" : settings.sync_interval_minutes % 60 === 0 ? "hours" : "minutes"; setEnabled(settings.sync_enabled); setUnit(nextUnit); setInterval(String(settings.sync_interval_minutes / (nextUnit === "days" ? 1440 : nextUnit === "hours" ? 60 : 1))); }, [settings.sync_enabled, settings.sync_interval_minutes]);
  const connections = (accounts ?? []).filter((account) => account.syncable || account.connector_type === "exchange_import");
  const runAll = async () => { setRunningAll(true); setFeedback(""); try { const results = await Promise.all((accounts ?? []).filter((account) => account.syncable).map((account) => postJson<SyncResult>(`/api/accounts/${account.id}/sync`, {}).catch(() => null))); const imported = results.reduce((total, item) => total + (item?.imported ?? 0), 0); setFeedback(`Checked ${results.length} source${results.length === 1 ? "" : "s"}; ${imported} new event${imported === 1 ? "" : "s"}.`); await refreshAccounts(); } finally { setRunningAll(false); } };
  const minutes = Number(interval) * (unit === "days" ? 1440 : unit === "hours" ? 60 : 1);
  return <div className="space-y-4"><Card className="max-w-3xl"><CardHeader eyebrow="Scheduler" title="Automatic synchronization" subtitle="Controls background checks only; manual Sync and Backfill remain available per source." /><div className="grid gap-3 sm:grid-cols-[1fr_220px] sm:items-end"><Toggle label="Enable scheduled synchronization" checked={enabled} onChange={setEnabled} /><Field label="Check every" htmlFor="sync-interval"><div className="flex gap-2"><Input id="sync-interval" type="number" min="1" max={unit === "days" ? "30" : unit === "hours" ? "24" : "1440"} value={interval} onChange={(event) => setInterval(event.target.value)} /><Select value={unit} onChange={(event) => setUnit(event.target.value as typeof unit)}><option value="minutes">m</option><option value="hours">h</option><option value="days">d</option></Select></div></Field></div><div className="mt-4 flex flex-wrap gap-2"><Button size="sm" variant="primary" onClick={() => void save({ sync_enabled: enabled, sync_interval_minutes: minutes })}>Save synchronization</Button><Button size="sm" variant="secondary" icon={<RefreshCw size={14} />} loading={runningAll} onClick={() => void runAll()}>Run all now</Button><Button size="sm" variant="ghost" onClick={() => navigate("Linked Accounts")}>Manage linked accounts →</Button></div>{feedback && <p className="mt-3 text-xs text-good">{feedback}</p>}</Card><Card><CardHeader eyebrow="Sources" title="Connected exchanges & nodes" subtitle="Each source retains its own schedule-independent status and configuration." />{connections.length === 0 ? <p className="text-xs text-soft">No syncable sources connected yet.</p> : <div className="space-y-2">{connections.map((account) => <div key={account.id} className="flex items-center justify-between rounded-lg border border-line px-3 py-2.5 text-xs"><div className="min-w-0"><p className="font-semibold text-ink">{account.name}</p><p className="mt-0.5 truncate font-mono text-[11px] text-faint">{connectorTypeMeta(account.connector_type).label} · {connectorSummary(account) ?? "—"}</p></div><div className="flex shrink-0 items-center gap-2.5"><span className="text-[11px] text-faint">{account.last_sync ? relativeTime(account.last_sync) : "Never synced"}</span><StatusPill status={account.status} /></div></div>)}</div>}</Card></div>;
}

function BackupSection({ settings, save }: SectionProps) {
  const [hour, setHour] = useState(String(settings.backup_hour_utc)); const [verifyAfterCreate, setVerifyAfterCreate] = useState(settings.backup_verify_after_create); const [daily, setDaily] = useState(String(settings.backup_retention_daily)); const [weekly, setWeekly] = useState(String(settings.backup_retention_weekly)); const [monthly, setMonthly] = useState(String(settings.backup_retention_monthly));
  useEffect(() => { setHour(String(settings.backup_hour_utc)); setVerifyAfterCreate(settings.backup_verify_after_create); setDaily(String(settings.backup_retention_daily)); setWeekly(String(settings.backup_retention_weekly)); setMonthly(String(settings.backup_retention_monthly)); }, [settings]);
  return <div className="max-w-3xl space-y-4"><BackupStatus detailed /><Card><CardHeader eyebrow="Policy" title="Automatic backup policy" subtitle="The app keeps encrypted SQLite snapshots; retention is applied after each automatic or uploaded backup." /><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"><Field label="Daily snapshots" htmlFor="daily"><Input id="daily" type="number" min="1" max="365" value={daily} onChange={(event) => setDaily(event.target.value)} /></Field><Field label="Weekly snapshots" htmlFor="weekly"><Input id="weekly" type="number" min="1" max="104" value={weekly} onChange={(event) => setWeekly(event.target.value)} /></Field><Field label="Monthly snapshots" htmlFor="monthly"><Input id="monthly" type="number" min="1" max="120" value={monthly} onChange={(event) => setMonthly(event.target.value)} /></Field><Field label="Run after (UTC hour)" htmlFor="backup-hour"><Select id="backup-hour" value={hour} onChange={(event) => setHour(event.target.value)}>{Array.from({ length: 24 }, (_, value) => <option key={value} value={value}>{String(value).padStart(2, "0")}:00 UTC</option>)}</Select></Field><div className="sm:col-span-2"><Toggle label="Verify each newly created backup" hint="Decrypts and checks SQLite integrity before marking it verified." checked={verifyAfterCreate} onChange={setVerifyAfterCreate} /></div></div><Button className="mt-4" size="sm" variant="primary" onClick={() => void save({ backup_hour_utc: Number(hour), backup_verify_after_create: verifyAfterCreate, backup_retention_daily: Number(daily), backup_retention_weekly: Number(weekly), backup_retention_monthly: Number(monthly) })}>Save backup policy</Button></Card></div>;
}

function SecuritySection() {
  const { data: status } = useData<SettingsStatus>(() => getJson("/api/settings/status"), []);
  const { data: secrets, refresh } = useData<SecretInventoryItem[]>(() => getJson("/api/settings/secrets"), []);
  const { confirm, confirmDialog } = useConfirmDialog();
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [feedback, setFeedback] = useState("");
  const reveal = async (item: SecretInventoryItem) => {
    if (!(await confirm({ title: "Reveal secret?", message: "Reveal " + item.label + "? This is dangerous: anyone viewing this screen will be able to read it.", confirmLabel: "Reveal secret" }))) return;
    const result = await postJson<{ value: string }>("/api/settings/secrets/" + item.id + "/reveal", { confirmed: true });
    setRevealed((values) => ({ ...values, [item.id]: result.value }));
  };
  const beginEdit = async (item: SecretInventoryItem) => {
    if (!(await confirm({ title: "Edit secret?", message: "Open " + item.label + " for editing? This is dangerous: replacing it may disconnect a source.", confirmLabel: "Open editor" }))) return;
    const result = await postJson<{ value: string }>("/api/settings/secrets/" + item.id + "/reveal", { confirmed: true });
    setRevealed((values) => ({ ...values, [item.id]: result.value }));
    setEditValues((values) => ({ ...values, [item.id]: result.value }));
    setEditing(item.id);
  };
  const saveEdit = async (item: SecretInventoryItem) => {
    if (!(await confirm({ title: "Replace secret?", message: "Replace " + item.label + "? The old value will be overwritten.", confirmLabel: "Replace secret", destructive: true }))) return;
    await patchJson("/api/settings/secrets/" + item.id, { value: editValues[item.id] ?? "" });
    setRevealed((values) => ({ ...values, [item.id]: editValues[item.id] ?? "" }));
    setEditing(null);
    setFeedback("Secret updated.");
    await refresh();
  };
  const remove = async (item: SecretInventoryItem) => {
    if (!(await confirm({ title: "Delete secret permanently?", message: "Permanently delete " + item.label + "? This cannot be undone and may disconnect a source.", confirmLabel: "Delete permanently", destructive: true }))) return;
    await deleteJson("/api/settings/secrets/" + item.id, { confirmed: true });
    setRevealed((values) => { const next = { ...values }; delete next[item.id]; return next; });
    setEditing(null);
    setFeedback("Secret permanently deleted.");
    await refresh();
  };
  return <div className="max-w-3xl space-y-4">
    <Card><CardHeader eyebrow="Credentials" title="Secret storage" subtitle="Stored credentials are encrypted at rest. Values are hidden until you explicitly confirm a reveal." /><div className="grid gap-2.5 text-xs sm:grid-cols-2"><SecurityRule ok={Boolean(status?.application_secret_configured)} label="Application encryption key configured" /><SecurityRule ok={Boolean(status?.backup_encryption_configured)} label="Backup encryption configured" /><SecurityRule ok label="Source credentials encrypted at rest" /><SecurityRule ok label="No seed phrases or private keys stored" /></div></Card>
    <Card><CardHeader eyebrow="Secret inventory" title="Keys and credentials" subtitle="Use the eye to reveal a value after a danger warning. App-managed values can also be replaced or deleted." />{feedback && <p className="mb-3 text-xs text-good">{feedback}</p>}<div className="space-y-2">{(secrets ?? []).map((item) => <div key={item.id} className="rounded-lg border border-line px-3 py-3"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs font-semibold text-ink">{item.label}</p><p className="mt-0.5 text-[11px] text-faint">{item.location} · {item.configured ? "Configured" : "Not configured"}</p></div><div className="flex gap-2">{item.revealable && <Button size="sm" variant="ghost" icon={revealed[item.id] ? <EyeOff size={13} /> : <Eye size={13} />} onClick={() => revealed[item.id] ? setRevealed((values) => { const next = { ...values }; delete next[item.id]; return next; }) : void reveal(item)}>{revealed[item.id] ? "Hide" : "Reveal"}</Button>}{item.editable && <Button size="sm" variant="ghost" icon={<Pencil size={13} />} onClick={() => editing === item.id ? setEditing(null) : void beginEdit(item)}>{editing === item.id ? "Cancel" : "Edit"}</Button>}{item.deletable && <Button size="sm" variant="ghost" icon={<Trash2 size={13} />} onClick={() => void remove(item)}>Delete</Button>}</div></div>{revealed[item.id] && (editing === item.id ? <div className="mt-3 flex gap-2"><Input aria-label={"Edit " + item.label} type="text" value={editValues[item.id] ?? ""} onChange={(event) => setEditValues((values) => ({ ...values, [item.id]: event.target.value }))} /><Button size="sm" variant="primary" onClick={() => void saveEdit(item)}>Save</Button></div> : <p className="mt-3 break-all rounded-md bg-warn-soft px-3 py-2 font-mono text-xs text-ink">{revealed[item.id]}</p>)}</div>)}</div></Card>
    <Card><CardHeader eyebrow="Host boundary" title="Master keys" subtitle="Application and backup master keys can be viewed after confirmation, but are not editable or deletable here." /><p className="text-xs text-soft">They come from the deployment environment and protect encrypted account credentials, attachments, and backups. Rotate or remove them through the host secret manager only after backing up and re-encrypting data.</p></Card>
    {confirmDialog}
  </div>;
}

function DataSection() { return <div className="max-w-3xl space-y-4"><Card><CardHeader eyebrow="Portability" title="Exports and evidence" subtitle="Your financial history remains portable even if this app is no longer maintained." /><div className="flex flex-wrap gap-2.5"><Button size="sm" icon={<FileText size={14} />} onClick={() => triggerDownload("/api/export/ledger.csv")}>Full ledger CSV</Button><Button size="sm" icon={<Archive size={14} />} onClick={() => triggerDownload("/api/export/evidence.zip")}>Evidence archive</Button></div></Card><Card><CardHeader eyebrow="Retention" title="Evidence is retained indefinitely" subtitle="Raw evidence and canonical history are not automatically purged." /><p className="text-xs text-soft">This is an intentional ledger invariant, not a configurable deletion schedule. Sources can be archived, but archived history remains auditable and recoverable.</p></Card></div>; }

function TaxSection({ settings, save, navigate }: SectionProps) {
  const { data: countries } = useData<TaxCountry[]>(() => getJson("/api/tax/countries"), []); const { data: languages } = useData<TaxLanguage[]>(() => getJson("/api/tax/languages"), []);
  const [country, setCountry] = useState(settings.default_country ?? ""); const [year, setYear] = useState(String(settings.default_tax_year ?? new Date().getFullYear())); const [name, setName] = useState(settings.taxpayer_name ?? ""); const [language, setLanguage] = useState(settings.default_language ?? "en"); const [plugins, setPlugins] = useState(settings.rp2_plugins.join(", "));
  useEffect(() => { setCountry(settings.default_country ?? ""); setYear(String(settings.default_tax_year ?? new Date().getFullYear())); setName(settings.taxpayer_name ?? ""); setLanguage(settings.default_language ?? "en"); setPlugins(settings.rp2_plugins.join(", ")); }, [settings]);
  const pluginList = Array.from(new Set(plugins.split(",").map((value) => value.trim().toLowerCase()).filter(Boolean)));
  return <div className="max-w-3xl space-y-4"><Card><CardHeader eyebrow="Report defaults" title="Tax integrations" subtitle="These are defaults for Reports; choosing a different country or year never changes the canonical ledger." /><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Field label="Country" htmlFor="default-country"><Select id="default-country" value={country} onChange={(event) => setCountry(event.target.value)}><option value="">Choose on report</option>{(countries ?? []).map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}</Select></Field><Field label="Tax year" htmlFor="default-year"><Input id="default-year" type="number" min="2009" max="2100" value={year} onChange={(event) => setYear(event.target.value)} /></Field><Field label="Report language" htmlFor="default-language"><Select id="default-language" value={language} onChange={(event) => setLanguage(event.target.value)}>{(languages ?? [{ code: "en", default: true }]).map((item) => <option key={item.code} value={item.code}>{languageLabel(item.code)}</option>)}</Select></Field><Field label="Taxpayer name" htmlFor="taxpayer-name"><Input id="taxpayer-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Your name" /></Field></div><div className="mt-4 flex flex-wrap gap-2"><Button size="sm" variant="primary" onClick={() => void save({ default_country: country || null, default_tax_year: Number(year), default_language: language, taxpayer_name: name || null })}>Save tax defaults</Button><Button size="sm" variant="ghost" onClick={() => navigate("Reports")}>Open Reports →</Button></div></Card><Card><CardHeader eyebrow="General report" title="Jurisdiction-neutral ledger review" subtitle="A general report is available when no country-specific tax adapter applies." /><p className="text-xs text-soft">It documents balances, transfers, corrections, and the event schedule; it deliberately does not calculate filing figures.</p><Button className="mt-3" size="sm" variant="secondary" onClick={() => { void save({ default_country: "GENERAL" }); navigate("Reports"); }}>Use general report</Button></Card><Card><CardHeader eyebrow="RP2" title="Installed RP2 plug-ins" subtitle="Register commands provided by your deployment. Settings does not download or execute arbitrary packages." /><Field label="Plug-in commands" htmlFor="rp2-plugins" hint="Comma-separated command names, for example rp2_es."><Input id="rp2-plugins" value={plugins} onChange={(event) => setPlugins(event.target.value)} placeholder="rp2_es" /></Field><Button className="mt-3" size="sm" variant="primary" onClick={() => void save({ rp2_plugins: pluginList })}>Save RP2 plug-ins</Button><p className="mt-3 text-xs text-soft">A plug-in still needs its package and country adapter installed on the host before it can generate a report.</p></Card><Card><CardHeader eyebrow="RP2 setup" title="Install plug-ins and country adapters" subtitle="Use the terminal to install packages; use this page to register the commands the app may run." /><div className="space-y-3 text-xs text-soft"><p>RP2 country connections are deployment dependencies. A command such as <code className="rounded bg-base px-1.5 py-0.5 font-mono text-[11px] text-ink">rp2_es</code> must be installed and available beside the API Python environment before a tax report can use it.</p><ol className="list-decimal space-y-2 pl-5"><li>Open a terminal and change to the API directory, for example <code className="rounded bg-base px-1.5 py-0.5 font-mono text-[11px] text-ink">cd /path/to/your/ledger/api</code>.</li><li>Install the bundled dependencies with <code className="rounded bg-base px-1.5 py-0.5 font-mono text-[11px] text-ink">.venv/bin/pip install -r requirements.txt</code>, or install a trusted country plug-in package separately.</li><li>Verify the adapter with <code className="rounded bg-base px-1.5 py-0.5 font-mono text-[11px] text-ink">.venv/bin/rp2_es --help</code>.</li><li>Add <code className="rounded bg-base px-1.5 py-0.5 font-mono text-[11px] text-ink">rp2_es</code> (or the installed <code className="rounded bg-base px-1.5 py-0.5 font-mono text-[11px] text-ink">rp2_&lt;country&gt;</code> command) above and save the plug-in list.</li></ol><p>A new country needs both a trusted RP2-compatible command and a country adapter/registry entry in the API. Registering a command alone cannot add tax rules that the app does not know how to apply.</p></div></Card></div>;
}

function AdvancedSection({ settings, refresh }: SectionProps & { refresh: () => Promise<void> }) { const { data: status } = useData<SettingsStatus>(() => getJson("/api/settings/status"), []); return <div className="max-w-3xl space-y-4"><Card><CardHeader eyebrow="Effective configuration" title="Runtime diagnostics" subtitle="Read-only health information for the self-hosted deployment." /><dl className="grid gap-3 text-xs sm:grid-cols-2"><Diagnostic label="API endpoint" value={API_BASE} /><Diagnostic label="Database" value={status?.database ?? "SQLite"} /><Diagnostic label="Price provider" value={settings.price_provider} /><Diagnostic label="Valuation currencies" value={settings.valuation_currencies.join(", ")} /><Diagnostic label="Scheduled sync" value={settings.sync_enabled ? `Every ${settings.sync_interval_minutes} minutes` : "Paused"} /><Diagnostic label="Evidence retention" value="Indefinite (locked)" /></dl></Card><Card><CardHeader eyebrow="Safe recovery" title="Refresh configuration" subtitle="Reload the current persisted values if another tab or administrator updated them." action={<Button size="sm" variant="secondary" onClick={() => void refresh()}>Reload</Button>} /><p className="text-xs text-soft">Use Restore defaults at the top of this page to reset only app-wide preferences. It does not alter accounts, credentials, backups, or ledger events.</p></Card></div>; }

function Toggle({ label, hint, checked, onChange }: { label: string; hint?: string; checked: boolean; onChange: (checked: boolean) => void }) { return <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-line px-3 py-2.5 transition-colors hover:border-line-strong"><span className="relative inline-flex shrink-0"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="peer sr-only" /><span aria-hidden="true" className="h-6 w-11 rounded-full bg-base ring-1 ring-inset ring-line transition-colors peer-checked:bg-accent peer-focus-visible:ring-2 peer-focus-visible:ring-accent/40" /><span aria-hidden="true" className="pointer-events-none absolute left-1 top-1 size-4 rounded-full bg-surface shadow-sm transition-transform peer-checked:translate-x-5" /></span><span><span className="block text-xs font-semibold text-ink">{label}</span>{hint && <span className="mt-0.5 block text-[11px] text-faint">{hint}</span>}</span></label>; }
function SecurityRule({ label, ok }: { label: string; ok: boolean }) { return <div className="flex items-center gap-2.5 rounded-lg border border-line px-3 py-2.5"><span className={ok ? "grid size-5 place-items-center rounded-full bg-good-soft text-good" : "grid size-5 place-items-center rounded-full bg-warn-soft text-warn"}>{ok ? <ShieldCheck size={12} /> : "!"}</span><span>{label}</span></div>; }
function Diagnostic({ label, value }: { label: string; value: string }) { return <div className="rounded-lg border border-line px-3 py-2.5"><dt className="font-mono text-[10px] uppercase tracking-wider text-faint">{label}</dt><dd className="mt-1 break-all text-ink">{value}</dd></div>; }
function languageLabel(code: string): string { return ({ en: "English", es: "Español", sv: "Svenska" } as Record<string, string>)[code] ?? code; }

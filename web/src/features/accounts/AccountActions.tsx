import { useRef, useState } from "react";
import { Archive, ArchiveRestore, History, Pause, Pencil, Play, RefreshCw, Trash2, Upload } from "lucide-react";
import type { Account, Page, SyncResult } from "../../types";
import { Dialog } from "../../components/ui/Dialog";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Field, Input, Select, Textarea } from "../../components/ui/Field";
import { StatusPill } from "../../components/domain/StatusPill";
import { accountKindLabel } from "../../components/domain/SourceCard";
import { connectorSummary, connectorTypeMeta, EVM_CHAINS } from "./connectorTypes";
import { apiFetch, patchJson, postJson, uploadFile } from "../../lib/api";
import { relativeTime } from "../../lib/format";

interface AccountActionsProps {
  account: Account;
  onClose: () => void;
  onNavigate: (page: Page) => void;
  onChanged: () => void;
}

export function AccountActions({ account, onClose, onNavigate, onChanged }: AccountActionsProps) {
  const label = accountKindLabel(account.kind);
  const [editing, setEditing] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [backfilling, setBackfilling] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [working, setWorking] = useState(false);
  const [pendingAction, setPendingAction] = useState<"archive" | "unarchive" | "delete" | null>(null);
  const [feedback, setFeedback] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const isArchived = Boolean(account.archived_at);

  const describeSync = (result: SyncResult) => {
    if (result.status === "ok") return `${result.imported} new, ${result.skipped} already recorded.`;
    return result.message ?? "Sync could not complete.";
  };

  const sync = async () => {
    setSyncing(true);
    setFeedback(null);
    try {
      const result = await postJson<SyncResult>(`/api/accounts/${account.id}/sync`, {});
      setFeedback({ tone: result.status === "unavailable" ? "bad" : "good", text: `Synced — ${describeSync(result)}` });
      onChanged();
    } catch (reason) {
      setFeedback({ tone: "bad", text: reason instanceof Error ? reason.message : "Sync failed" });
    } finally {
      setSyncing(false);
    }
  };

  const backfill = async () => {
    setBackfilling(true);
    setFeedback(null);
    try {
      const result = await postJson<SyncResult>(`/api/accounts/${account.id}/backfill`, {});
      setFeedback({ tone: result.status === "unavailable" ? "bad" : "good", text: `Backfilled — ${describeSync(result)}` });
      onChanged();
    } catch (reason) {
      setFeedback({ tone: "bad", text: reason instanceof Error ? reason.message : "Backfill failed" });
    } finally {
      setBackfilling(false);
    }
  };

  const confirmPending = async () => {
    if (!pendingAction) return;
    setWorking(true);
    setFeedback(null);
    try {
      if (pendingAction === "archive") {
        await postJson(`/api/accounts/${account.id}/archive`, {});
      } else if (pendingAction === "unarchive") {
        await postJson(`/api/accounts/${account.id}/unarchive`, {});
      } else {
        await apiFetch(`/api/accounts/${account.id}?confirm=true`, { method: "DELETE" });
      }
      // The account object this dialog was opened with is now stale (its
      // status/archived_at changed underneath it) — close rather than show
      // outdated state, and let the refreshed list be the visible proof it
      // worked instead of a toast that closes with the dialog anyway.
      onChanged();
      onClose();
    } catch (reason) {
      setFeedback({ tone: "bad", text: reason instanceof Error ? reason.message : `Could not ${pendingAction}` });
      setWorking(false);
      setPendingAction(null);
    }
  };

  const togglePause = async () => {
    setWorking(true);
    setFeedback(null);
    try {
      await postJson(`/api/accounts/${account.id}/${account.paused ? "resume" : "pause"}`, {});
      onChanged();
      onClose();
    } catch (reason) {
      setFeedback({ tone: "bad", text: reason instanceof Error ? reason.message : "Could not update pause state" });
    } finally {
      setWorking(false);
    }
  };

  const uploadExport = async (file: File) => {
    setUploading(true);
    setFeedback(null);
    try {
      const result = await uploadFile<{ imported: number; skipped_duplicates: number }>("/api/import/bitget", file);
      setFeedback({ tone: "good", text: `Imported ${result.imported} events (${result.skipped_duplicates} already recorded).` });
      onChanged();
    } catch (reason) {
      setFeedback({ tone: "bad", text: reason instanceof Error ? reason.message : "Import failed" });
    } finally {
      setUploading(false);
    }
  };

  if (editing) {
    return <EditAccountDialog account={account} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); onChanged(); }} />;
  }

  const summary = connectorSummary(account);

  return (
    <Dialog
      open
      onClose={onClose}
      title={account.name}
      eyebrow={`${label} · ${connectorTypeMeta(account.connector_type).label}`}
      footer={
        <button
          onClick={() => {
            onClose();
            onNavigate("Reports");
          }}
          className="text-xs font-semibold text-accent hover:text-accent-hover"
        >
          View with reports →
        </button>
      }
    >
      <div className="space-y-5">
        <div className="flex items-center justify-between">
          <StatusPill status={isArchived ? "archived" : account.paused ? "paused" : account.status} />
          <Badge tone="neutral">{account.kind}</Badge>
        </div>

        {isArchived && (
          <p className="rounded-lg bg-base px-3 py-2.5 text-[11px] text-soft">
            Archived {account.archived_at ? relativeTime(account.archived_at) : ""} — hidden from Linked Accounts, but
            its {account.event_count} event{account.event_count === 1 ? "" : "s"} are still in your ledger. Restore
            it to sync again, or delete it permanently below.
          </p>
        )}
        {!isArchived && account.paused && (
          <p className="rounded-lg bg-warn-soft px-3 py-2.5 text-[11px] text-warn">
            Paused — still visible here and nothing is deleted, but automatic and manual syncing are both off until
            you resume it.
          </p>
        )}

        <dl className="grid grid-cols-2 gap-4 text-sm">
          {summary && (
            <div className="col-span-2">
              <dt className="text-[10px] font-medium uppercase tracking-wider text-faint">Connection</dt>
              <dd className="mt-1 break-all font-mono text-xs text-ink">{summary}</dd>
            </div>
          )}
          <div>
            <dt className="text-[10px] font-medium uppercase tracking-wider text-faint">Wallet software</dt>
            <dd className="mt-1 text-ink">{account.wallet_software || "—"}</dd>
          </div>
          <div>
            <dt className="text-[10px] font-medium uppercase tracking-wider text-faint">Last sync</dt>
            <dd className="mt-1 text-ink">{account.last_sync ? relativeTime(account.last_sync) : "Never"}</dd>
          </div>
          <div>
            <dt className="text-[10px] font-medium uppercase tracking-wider text-faint">Ledger events</dt>
            <dd className="mt-1 text-ink">{account.event_count}</dd>
          </div>
          <div className="col-span-2">
            <dt className="text-[10px] font-medium uppercase tracking-wider text-faint">Note</dt>
            <dd className="mt-1 text-soft">{account.note || "No source note recorded."}</dd>
          </div>
        </dl>

        <div>
          <p className="font-mono text-[10px] uppercase tracking-widest text-faint">Source actions</p>
          {!pendingAction && (
          <div className="mt-2.5 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {isArchived ? (
              <ActionButton icon={<ArchiveRestore size={16} />} label="Restore" onClick={() => setPendingAction("unarchive")} disabled={working} />
            ) : account.syncable ? (
              <>
                <ActionButton icon={<RefreshCw size={16} className={syncing ? "animate-spin" : ""} />} label="Sync now" onClick={() => void sync()} disabled={syncing || backfilling || account.paused} />
                <ActionButton icon={<History size={16} className={backfilling ? "animate-spin" : ""} />} label="Backfill" onClick={() => void backfill()} disabled={syncing || backfilling || account.paused} />
              </>
            ) : account.connector_type === "exchange_import" ? (
              <>
                <ActionButton icon={<Upload size={16} />} label="Upload export" onClick={() => fileInput.current?.click()} disabled={uploading || account.paused} />
                <input ref={fileInput} type="file" accept="application/json" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadExport(f); e.target.value = ""; }} />
              </>
            ) : (
              <ActionButton icon={<RefreshCw size={16} />} label="Sync" disabled />
            )}
            {!isArchived && <ActionButton icon={<Pencil size={16} />} label="Edit" onClick={() => setEditing(true)} />}
            {!isArchived && (
              <ActionButton
                icon={account.paused ? <Play size={16} /> : <Pause size={16} />}
                label={account.paused ? "Resume" : "Pause"}
                onClick={() => void togglePause()}
                disabled={working}
              />
            )}
            {!isArchived && <ActionButton icon={<Archive size={16} />} label="Archive" onClick={() => setPendingAction("archive")} disabled={working} />}
            <ActionButton icon={<Trash2 size={16} />} label="Delete" tone="danger" onClick={() => setPendingAction("delete")} disabled={working} />
          </div>
          )}

          {pendingAction && (
            <div className="mt-3 rounded-lg border border-line bg-base px-3.5 py-3">
              <p className="text-xs text-ink">
                {pendingAction === "archive" && "Archive this source? It stops syncing and is hidden from the list, but nothing is deleted — you can restore it any time."}
                {pendingAction === "unarchive" && "Restore this source to Linked Accounts?"}
                {pendingAction === "delete" && (
                  <>
                    Permanently delete <b>{account.name}</b> and its stored credentials? This can't be undone. Its{" "}
                    {account.event_count} ledger event{account.event_count === 1 ? "" : "s"} will be kept, marked as
                    coming from a removed source.
                  </>
                )}
              </p>
              <div className="mt-2.5 flex gap-2">
                <Button size="sm" variant="secondary" onClick={() => setPendingAction(null)} disabled={working}>
                  Cancel
                </Button>
                <Button
                  size="sm"
                  variant={pendingAction === "delete" ? "danger" : "primary"}
                  onClick={() => void confirmPending()}
                  loading={working}
                >
                  {pendingAction === "archive" ? "Archive" : pendingAction === "unarchive" ? "Restore" : "Delete permanently"}
                </Button>
              </div>
            </div>
          )}

          {feedback && (
            <p className={`mt-3 text-[11px] ${feedback.tone === "good" ? "text-good" : "text-bad"}`}>{feedback.text}</p>
          )}
          {!account.syncable && account.connector_type !== "exchange_import" && (
            <p className="mt-3 text-[11px] text-faint">
              This source doesn't support automatic sync yet — add or correct its history with manual entries.
            </p>
          )}
        </div>
      </div>
    </Dialog>
  );
}

function ActionButton({
  icon,
  label,
  onClick,
  disabled,
  tone = "default",
}: {
  icon: React.ReactNode;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  tone?: "default" | "danger";
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled || !onClick}
      className={
        tone === "danger"
          ? "flex flex-col items-center gap-1.5 rounded-lg border border-line py-3 text-soft transition-colors enabled:hover:border-bad enabled:hover:text-bad disabled:opacity-45"
          : "flex flex-col items-center gap-1.5 rounded-lg border border-line py-3 text-soft transition-colors enabled:hover:border-accent enabled:hover:text-accent disabled:opacity-45"
      }
    >
      {icon}
      <span className="text-[11px] font-medium">{label}</span>
    </button>
  );
}

const LIVE_EXCHANGE_TYPES = ["bitget_live", "binance_live"];

function EditAccountDialog({ account, onClose, onSaved }: { account: Account; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(account.name);
  const [walletSoftware, setWalletSoftware] = useState(account.wallet_software ?? "");
  const [note, setNote] = useState(account.note ?? "");
  const [address, setAddress] = useState(account.address ?? "");
  const [chainNetwork, setChainNetwork] = useState(account.chain_network ?? "ethereum");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [symbols, setSymbols] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const isLiveExchange = LIVE_EXCHANGE_TYPES.includes(account.connector_type);

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const body: Record<string, unknown> = { name, wallet_software: walletSoftware || null, note: note || null };
      if (account.address !== null || ["bitcoin_address", "evm_address", "solana_address"].includes(account.connector_type)) {
        body.address = address || null;
      }
      if (account.connector_type === "evm_address") {
        body.chain_network = chainNetwork;
      }
      if (isLiveExchange && apiKey.trim() && apiSecret.trim()) {
        body.config =
          account.connector_type === "bitget_live"
            ? { api_key: apiKey, api_secret: apiSecret, passphrase }
            : { api_key: apiKey, api_secret: apiSecret, symbols };
      }
      await patchJson(`/api/accounts/${account.id}`, body);
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save changes");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Edit ${account.name}`}
      eyebrow="Linked account"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => void save()} loading={saving}>
            Save changes
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        <Field label="Name" htmlFor="edit-name">
          <Input id="edit-name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        {["bitcoin_address", "evm_address", "solana_address"].includes(account.connector_type) && (
          <Field label="Address" htmlFor="edit-address" hint="Public address only.">
            <Input id="edit-address" value={address} onChange={(e) => setAddress(e.target.value)} />
          </Field>
        )}
        {account.connector_type === "evm_address" && (
          <Field label="Network" htmlFor="edit-chain">
            <Select id="edit-chain" value={chainNetwork} onChange={(e) => setChainNetwork(e.target.value)}>
              {EVM_CHAINS.map((chain) => (
                <option key={chain.id} value={chain.id}>
                  {chain.label}
                </option>
              ))}
            </Select>
          </Field>
        )}
        {isLiveExchange && (
          <div className="space-y-4 rounded-lg border border-line p-3.5">
            <p className="text-[11px] text-soft">
              Rotate the API key — leave blank to keep the current one. Both key and secret must be entered together.
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="New API key" htmlFor="edit-api-key">
                <Input id="edit-api-key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="Unchanged" />
              </Field>
              <Field label="New API secret" htmlFor="edit-api-secret">
                <Input id="edit-api-secret" type="password" value={apiSecret} onChange={(e) => setApiSecret(e.target.value)} placeholder="Unchanged" />
              </Field>
              {account.connector_type === "bitget_live" && (
                <Field label="Passphrase" htmlFor="edit-passphrase">
                  <Input id="edit-passphrase" type="password" value={passphrase} onChange={(e) => setPassphrase(e.target.value)} placeholder="Required if rotating" />
                </Field>
              )}
              {account.connector_type === "binance_live" && (
                <Field label="Trading pairs" htmlFor="edit-symbols" hint="Comma-separated, optional">
                  <Input id="edit-symbols" value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="BTCUSDT,ETHUSDT" />
                </Field>
              )}
            </div>
          </div>
        )}
        <Field label="Wallet software" htmlFor="edit-software">
          <Input id="edit-software" value={walletSoftware} onChange={(e) => setWalletSoftware(e.target.value)} placeholder="Optional" />
        </Field>
        <Field label="Note" htmlFor="edit-note">
          <Textarea id="edit-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional context" />
        </Field>
        {error && <p className="rounded-lg bg-bad-soft px-3 py-2 text-xs text-bad">{error}</p>}
      </div>
    </Dialog>
  );
}

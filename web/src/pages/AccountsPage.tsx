import { useState } from "react";
import { ChevronDown, ChevronUp, Plus, WalletCards } from "lucide-react";
import type { Account, ConnectorType, Page } from "../types";
import { getJson, postJson } from "../lib/api";
import { useData } from "../hooks/useData";
import { Card } from "../components/ui/Card";
import { SectionHeader } from "../components/ui/SectionHeader";
import { Button } from "../components/ui/Button";
import { Dialog } from "../components/ui/Dialog";
import { Field, Input, Select, Textarea } from "../components/ui/Field";
import { EmptyState, ErrorState } from "../components/ui/EmptyState";
import { Skeleton } from "../components/ui/Skeleton";
import { SourceCard } from "../components/domain/SourceCard";
import { AccountActions } from "../features/accounts/AccountActions";
import { CONNECTOR_TYPES, EVM_CHAINS } from "../features/accounts/connectorTypes";
import { cx } from "../lib/format";

type Kind = "exchange" | "wallet" | "manual" | "other";

const GROUP_ORDER: Kind[] = ["exchange", "wallet", "manual", "other"];
const groupLabels: Record<string, string> = { exchange: "Exchanges", wallet: "Wallets", manual: "Manual accounts", other: "Other" };

export function groupLabel(kind: Kind): string {
  return groupLabels[kind] ?? "Other";
}

interface AccountsPageProps {
  navigate: (page: Page) => void;
}

export function AccountsPage({ navigate }: AccountsPageProps) {
  const { data: accounts, loading, error, refresh } = useData<Account[]>(() => getJson("/api/accounts"), []);
  const [showAdd, setShowAdd] = useState(false);
  const [managing, setManaging] = useState<Account | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const {
    data: allAccounts,
    refresh: refreshArchived,
  } = useData<Account[]>(
    () => (showArchived ? getJson<Account[]>("/api/accounts?include_archived=true") : Promise.resolve([])),
    [showArchived],
    { initial: [] },
  );
  const archived = (allAccounts ?? []).filter((a) => a.archived_at);

  const refreshAll = () => {
    void refresh();
    if (showArchived) void refreshArchived();
  };

  const groups = (accounts ?? []).reduce<Record<string, Account[]>>((acc, account) => {
    const key = account.kind in groupLabels ? account.kind : "other";
    (acc[key] ||= []).push(account);
    return acc;
  }, {});

  return (
    <div className="space-y-8 animate-fade-in">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="max-w-xl text-sm text-soft">
            Every source contributes to one canonical ledger. Credentials and raw evidence remain separate from
            normalized events — and wallet software is never treated as ownership.
          </p>
        </div>
        <Button variant="primary" icon={<Plus size={16} />} onClick={() => setShowAdd(true)}>
          Add source
        </Button>
      </div>

      {error && <ErrorState message={error} />}

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Skeleton className="h-44" />
          <Skeleton className="h-44" />
        </div>
      ) : (accounts ?? []).length === 0 ? (
        <Card>
          <EmptyState
            icon={<WalletCards size={22} />}
            title="No sources linked"
            text="Register an exchange, wallet, or manual bucket to start building the ledger."
            action={
              <Button variant="primary" size="sm" icon={<Plus size={14} />} onClick={() => setShowAdd(true)}>
                Add your first source
              </Button>
            }
          />
        </Card>
      ) : (
        GROUP_ORDER.filter((kind) => (groups[kind] ?? []).length > 0).map((kind) => (
          <section key={kind}>
            <SectionHeader eyebrow="Linked accounts" title={groupLabel(kind)} />
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              {(groups[kind] ?? []).map((account) => (
                <SourceCard key={account.id} account={account} onManage={setManaging} />
              ))}
              {kind === "exchange" && (
                <button
                  onClick={() => setShowAdd(true)}
                  className="grid min-h-40 place-items-center gap-1.5 rounded-card border border-dashed border-line-strong text-soft transition-colors hover:border-accent hover:text-accent"
                >
                  <Plus size={20} />
                  <span className="text-sm font-semibold">Connect another source</span>
                  <span className={cx("text-[11px]")}>Exchange · wallet · manual</span>
                </button>
              )}
            </div>
          </section>
        ))
      )}

      <section>
        <button
          onClick={() => setShowArchived((v) => !v)}
          className="inline-flex items-center gap-1.5 text-xs font-semibold text-soft hover:text-ink"
        >
          {showArchived ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          Show archived sources
        </button>
        {showArchived && (
          <div className="mt-3">
            {archived.length === 0 ? (
              <p className="text-xs text-faint">No archived sources.</p>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                {archived.map((account) => (
                  <SourceCard key={account.id} account={account} onManage={setManaging} />
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      {showAdd && (
        <AddSourceDialog
          onClose={() => setShowAdd(false)}
          onCreated={() => {
            setShowAdd(false);
            refreshAll();
          }}
        />
      )}

      {managing && (
        <AccountActions
          account={managing}
          onClose={() => setManaging(null)}
          onNavigate={navigate}
          onChanged={refreshAll}
        />
      )}
    </div>
  );
}

interface AddSourceDialogProps {
  onClose: () => void;
  onCreated: () => void;
}

interface SourceForm {
  name: string;
  wallet_software: string;
  note: string;
  chain_network: string;
  address: string;
  host: string;
  port: string;
  username: string;
  password: string;
  macaroon: string;
  exchange_mode: "import" | "live";
  exchange_provider: "bitget" | "binance";
  api_key: string;
  api_secret: string;
  passphrase: string;
  symbols: string;
}

const EMPTY_FORM: SourceForm = {
  name: "",
  wallet_software: "",
  note: "",
  chain_network: "ethereum",
  address: "",
  host: "127.0.0.1",
  port: "18082",
  username: "",
  password: "",
  macaroon: "",
  exchange_mode: "live",
  exchange_provider: "bitget",
  api_key: "",
  api_secret: "",
  passphrase: "",
  symbols: "",
};

function AddSourceDialog({ onClose, onCreated }: AddSourceDialogProps) {
  const [type, setType] = useState<ConnectorType | null>(null);
  const [form, setForm] = useState<SourceForm>(EMPTY_FORM);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const change = (field: keyof SourceForm, value: string) => setForm((current) => ({ ...current, [field]: value }));

  const isLiveExchange = type === "exchange_import" && form.exchange_mode === "live";

  const save = async () => {
    if (!type) return;
    setSaving(true);
    setError("");
    try {
      const connectorType = isLiveExchange ? `${form.exchange_provider}_live` : type;
      const body: Record<string, unknown> = {
        name: form.name,
        connector_type: connectorType,
        wallet_software: form.wallet_software || null,
        note: form.note || null,
      };
      if (type === "bitcoin_address" || type === "solana_address") {
        body.address = form.address;
      }
      if (type === "evm_address") {
        body.address = form.address;
        body.chain_network = form.chain_network;
      }
      if (type === "monero_rpc") {
        body.config = { host: form.host, port: form.port, username: form.username || undefined, password: form.password || undefined };
      }
      if (type === "lightning_node") {
        body.config = { host: form.host, macaroon: form.macaroon };
      }
      if (isLiveExchange && form.exchange_provider === "bitget") {
        body.config = { api_key: form.api_key, api_secret: form.api_secret, passphrase: form.passphrase };
      }
      if (isLiveExchange && form.exchange_provider === "binance") {
        body.config = { api_key: form.api_key, api_secret: form.api_secret, symbols: form.symbols };
      }
      const created = await postJson<{ id: number }>("/api/accounts", body);
      // Live sources get an immediate deeper pull so history shows up right
      // away, instead of waiting for the next scheduled check.
      if (isLiveExchange || ["bitcoin_address", "evm_address", "solana_address"].includes(type)) {
        void postJson(`/api/accounts/${created.id}/backfill`, {});
      }
      onCreated();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create source");
    } finally {
      setSaving(false);
    }
  };

  const canSave =
    form.name.trim().length > 0 &&
    (type === "manual"
      ? true
      : type === "exchange_import"
        ? isLiveExchange
          ? form.api_key.trim().length > 0 &&
            form.api_secret.trim().length > 0 &&
            (form.exchange_provider !== "bitget" || form.passphrase.trim().length > 0)
          : true
        : type === "bitcoin_address" || type === "solana_address" || type === "evm_address"
          ? form.address.trim().length > 0
          : type === "monero_rpc"
            ? form.host.trim().length > 0 && form.port.trim().length > 0
            : type === "lightning_node"
              ? form.host.trim().length > 0 && form.macaroon.trim().length > 0
              : false);

  return (
    <Dialog
      open
      onClose={onClose}
      title={type ? connectorTitle(type) : "Add source"}
      eyebrow="Linked account"
      footer={
        type && (
          <>
            <Button variant="secondary" onClick={() => setType(null)}>
              Back
            </Button>
            <Button variant="primary" onClick={() => void save()} loading={saving} disabled={!canSave}>
              Register source
            </Button>
          </>
        )
      }
    >
      {!type ? (
        <div className="grid gap-2.5 sm:grid-cols-2">
          {CONNECTOR_TYPES.map((option) => (
            <button
              key={option.id}
              onClick={() => setType(option.id)}
              className="flex items-start gap-3 rounded-lg border border-line px-3.5 py-3 text-left transition-colors hover:border-accent hover:bg-accent-soft/40"
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent-soft text-sm font-bold text-accent">
                {option.glyph}
              </span>
              <span>
                <span className="block text-sm font-semibold text-ink">{option.label}</span>
                <span className="mt-0.5 block text-[11px] text-soft">{option.hint}</span>
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-xs text-soft">{CONNECTOR_TYPES.find((c) => c.id === type)?.hint}</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name" htmlFor="acc-name" className="sm:col-span-2">
              <Input
                id="acc-name"
                value={form.name}
                onChange={(e) => change("name", e.target.value)}
                placeholder={placeholderName(type)}
              />
            </Field>

            {type === "exchange_import" && (
              <div className="sm:col-span-2 space-y-4">
                <div className="inline-flex rounded-lg bg-base p-1">
                  {(["live", "import"] as const).map((mode) => (
                    <button
                      key={mode}
                      onClick={() => change("exchange_mode", mode)}
                      className={
                        form.exchange_mode === mode
                          ? "rounded-md bg-surface px-3 py-1.5 text-xs font-semibold text-ink shadow-sm"
                          : "rounded-md px-3 py-1.5 text-xs font-medium text-soft hover:text-ink"
                      }
                    >
                      {mode === "live" ? "Live API key" : "File import"}
                    </button>
                  ))}
                </div>

                {isLiveExchange && (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="Exchange" htmlFor="acc-exchange" className="sm:col-span-2">
                      <Select id="acc-exchange" value={form.exchange_provider} onChange={(e) => change("exchange_provider", e.target.value)}>
                        <option value="bitget">Bitget</option>
                        <option value="binance">Binance</option>
                      </Select>
                    </Field>
                    <Field label="API key" htmlFor="acc-api-key">
                      <Input id="acc-api-key" value={form.api_key} onChange={(e) => change("api_key", e.target.value)} placeholder="Read-only key" />
                    </Field>
                    <Field label="API secret" htmlFor="acc-api-secret">
                      <Input id="acc-api-secret" type="password" value={form.api_secret} onChange={(e) => change("api_secret", e.target.value)} placeholder="Secret" />
                    </Field>
                    {form.exchange_provider === "bitget" && (
                      <Field label="Passphrase" htmlFor="acc-passphrase">
                        <Input id="acc-passphrase" type="password" value={form.passphrase} onChange={(e) => change("passphrase", e.target.value)} placeholder="API passphrase" />
                      </Field>
                    )}
                    {form.exchange_provider === "binance" && (
                      <Field label="Trading pairs" htmlFor="acc-symbols" className="sm:col-span-2" hint="Optional, comma-separated (e.g. BTCUSDT,ETHUSDT) — Binance requires a symbol to pull trade history; deposits/withdrawals don't need this.">
                        <Input id="acc-symbols" value={form.symbols} onChange={(e) => change("symbols", e.target.value)} placeholder="BTCUSDT,ETHUSDT" />
                      </Field>
                    )}
                    <p className="sm:col-span-2 rounded-lg bg-warn-soft px-3 py-2.5 text-[11px] text-warn">
                      Use a <b>read-only</b> API key — never grant trading or withdrawal permissions. The key is
                      encrypted at rest and only ever used to read your history.
                    </p>
                  </div>
                )}
              </div>
            )}

            {type === "bitcoin_address" && (
              <Field
                label="Address or extended public key"
                htmlFor="acc-address"
                className="sm:col-span-2"
                hint="A single address, or an xpub/ypub/zpub to track the whole wallet (a fresh address per transaction is normal for HD wallets). Never a seed phrase or private key."
              >
                <Input id="acc-address" value={form.address} onChange={(e) => change("address", e.target.value)} placeholder="bc1… or zpub6…" />
              </Field>
            )}

            {type === "solana_address" && (
              <Field label="Address" htmlFor="acc-address" className="sm:col-span-2" hint="Public address only — never a seed phrase or private key.">
                <Input id="acc-address" value={form.address} onChange={(e) => change("address", e.target.value)} placeholder="Public address" />
              </Field>
            )}

            {type === "evm_address" && (
              <>
                <Field label="Address" htmlFor="acc-address" hint="Never a seed phrase or private key.">
                  <Input id="acc-address" value={form.address} onChange={(e) => change("address", e.target.value)} placeholder="0x…" />
                </Field>
                <Field label="Network" htmlFor="acc-chain">
                  <Select id="acc-chain" value={form.chain_network} onChange={(e) => change("chain_network", e.target.value)}>
                    {EVM_CHAINS.map((chain) => (
                      <option key={chain.id} value={chain.id}>
                        {chain.label}
                      </option>
                    ))}
                  </Select>
                </Field>
              </>
            )}

            {type === "monero_rpc" && (
              <>
                <Field label="Wallet-rpc host" htmlFor="acc-host">
                  <Input id="acc-host" value={form.host} onChange={(e) => change("host", e.target.value)} placeholder="127.0.0.1" />
                </Field>
                <Field label="Port" htmlFor="acc-port">
                  <Input id="acc-port" value={form.port} onChange={(e) => change("port", e.target.value)} placeholder="18082" />
                </Field>
                <Field label="RPC username" htmlFor="acc-user" hint="Optional">
                  <Input id="acc-user" value={form.username} onChange={(e) => change("username", e.target.value)} placeholder="Optional" />
                </Field>
                <Field label="RPC password" htmlFor="acc-pass" hint="Optional, stored encrypted">
                  <Input id="acc-pass" type="password" value={form.password} onChange={(e) => change("password", e.target.value)} placeholder="Optional" />
                </Field>
              </>
            )}

            {type === "lightning_node" && (
              <>
                <Field label="Node REST host" htmlFor="acc-host" className="sm:col-span-2" hint="e.g. https://localhost:8080">
                  <Input id="acc-host" value={form.host} onChange={(e) => change("host", e.target.value)} placeholder="https://localhost:8080" />
                </Field>
                <Field label="Macaroon (hex)" htmlFor="acc-macaroon" className="sm:col-span-2" hint="Stored encrypted. Read-only macaroon recommended.">
                  <Textarea id="acc-macaroon" rows={2} value={form.macaroon} onChange={(e) => change("macaroon", e.target.value)} placeholder="0201036c6e64…" />
                </Field>
              </>
            )}

            <Field label="Wallet software" htmlFor="acc-software" className="sm:col-span-2" hint="Interfaces are not ownership — MetaMask → Rabby is the same account.">
              <Input id="acc-software" value={form.wallet_software} onChange={(e) => change("wallet_software", e.target.value)} placeholder="Optional — Cake, Exodus, MetaMask…" />
            </Field>
            <Field label="Note" htmlFor="acc-note" className="sm:col-span-2">
              <Textarea id="acc-note" rows={2} value={form.note} onChange={(e) => change("note", e.target.value)} placeholder="Optional context" />
            </Field>
          </div>

          {type === "exchange_import" && !isLiveExchange && (
            <p className="rounded-lg bg-base px-3 py-2.5 text-[11px] text-soft">
              After registering, open the source and use <b>Upload export</b> to import trade/transaction history as
              JSON. No API keys are stored for this source type.
            </p>
          )}
          {isLiveExchange && (
            <p className="rounded-lg bg-base px-3 py-2.5 text-[11px] text-soft">
              After registering, this source pulls its recent history immediately, then keeps checking automatically
              on the interval set in Settings → Synchronization.
            </p>
          )}
          {(type === "monero_rpc" || type === "lightning_node") && (
            <p className="rounded-lg bg-warn-soft px-3 py-2.5 text-[11px] text-warn">
              {type === "monero_rpc"
                ? "Requires a monero-wallet-rpc daemon reachable at this host/port — ideally view-only."
                : "Requires your own LND node reachable at this host, with a macaroon that can read payments/invoices/channels."}
            </p>
          )}

          {error && <p className="rounded-lg bg-bad-soft px-3 py-2 text-xs text-bad">{error}</p>}
        </div>
      )}
    </Dialog>
  );
}

function connectorTitle(type: ConnectorType): string {
  return `Add ${CONNECTOR_TYPES.find((c) => c.id === type)?.label ?? "source"}`;
}

function placeholderName(type: ConnectorType): string {
  switch (type) {
    case "exchange_import":
      return "Bitget";
    case "bitcoin_address":
      return "Cold storage";
    case "evm_address":
      return "MetaMask · Ethereum";
    case "solana_address":
      return "Phantom";
    case "monero_rpc":
      return "Monero wallet";
    case "lightning_node":
      return "Lightning node";
    default:
      return "Cold wallet";
  }
}

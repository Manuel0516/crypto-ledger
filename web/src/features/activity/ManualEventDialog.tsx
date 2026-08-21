import { useEffect, useMemo, useState } from "react";
import { Upload } from "lucide-react";
import type { Account, EventDetail } from "../../types";
import { apiFetch, getJson, patchJson, postJson, putJson } from "../../lib/api";
import { useData } from "../../hooks/useData";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { Field, Input, Select } from "../../components/ui/Field";
import { MarketPriceButton } from "../../components/domain/MarketPriceButton";

const ACTIVITY_CHOICES = [
  { value: "DEPOSIT", label: "Deposit" },
  { value: "WITHDRAWAL", label: "Withdraw" },
  { value: "TRANSFER", label: "Transfer" },
  { value: "PAYMENT", label: "Payment" },
  { value: "DONATION", label: "Donation" },
  { value: "SWAP", label: "Swap" },
  { value: "MINING_REWARD", label: "Mining reward" },
  { value: "STAKING_REWARD", label: "Staking reward" },
  { value: "LIQUIDITY", label: "Liquidity" },
] as const;

type ActivityType = (typeof ACTIVITY_CHOICES)[number]["value"];

interface SimpleForm {
  event_type: ActivityType;
  symbol: string;
  asset_network: string;
  amount: string;
  secondary_symbol: string;
  secondary_amount: string;
  occurred_at: string;
  account_id: number | null;
  address_to: string;
  from_wallet: string;
  eur_value: string;
  sek_value: string;
  fee_asset: string;
  fee_amount: string;
  tx_hash: string;
}

const OUTGOING_TYPES = new Set<ActivityType>(["WITHDRAWAL", "PAYMENT", "DONATION", "SWAP", "LIQUIDITY"]);
const INCOMING_TYPES = new Set<ActivityType>(["DEPOSIT", "MINING_REWARD", "STAKING_REWARD"]);

function normalizeType(type: string): ActivityType {
  if (type === "BUY" || type === "SELL" || type === "CONVERT") return "SWAP";
  if (type === "SEND" || type === "BRIDGE_OUT") return "WITHDRAWAL";
  if (type === "RECEIVE" || type === "BRIDGE_IN" || type === "AIRDROP" || type === "INCOME") return "DEPOSIT";
  if (type === "LP_DEPOSIT" || type === "LP_WITHDRAWAL" || type === "LP_REWARD") return "LIQUIDITY";
  return ACTIVITY_CHOICES.some((choice) => choice.value === type) ? (type as ActivityType) : "DEPOSIT";
}

function normalizeSign(type: ActivityType, amount: string): string {
  const bare = amount.trim().replace(/^-/, "");
  if (!bare) return amount;
  if (OUTGOING_TYPES.has(type)) return `-${bare}`;
  if (INCOMING_TYPES.has(type)) return bare;
  return type === "TRANSFER" ? `-${bare}` : amount;
}

function eventToForm(data: EventDetail): SimpleForm {
  const event = data.event;
  const type = normalizeType(event.event_type);
  return {
    event_type: type,
    symbol: event.asset_symbol,
    asset_network: event.network ?? "",
    amount: event.primary_amount,
    secondary_symbol: event.secondary_asset_symbol ?? "",
    secondary_amount: event.secondary_amount ?? "",
    occurred_at: new Date(event.occurred_at).toISOString().slice(0, 16),
    account_id: event.account_id,
    address_to: event.address_to ?? "",
    from_wallet: event.address_from ?? "",
    eur_value: event.eur_value ?? "",
    sek_value: event.sek_value ?? "",
    fee_asset: data.event.fees[0]?.asset_symbol ?? "",
    fee_amount: data.event.fees[0]?.amount ?? "",
    tx_hash: event.tx_hash ?? "",
  };
}

function emptyForm(): SimpleForm {
  return {
    event_type: "DEPOSIT",
    symbol: "BTC",
    asset_network: "",
    amount: "",
    secondary_symbol: "",
    secondary_amount: "",
    occurred_at: new Date().toISOString().slice(0, 16),
    account_id: null,
    address_to: "",
    from_wallet: "",
    eur_value: "",
    sek_value: "",
    fee_asset: "",
    fee_amount: "",
    tx_hash: "",
  };
}

interface ManualEventDialogProps {
  onClose: () => void;
  onSaved: () => void;
  editEvent?: EventDetail;
}

export function ManualEventDialog({ onClose, onSaved, editEvent }: ManualEventDialogProps) {
  const { data: accounts } = useData<Account[]>(() => getJson("/api/accounts?include_archived=true"), []);
  const initial = editEvent ? eventToForm(editEvent) : emptyForm();
  const [form, setForm] = useState<SimpleForm>(initial);
  const [destinationAccountId, setDestinationAccountId] = useState<number | null>(() => {
    if (!editEvent || normalizeType(editEvent.event.event_type) !== "TRANSFER" || !editEvent.event.address_to) return null;
    return accounts?.find((account) => account.name === editEvent.event.address_to || account.address === editEvent.event.address_to)?.id ?? null;
  });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (destinationAccountId !== null || !editEvent || normalizeType(editEvent.event.event_type) !== "TRANSFER" || !editEvent.event.address_to || !accounts) return;
    const account = accounts.find((item) => item.name === editEvent.event.address_to || item.address === editEvent.event.address_to);
    if (account) setDestinationAccountId(account.id);
  }, [accounts, destinationAccountId, editEvent]);

  const selectedAccount = useMemo(() => accounts?.find((account) => account.id === form.account_id), [accounts, form.account_id]);
  const destinationAccount = useMemo(() => accounts?.find((account) => account.id === destinationAccountId), [accounts, destinationAccountId]);
  const isSwap = form.event_type === "SWAP";
  const isDeposit = form.event_type === "DEPOSIT";
  const isTransfer = form.event_type === "TRANSFER";
  const needsExternalDestination = form.event_type === "WITHDRAWAL" || form.event_type === "PAYMENT" || form.event_type === "DONATION";
  const needsWalletName = form.account_id == null;

  const change = <K extends keyof SimpleForm>(field: K, value: SimpleForm[K]) => setForm((current) => ({ ...current, [field]: value }));

  const changeType = (value: ActivityType) => {
    setForm((current) => ({
      ...current,
      event_type: value,
      secondary_symbol: value === "SWAP" ? current.secondary_symbol : "",
      secondary_amount: value === "SWAP" ? current.secondary_amount : "",
      address_to: value === "TRANSFER" || value === "WITHDRAWAL" || value === "PAYMENT" || value === "DONATION" ? current.address_to : "",
      from_wallet: value === "DEPOSIT" ? current.from_wallet : "",
    }));
    if (value !== "TRANSFER") setDestinationAccountId(null);
  };

  const chooseAccount = (raw: string) => {
    const accountId = raw ? Number(raw) : null;
    const account = accounts?.find((item) => item.id === accountId);
    setForm((current) => ({ ...current, account_id: accountId }));
  };

  const chooseDestination = (raw: string) => {
    const accountId = raw ? Number(raw) : null;
    const account = accounts?.find((item) => item.id === accountId);
    setDestinationAccountId(accountId);
    change("address_to", account?.address ?? account?.name ?? "");
  };

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (!form.symbol.trim() || !form.amount.trim() || !form.occurred_at) {
        setError("Enter an asset, amount, and date/time.");
        return;
      }
      if (isSwap && (!form.secondary_symbol.trim() || !form.secondary_amount.trim())) {
        setError("Enter both assets and amounts for a swap.");
        return;
      }
      if (isTransfer && !destinationAccountId) {
        setError("Choose the wallet receiving the transfer.");
        return;
      }
      if (editEvent && !reason.trim()) {
        setError("Add a reason for this correction.");
        return;
      }

      const primaryAmount = normalizeSign(form.event_type, form.amount);
      const party = isDeposit ? form.from_wallet.trim() : form.address_to.trim();
      const sourceWallet = (selectedAccount?.address ?? selectedAccount?.name ?? form.from_wallet.trim()) || null;
      const addressFrom = isDeposit ? party || null : sourceWallet;
      const addressTo = isDeposit
        ? (selectedAccount?.address ?? selectedAccount?.name ?? form.address_to.trim()) || null
        : isTransfer
          ? destinationAccount?.address ?? destinationAccount?.name ?? null
          : needsExternalDestination
            ? party || null
            : selectedAccount?.address ?? selectedAccount?.name ?? null;

      if (editEvent) {
        const original = editEvent.event;
        // A second leg has to exist before its amount can be corrected the
        // normal way — an event with no secondary_asset_id yet needs the
        // dedicated swap-leg endpoint instead of a plain field override.
        const needsNewSwapLeg = isSwap && original.secondary_asset_id == null;

        const values: Record<string, string | null> = {
          event_type: needsNewSwapLeg ? original.event_type : form.event_type,
          primary_amount: primaryAmount,
          secondary_amount: isSwap && !needsNewSwapLeg ? form.secondary_amount.trim() || null : original.secondary_amount,
          occurred_at: new Date(form.occurred_at).toISOString(),
          address_from: addressFrom,
          address_to: addressTo,
          tx_hash: form.tx_hash.trim() || null,
        };
        const originalValues: Record<string, string | null> = {
          event_type: original.event_type,
          primary_amount: original.primary_amount,
          secondary_amount: original.secondary_amount,
          occurred_at: new Date(original.occurred_at).toISOString(),
          address_from: original.address_from,
          address_to: original.address_to,
          tx_hash: original.tx_hash,
        };
        for (const [field, value] of Object.entries(values)) {
          if (value !== originalValues[field]) await patchJson(`/api/events/${editEvent.event.id}`, { field, value, reason: reason.trim() });
        }
        if (needsNewSwapLeg) {
          await postJson(`/api/events/${editEvent.event.id}/swap-leg`, {
            secondary_asset_symbol: form.secondary_symbol.trim().toUpperCase(),
            secondary_asset_network: form.asset_network.trim() || null,
            secondary_amount: form.secondary_amount.trim(),
            event_type: "SWAP",
            reason: reason.trim(),
          });
        }
        const currentEur = editEvent.valuations.find((value) => value.quote_currency === "EUR")?.total_value ?? "";
        const currentSek = editEvent.valuations.find((value) => value.quote_currency === "SEK")?.total_value ?? "";
        if (form.eur_value !== currentEur) await putJson(`/api/events/${editEvent.event.id}/valuations/EUR`, { total_value: form.eur_value || null, reason: reason.trim() });
        if (form.sek_value !== currentSek) await putJson(`/api/events/${editEvent.event.id}/valuations/SEK`, { total_value: form.sek_value || null, reason: reason.trim() });
        onSaved();
        return;
      }

      await apiFetch("/api/events/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          event_type: form.event_type,
          symbol: form.symbol.trim().toUpperCase(),
          asset_network: form.asset_network.trim() || null,
          amount: primaryAmount,
          secondary_symbol: isSwap ? form.secondary_symbol.trim().toUpperCase() : null,
          secondary_asset_network: isSwap ? form.asset_network.trim() || null : null,
          secondary_amount: isSwap ? form.secondary_amount.trim() || null : null,
          occurred_at: new Date(form.occurred_at).toISOString(),
          account_id: form.account_id,
          address_from: addressFrom,
          address_to: addressTo,
          tx_hash: form.tx_hash.trim() || null,
          fees: form.fee_asset.trim() && form.fee_amount.trim() ? [{
            fee_type: "NETWORK_FEE",
            asset_symbol: form.fee_asset.trim().toUpperCase(),
            amount: form.fee_amount.trim(),
          }] : [],
          eur_value: form.eur_value.trim() || null,
          sek_value: form.sek_value.trim() || null,
        }),
      });
      onSaved();
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "Could not save activity");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={editEvent ? "Correct activity" : "Add activity"}
      eyebrow={editEvent ? "Activity correction" : "New activity"}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={<Upload size={15} />} onClick={() => void save()} loading={saving}>
            {editEvent ? "Save changes" : "Add activity"}
          </Button>
        </>
      }
    >
      <p className="mb-5 text-xs text-soft">Add only the facts needed to record what happened. Prices can be filled automatically when EUR or SEK is left blank.</p>
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Type" htmlFor="activity-type">
            <Select id="activity-type" value={form.event_type} onChange={(event) => changeType(event.target.value as ActivityType)}>
              {ACTIVITY_CHOICES.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
            </Select>
          </Field>
          <Field label="Date & time" htmlFor="activity-datetime">
            <Input id="activity-datetime" type="datetime-local" value={form.occurred_at} onChange={(event) => change("occurred_at", event.target.value)} />
          </Field>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={isDeposit ? "To wallet" : "Wallet"} htmlFor="activity-wallet" hint={editEvent ? "Wallet cannot be changed here" : undefined}>
            <Select id="activity-wallet" value={form.account_id ?? ""} onChange={(event) => chooseAccount(event.target.value)} disabled={Boolean(editEvent)}>
              <option value="">Other wallet…</option>
              {(accounts ?? []).map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}
            </Select>
          </Field>
          {needsWalletName && <Field label={isDeposit ? "To wallet" : "From wallet"} htmlFor="activity-wallet-name"><Input id="activity-wallet-name" value={isDeposit ? form.address_to : form.from_wallet} onChange={(event) => change(isDeposit ? "address_to" : "from_wallet", event.target.value)} placeholder="Wallet name or address" /></Field>}
          {isTransfer && <Field label="To wallet" htmlFor="activity-destination-wallet" hint="Same-provider transfers are valid, for example Bitget Spot → Bitget Futures"><Select id="activity-destination-wallet" value={destinationAccountId ?? ""} onChange={(event) => chooseDestination(event.target.value)}><option value="">Choose wallet…</option>{(accounts ?? []).map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</Select></Field>}
          {isDeposit && <Field label="From wallet" htmlFor="activity-from"><Input id="activity-from" value={form.from_wallet} onChange={(event) => change("from_wallet", event.target.value)} placeholder="Wallet name or address" /></Field>}
          {needsExternalDestination && <Field label="To wallet" htmlFor="activity-to"><Input id="activity-to" value={form.address_to} onChange={(event) => change("address_to", event.target.value)} placeholder="Wallet name or address" /></Field>}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={isSwap ? "Asset given" : "Asset"} htmlFor="activity-symbol"><Input id="activity-symbol" value={form.symbol} onChange={(event) => change("symbol", event.target.value.toUpperCase())} placeholder="BTC" autoFocus /></Field>
          <Field label={isSwap ? "Amount given" : "Amount"} htmlFor="activity-amount"><Input id="activity-amount" value={form.amount} onChange={(event) => change("amount", event.target.value)} placeholder="0.0012" inputMode="decimal" /></Field>
          {isSwap && <>
            <Field label="Asset received" htmlFor="activity-secondary-symbol"><Input id="activity-secondary-symbol" value={form.secondary_symbol} onChange={(event) => change("secondary_symbol", event.target.value.toUpperCase())} placeholder="ETH" /></Field>
            <Field label="Amount received" htmlFor="activity-secondary-amount"><Input id="activity-secondary-amount" value={form.secondary_amount} onChange={(event) => change("secondary_amount", event.target.value)} placeholder="0.15" inputMode="decimal" /></Field>
          </>}
          <Field label="Network (optional)" htmlFor="activity-network"><Input id="activity-network" value={form.asset_network} onChange={(event) => change("asset_network", event.target.value)} placeholder="Ethereum, Solana…" /></Field>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Value in EUR (optional)" htmlFor="activity-eur"><Input id="activity-eur" value={form.eur_value} onChange={(event) => change("eur_value", event.target.value)} placeholder="Filled automatically if blank" inputMode="decimal" /></Field>
          <Field label="Value in SEK (optional)" htmlFor="activity-sek"><Input id="activity-sek" value={form.sek_value} onChange={(event) => change("sek_value", event.target.value)} placeholder="Filled automatically if blank" inputMode="decimal" /></Field>
          <div className="sm:col-span-2">
            <MarketPriceButton
              symbol={form.symbol}
              network={form.asset_network}
              amount={form.amount}
              occurredAt={form.occurred_at}
              currencies={["EUR", "SEK"]}
              label="Fill in FIAT prices"
              onFilled={(prices) => {
                if (prices.EUR) change("eur_value", prices.EUR.total_value);
                if (prices.SEK) change("sek_value", prices.SEK.total_value);
              }}
            />
          </div>
          {!editEvent ? (
            <div className="sm:col-span-2">
              <span className="text-[11px] font-medium text-faint">Fee (optional)</span>
              <div className="mt-1.5 grid gap-2 sm:grid-cols-2">
                <Input id="activity-fee-amount" value={form.fee_amount} onChange={(event) => change("fee_amount", event.target.value)} placeholder="Amount" inputMode="decimal" aria-label="Fee amount" />
                <Input id="activity-fee-asset" value={form.fee_asset} onChange={(event) => change("fee_asset", event.target.value.toUpperCase())} placeholder={form.symbol || "Asset"} aria-label="Fee asset" />
              </div>
            </div>
          ) : (
            <p className="sm:col-span-2 text-[11px] text-soft">Fees are managed in the activity details so every fee uses the same fee list.</p>
          )}
        </div>

        <Field label="Transaction hash (optional)" htmlFor="activity-tx-hash"><Input id="activity-tx-hash" value={form.tx_hash} onChange={(event) => change("tx_hash", event.target.value)} placeholder="Blockchain transaction hash" /></Field>

        {editEvent && <Field label="Reason for correction" htmlFor="activity-reason" hint="Required for the audit trail"><Input id="activity-reason" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Why does this activity need correction?" /></Field>}
      </div>
      {error && <p className="mt-4 rounded-lg bg-bad-soft px-3 py-2 text-xs text-bad">{error}</p>}
    </Dialog>
  );
}

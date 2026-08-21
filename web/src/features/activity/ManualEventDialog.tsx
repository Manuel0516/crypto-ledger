import { useEffect, useMemo, useState } from "react";
import { Upload } from "lucide-react";
import type { Account, EventDetail, ManualEventInput } from "../../types";
import { EVENT_TYPES } from "../../types";
import { apiFetch, getJson, patchJson, putJson } from "../../lib/api";
import { useData } from "../../hooks/useData";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { Field, Input, Select, Textarea } from "../../components/ui/Field";
import { MarketPriceButton } from "../../components/domain/MarketPriceButton";

const EVENT_CHOICES = Object.keys(EVENT_TYPES);

const FEE_TYPES = ["NETWORK_FEE", "GAS_FEE", "EXCHANGE_FEE", "TRADING_FEE", "FUNDING_FEE"];

// Which extra field groups this type actually needs — this is the crux of
// the request: a swap needs a second asset, a deposit needs a from-address,
// a staking reward needs neither. Everything not listed here can still be
// added afterwards through the event's own "Manual correction" editor, so
// nothing is permanently hidden — it's just not asked for up front.
type FieldGroup = "secondaryAsset" | "destination" | "counterparty" | "addressFrom" | "addressTo" | "fee" | "evidence" | "fiatValue";

const TYPE_FIELDS: Record<string, FieldGroup[]> = {
  MANUAL_ADJUSTMENT: ["evidence"],
  BUY: ["fee", "fiatValue", "evidence"],
  SELL: ["fee", "fiatValue", "evidence"],
  SWAP: ["secondaryAsset", "fee", "fiatValue", "evidence"],
  DEPOSIT: ["addressFrom", "counterparty", "evidence"],
  WITHDRAWAL: ["destination", "addressFrom", "addressTo", "counterparty", "fee", "evidence"],
  TRANSFER: ["destination", "addressFrom", "addressTo", "counterparty", "fee", "evidence"],
  SEND: ["destination", "addressFrom", "addressTo", "counterparty", "fee", "evidence"],
  RECEIVE: ["addressFrom", "addressTo", "counterparty", "evidence"],
  PAYMENT: ["destination", "addressFrom", "addressTo", "counterparty", "fee", "evidence"],
  GIFT_SENT: ["destination", "addressFrom", "addressTo", "counterparty"],
  GIFT_RECEIVED: ["addressFrom", "addressTo", "counterparty"],
  INCOME: ["counterparty", "fiatValue", "evidence"],
  STAKING_REWARD: ["counterparty", "fiatValue"],
  AIRDROP: ["counterparty", "fiatValue"],
  CASHBACK: ["counterparty", "fiatValue"],
  MINING_REWARD: ["counterparty", "fiatValue"],
  LOST: [],
  STOLEN: ["counterparty"],
  UNKNOWN: ["counterparty", "evidence"],
};

// Whether the primary leg is inherently a loss or a gain for this type, so
// the user never has to remember to type a minus sign themselves. TRANSFER/
// MANUAL_ADJUSTMENT/UNKNOWN are left neutral — genuinely ambiguous without
// more context, so whatever sign is typed is kept as-is.
const OUTGOING_TYPES = new Set(["SELL", "SWAP", "WITHDRAWAL", "SEND", "PAYMENT", "GIFT_SENT", "STOLEN", "LOST"]);
const INCOMING_TYPES = new Set(["BUY", "DEPOSIT", "RECEIVE", "GIFT_RECEIVED", "INCOME", "STAKING_REWARD", "AIRDROP", "CASHBACK", "MINING_REWARD"]);

function normalizeSign(type: string, amount: string): string {
  const bare = amount.trim().replace(/^-/, "");
  if (!bare) return amount;
  if (OUTGOING_TYPES.has(type)) return `-${bare}`;
  if (INCOMING_TYPES.has(type)) return bare;
  return amount;
}

const AMOUNT_LABELS: Record<string, string> = {
  SWAP: "Amount given",
  WITHDRAWAL: "Amount sent",
  SEND: "Amount sent",
  PAYMENT: "Amount paid",
  GIFT_SENT: "Amount gifted",
  DEPOSIT: "Amount received",
  RECEIVE: "Amount received",
  GIFT_RECEIVED: "Amount received",
  STOLEN: "Amount lost",
  LOST: "Amount lost",
};

interface ManualEventDialogProps {
  onClose: () => void;
  onSaved: () => void;
  editEvent?: EventDetail;
}

function eventToForm(data: EventDetail): ManualEventInput {
  const event = data.event;
  const fee = data.fees[0];
  return {
    event_type: event.event_type,
    symbol: event.asset_symbol,
    asset_network: event.network ?? "",
    amount: event.primary_amount,
    secondary_symbol: event.secondary_asset_symbol ?? "",
    secondary_amount: event.secondary_amount ?? "",
    occurred_at: new Date(event.occurred_at).toISOString().slice(0, 16),
    account_id: event.account_id,
    source_label: event.source_label,
    destination_label: event.destination_label ?? "",
    counterparty: event.counterparty ?? "",
    description: event.description ?? "",
    merchant: event.merchant ?? "",
    tags: event.tags,
    evidence_reference: event.evidence_reference ?? "",
    source_timezone: event.source_timezone ?? "",
    address_from: event.address_from ?? "",
    address_to: event.address_to ?? "",
    tx_hash: event.tx_hash ?? "",
    order_id: event.order_id ?? "",
    trade_id: event.trade_id ?? "",
    deposit_id: event.deposit_id ?? "",
    withdrawal_id: event.withdrawal_id ?? "",
    fee_asset: fee?.asset_symbol ?? "",
    fee_amount: fee?.amount ?? "",
    fee_type: fee?.fee_type ?? "NETWORK_FEE",
    eur_value: event.eur_value ?? "",
    sek_value: event.sek_value ?? "",
    notes: event.notes ?? "",
  };
}

export function ManualEventDialog({ onClose, onSaved, editEvent }: ManualEventDialogProps) {
  const { data: accounts } = useData<Account[]>(() => getJson("/api/accounts"), []);
  const [form, setForm] = useState<ManualEventInput>(() => editEvent ? eventToForm(editEvent) : {
    event_type: "MANUAL_ADJUSTMENT",
    symbol: "BTC",
    asset_network: "",
    amount: "",
    secondary_symbol: "",
    secondary_amount: "",
    occurred_at: new Date().toISOString().slice(0, 16),
    account_id: null,
    source_label: "Manual",
    destination_label: "",
    counterparty: "",
    description: "",
    merchant: "",
    tags: [],
    evidence_reference: "",
    source_timezone: "",
    address_from: "",
    address_to: "",
    tx_hash: "",
    order_id: "",
    trade_id: "",
    deposit_id: "",
    withdrawal_id: "",
    fee_asset: "",
    fee_amount: "",
    fee_type: "NETWORK_FEE",
    eur_value: "",
    sek_value: "",
    notes: "",
  });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [reason, setReason] = useState("");

  const groups = useMemo(() => TYPE_FIELDS[form.event_type] ?? [], [form.event_type]);
  // In edit mode show every field so an existing record can be corrected in
  // one complete form, even if the original type would normally hide it.
  const has = (g: FieldGroup) => Boolean(editEvent) || groups.includes(g);
  // When a second asset leg is visible, the primary leg is the asset given
  // and the secondary leg is the asset received. This also keeps edit mode
  // clear when an imported record has a secondary leg but its event type
  // would otherwise use the generic "Amount received" label.
  const amountLabel = has("secondaryAsset") ? "Amount given" : (AMOUNT_LABELS[form.event_type] ?? "Amount");
  const destinationAccount = accounts?.find((account) => account.name === form.destination_label);
  const destinationChoice = destinationAccount ? String(destinationAccount.id) : "external";
  const counterpartyAccount = accounts?.find((account) => account.name === form.counterparty);
  const counterpartyChoice = counterpartyAccount ? String(counterpartyAccount.id) : "external";

  useEffect(() => {
    if (!accounts?.length) return;
    const selectedAccount = accounts.find((account) => account.id === form.account_id);
    const outgoing = new Set(["WITHDRAWAL", "TRANSFER", "SEND", "PAYMENT", "GIFT_SENT"]).has(form.event_type);
    const nextAddressFrom = outgoing ? selectedAccount?.address : counterpartyAccount?.address;
    const nextAddressTo = outgoing ? destinationAccount?.address : selectedAccount?.address;
    if (!nextAddressFrom && !nextAddressTo) return;
    setForm((current) => ({
      ...current,
      address_from: current.address_from || nextAddressFrom || "",
      address_to: current.address_to || nextAddressTo || "",
    }));
  }, [accounts, counterpartyAccount, destinationAccount, form.account_id, form.event_type]);

  const change = <K extends keyof ManualEventInput>(field: K, value: ManualEventInput[K]) =>
    setForm((current) => ({ ...current, [field]: value }));

  const changeType = (type: string) => {
    if (editEvent) {
      setForm((current) => ({ ...current, event_type: type }));
      return;
    }
    const nextGroups = TYPE_FIELDS[type] ?? [];
    setForm((current) => ({
      ...current,
      event_type: type,
      // Clear fields the new type doesn't use, so a stray value from a
      // previous selection can't sneak into a record that has no use for it.
      secondary_symbol: nextGroups.includes("secondaryAsset") ? current.secondary_symbol : "",
      secondary_amount: nextGroups.includes("secondaryAsset") ? current.secondary_amount : "",
      destination_label: nextGroups.includes("destination") ? current.destination_label : "",
      counterparty: nextGroups.includes("counterparty") ? current.counterparty : "",
      address_from: nextGroups.includes("addressFrom") ? current.address_from : "",
      address_to: nextGroups.includes("addressTo") ? current.address_to : "",
      fee_asset: nextGroups.includes("fee") ? current.fee_asset : "",
      fee_amount: nextGroups.includes("fee") ? current.fee_amount : "",
      tx_hash: nextGroups.includes("evidence") ? current.tx_hash : "",
      order_id: nextGroups.includes("evidence") ? current.order_id : "",
      eur_value: nextGroups.includes("fiatValue") ? current.eur_value : "",
      sek_value: nextGroups.includes("fiatValue") ? current.sek_value : "",
    }));
  };

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (editEvent) {
        if (!reason.trim()) {
          setError("Add a reason for this correction.");
          return;
        }
        const values: Record<string, string | null> = {
          event_type: form.event_type,
          source_label: form.source_label,
          destination_label: form.destination_label || null,
          counterparty: form.counterparty || null,
          description: form.description || null,
          merchant: form.merchant || null,
          tags_json: JSON.stringify(form.tags ?? []),
          evidence_reference: form.evidence_reference || null,
          address_from: form.address_from || null,
          address_to: form.address_to || null,
          notes: form.notes || null,
          primary_amount: form.amount,
          occurred_at: form.occurred_at ? new Date(form.occurred_at).toISOString() : null,
          tx_hash: form.tx_hash || null,
          order_id: form.order_id || null,
          trade_id: form.trade_id || null,
          deposit_id: form.deposit_id || null,
          withdrawal_id: form.withdrawal_id || null,
        };
        const original = editEvent.event;
        const originalValues: Record<string, string | null> = {
          event_type: original.event_type,
          source_label: original.source_label,
          destination_label: original.destination_label,
          counterparty: original.counterparty,
          description: original.description,
          merchant: original.merchant,
          tags_json: JSON.stringify(original.tags),
          evidence_reference: original.evidence_reference,
          address_from: original.address_from,
          address_to: original.address_to,
          notes: original.notes,
          primary_amount: original.primary_amount,
          occurred_at: new Date(original.occurred_at).toISOString(),
          tx_hash: original.tx_hash,
          order_id: original.order_id,
          trade_id: original.trade_id,
          deposit_id: original.deposit_id,
          withdrawal_id: original.withdrawal_id,
        };
        for (const [field, value] of Object.entries(values)) {
          if (value !== originalValues[field]) await patchJson(`/api/events/${editEvent.event.id}`, { field, value, reason: reason.trim() });
        }
        const currentEur = editEvent.valuations.find((v) => v.quote_currency === "EUR")?.total_value ?? "";
        const currentSek = editEvent.valuations.find((v) => v.quote_currency === "SEK")?.total_value ?? "";
        if ((form.eur_value || "") !== currentEur) await putJson(`/api/events/${editEvent.event.id}/valuations/EUR`, { total_value: form.eur_value || null, reason: reason.trim() });
        if ((form.sek_value || "") !== currentSek) await putJson(`/api/events/${editEvent.event.id}/valuations/SEK`, { total_value: form.sek_value || null, reason: reason.trim() });
        onSaved();
        return;
      }
      const body: Record<string, unknown> = {
        ...form,
        amount: normalizeSign(form.event_type, form.amount),
        occurred_at: form.occurred_at ? new Date(form.occurred_at).toISOString() : undefined,
      };
      // Empty optional strings should reach the API as null, not "".
      for (const key of Object.keys(body)) {
        if (body[key] === "") body[key] = null;
      }
      await apiFetch("/api/events/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save event");
    } finally {
      setSaving(false);
    }
  };

  const onAccountChange = (raw: string) => {
    const id = raw ? Number(raw) : null;
    change("account_id", id);
    const account = accounts?.find((a) => a.id === id);
    if (account) {
      change("source_label", account.name);
      if (account.address) {
        const outgoing = new Set(["WITHDRAWAL", "TRANSFER", "SEND", "PAYMENT", "GIFT_SENT"]).has(form.event_type);
        change(outgoing ? "address_from" : "address_to", account.address);
      }
    }
  };

  const onDestinationChange = (raw: string) => {
    if (raw === "external") {
      change("destination_label", "");
      return;
    }
    const account = accounts?.find((item) => item.id === Number(raw));
    if (account) {
      change("destination_label", account.name);
      if (account.address) change("address_to", account.address);
    }
  };

  const onCounterpartyChange = (raw: string) => {
    if (raw === "external") {
      change("counterparty", "");
      return;
    }
    const account = accounts?.find((item) => item.id === Number(raw));
    if (account) {
      change("counterparty", account.name);
      if (account.address && has("addressFrom")) change("address_from", account.address);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={editEvent ? "Modify activity" : "Add manual event"}
      eyebrow={editEvent ? "Activity correction" : "New record"}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" icon={<Upload size={15} />} onClick={() => void save()} loading={saving}>
            {editEvent ? "Save correction" : "Save for review"}
          </Button>
        </>
      }
    >
      <p className="mb-5 text-xs text-soft">
        {editEvent ? "Update the activity using the same full form as when it was added. The original normalized values and raw evidence remain unchanged." : "Manual records stay clearly marked in provenance and require review before a tax report is ready. Fields shown adapt to the type — anything else can still be added afterwards from the event's own details."}
      </p>
      <div className="space-y-5">
        <FormSection title="Activity" description="What happened, when, and which assets were involved.">
          <Field label="Type" htmlFor="manual-type"><Select id="manual-type" value={form.event_type} onChange={(e) => changeType(e.target.value)}>{EVENT_CHOICES.map((choice) => <option key={choice} value={choice}>{choice.split("_").join(" ")}</option>)}</Select></Field>
          <Field label="Date & time" htmlFor="manual-datetime"><Input id="manual-datetime" type="datetime-local" value={form.occurred_at} onChange={(e) => change("occurred_at", e.target.value)} /></Field>
          <Field label={has("secondaryAsset") ? "Asset given" : "Asset symbol"} htmlFor="manual-symbol"><Input id="manual-symbol" value={form.symbol} onChange={(e) => change("symbol", e.target.value.toUpperCase())} placeholder="BTC" /></Field>
          <Field label="Network" htmlFor="manual-asset-network" hint="Optional, for token identity"><Input id="manual-asset-network" value={form.asset_network ?? ""} onChange={(e) => change("asset_network", e.target.value)} placeholder="Bitcoin, Ethereum…" /></Field>
          <Field label={amountLabel} htmlFor="manual-amount"><Input id="manual-amount" value={form.amount} onChange={(e) => change("amount", e.target.value)} placeholder="0.0012" inputMode="decimal" required /></Field>
          {has("secondaryAsset") && <><Field label="Asset received" htmlFor="manual-secondary-symbol"><Input id="manual-secondary-symbol" value={form.secondary_symbol ?? ""} onChange={(e) => change("secondary_symbol", e.target.value.toUpperCase())} placeholder="ETH" /></Field><Field label="Received asset network" htmlFor="manual-secondary-network"><Input id="manual-secondary-network" value={form.secondary_asset_network ?? ""} onChange={(e) => change("secondary_asset_network", e.target.value)} placeholder="Optional" /></Field><Field label="Amount received" htmlFor="manual-secondary-amount"><Input id="manual-secondary-amount" value={form.secondary_amount ?? ""} onChange={(e) => change("secondary_amount", e.target.value)} placeholder="0.15" inputMode="decimal" /></Field></>}
        </FormSection>

        <FormSection title="Ownership & counterparties" description="Which of your accounts and which other party were involved.">
          <Field label="Account / source" htmlFor="manual-account" hint="Optional — links this event to a linked source"><Select id="manual-account" value={form.account_id ?? ""} onChange={(e) => onAccountChange(e.target.value)}><option value="">Custom label…</option>{(accounts ?? []).map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</Select></Field>
          <Field label="Source label" htmlFor="manual-source"><Input id="manual-source" value={form.source_label} onChange={(e) => change("source_label", e.target.value)} placeholder="Manual" disabled={form.account_id != null} /></Field>
          {has("destination") && <><Field label="Destination source" htmlFor="manual-destination-source" hint="Choose a registered source or enter an external destination"><Select id="manual-destination-source" value={destinationChoice} onChange={(e) => onDestinationChange(e.target.value)}><option value="external">External source…</option>{(accounts ?? []).map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</Select></Field>{destinationChoice === "external" && <Field label="External destination" htmlFor="manual-destination"><Input id="manual-destination" value={form.destination_label ?? ""} onChange={(e) => change("destination_label", e.target.value)} placeholder="Exchange, wallet, person…" /></Field>}</>}
          {has("counterparty") && <><Field label="Other source" htmlFor="manual-counterparty-source" hint="Choose a registered source or enter an external party"><Select id="manual-counterparty-source" value={counterpartyChoice} onChange={(e) => onCounterpartyChange(e.target.value)}><option value="external">External source…</option>{(accounts ?? []).map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</Select></Field>{counterpartyChoice === "external" && <Field label="External source / counterparty" htmlFor="manual-counterparty"><Input id="manual-counterparty" value={form.counterparty ?? ""} onChange={(e) => change("counterparty", e.target.value)} placeholder="Exchange, wallet, person…" /></Field>}</>}
          <Field label="Merchant" htmlFor="manual-merchant"><Input id="manual-merchant" value={form.merchant ?? ""} onChange={(e) => change("merchant", e.target.value)} placeholder="Optional" /></Field>
        </FormSection>

        {(has("addressFrom") || has("addressTo") || has("evidence")) && <FormSection title="Evidence & identifiers" description="Addresses and source references that support this activity.">
          {has("addressFrom") && <Field label="From address" htmlFor="manual-address-from"><Input id="manual-address-from" value={form.address_from ?? ""} onChange={(e) => change("address_from", e.target.value)} placeholder="Optional" /></Field>}
          {has("addressTo") && <Field label="To address" htmlFor="manual-address-to"><Input id="manual-address-to" value={form.address_to ?? ""} onChange={(e) => change("address_to", e.target.value)} placeholder="Optional" /></Field>}
          {has("evidence") && <><Field label="Transaction hash" htmlFor="manual-tx-hash"><Input id="manual-tx-hash" value={form.tx_hash ?? ""} onChange={(e) => change("tx_hash", e.target.value)} placeholder="Optional" /></Field><Field label="Order / reference ID" htmlFor="manual-order-id"><Input id="manual-order-id" value={form.order_id ?? ""} onChange={(e) => change("order_id", e.target.value)} placeholder="Optional" /></Field><Field label="Evidence reference" htmlFor="manual-evidence-reference" hint="Receipt, explorer URL, or external record"><Input id="manual-evidence-reference" value={form.evidence_reference ?? ""} onChange={(e) => change("evidence_reference", e.target.value)} placeholder="Optional" /></Field><Field label="Source timezone" htmlFor="manual-source-timezone" hint="Only when the source timestamp is local"><Input id="manual-source-timezone" value={form.source_timezone ?? ""} onChange={(e) => change("source_timezone", e.target.value)} placeholder="Europe/Stockholm" /></Field><Field label="Trade ID" htmlFor="manual-trade-id"><Input id="manual-trade-id" value={form.trade_id ?? ""} onChange={(e) => change("trade_id", e.target.value)} placeholder="Optional" /></Field><Field label="Deposit ID" htmlFor="manual-deposit-id"><Input id="manual-deposit-id" value={form.deposit_id ?? ""} onChange={(e) => change("deposit_id", e.target.value)} placeholder="Optional" /></Field><Field label="Withdrawal ID" htmlFor="manual-withdrawal-id"><Input id="manual-withdrawal-id" value={form.withdrawal_id ?? ""} onChange={(e) => change("withdrawal_id", e.target.value)} placeholder="Optional" /></Field></>}
        </FormSection>}

        {(has("fiatValue") || has("fee")) && <FormSection title="Values & fees" description="Optional values and costs used for pricing and reporting.">
          {has("fiatValue") && <>
            <Field label="EUR value" htmlFor="manual-eur" hint="Leave blank to price automatically"><Input id="manual-eur" value={form.eur_value ?? ""} onChange={(e) => change("eur_value", e.target.value)} placeholder="Optional" inputMode="decimal" /></Field>
            <Field label="SEK value" htmlFor="manual-sek" hint="Leave blank to price automatically"><Input id="manual-sek" value={form.sek_value ?? ""} onChange={(e) => change("sek_value", e.target.value)} placeholder="Optional" inputMode="decimal" /></Field>
            <div className="sm:col-span-2">
              <MarketPriceButton
                symbol={form.symbol}
                network={form.asset_network}
                amount={form.amount}
                occurredAt={form.occurred_at}
                currencies={["EUR", "SEK"]}
                label="Fill EUR & SEK from market price"
                onFilled={(prices) => {
                  if (prices.EUR) change("eur_value", prices.EUR.total_value);
                  if (prices.SEK) change("sek_value", prices.SEK.total_value);
                }}
              />
            </div>
          </>}
          {has("fee") && <><Field label="Fee type" htmlFor="manual-fee-type"><Select id="manual-fee-type" value={form.fee_type} onChange={(e) => change("fee_type", e.target.value)}>{FEE_TYPES.map((feeType) => <option key={feeType} value={feeType}>{feeType.split("_").join(" ")}</option>)}</Select></Field><Field label="Fee asset" htmlFor="manual-fee-asset"><Input id="manual-fee-asset" value={form.fee_asset ?? ""} onChange={(e) => change("fee_asset", e.target.value.toUpperCase())} placeholder="Optional" /></Field><Field label="Fee amount" htmlFor="manual-fee-amount"><Input id="manual-fee-amount" value={form.fee_amount ?? ""} onChange={(e) => change("fee_amount", e.target.value)} placeholder="Optional" inputMode="decimal" /></Field></>}
        </FormSection>}

        <FormSection title="Context" description="Human-readable details that make future review easier.">
          <Field label="Description" htmlFor="manual-description" className="sm:col-span-2"><Input id="manual-description" value={form.description ?? ""} onChange={(e) => change("description", e.target.value)} placeholder="What happened?" /></Field>
          <Field label="Tags" htmlFor="manual-tags" hint="Comma-separated"><Input id="manual-tags" value={(form.tags ?? []).join(", ")} onChange={(e) => change("tags", e.target.value.split(",").map((tag) => tag.trim()).filter(Boolean))} placeholder="travel, reimbursement" /></Field>
          <Field label="Notes" htmlFor="manual-notes" className="sm:col-span-2"><Textarea id="manual-notes" rows={3} value={form.notes ?? ""} onChange={(e) => change("notes", e.target.value)} placeholder="Why is this record being added?" /></Field>
        </FormSection>

        {editEvent && <FormSection title="Audit trail" description="Required to preserve why this imported activity was corrected."><Field label="Reason for correction" htmlFor="edit-reason" hint="Required for the audit trail" className="sm:col-span-2"><Textarea id="edit-reason" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why are these activity details being corrected?" /></Field></FormSection>}
      </div>
      {error && <p className="mt-4 rounded-lg bg-bad-soft px-3 py-2 text-xs text-bad">{error}</p>}
    </Dialog>
  );
}

function FormSection({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-base/35 p-4">
      <div className="mb-4 border-b border-line pb-3">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        <p className="mt-0.5 text-xs text-soft">{description}</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
    </section>
  );
}

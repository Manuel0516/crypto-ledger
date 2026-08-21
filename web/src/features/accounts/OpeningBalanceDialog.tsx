import { useState } from "react";
import type { Account } from "../../types";
import { postJson } from "../../lib/api";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { Field, Input } from "../../components/ui/Field";

interface OpeningBalanceDialogProps {
  account: Account;
  onClose: () => void;
  onSaved: () => void;
}

export function OpeningBalanceDialog({ account, onClose, onSaved }: OpeningBalanceDialogProps) {
  const [symbol, setSymbol] = useState("");
  const [network, setNetwork] = useState("");
  const [amount, setAmount] = useState("");
  const [occurredAt, setOccurredAt] = useState(() => new Date().toISOString().slice(0, 16));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await postJson("/api/events/manual", {
        event_type: "MANUAL_ADJUSTMENT",
        event_subtype: "opening_balance",
        symbol: symbol.trim().toUpperCase(),
        asset_network: network.trim() || null,
        amount: amount.trim(),
        occurred_at: new Date(occurredAt).toISOString(),
        account_id: account.id,
      });
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save opening balance");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Opening balance · ${account.name}`}
      eyebrow="Manual account"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => void save()} loading={saving} disabled={!symbol.trim() || !amount.trim() || !occurredAt}>
            Save opening balance
          </Button>
        </>
      }
    >
      <p className="mb-5 text-xs text-soft">
        This records the starting amount as a ledger event. Add future activity with this account selected and its balance will update from the complete history. Use Activity to correct the opening event later.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Asset symbol" htmlFor="opening-symbol">
          <Input id="opening-symbol" value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} placeholder="XMR" autoFocus />
        </Field>
        <Field label="Amount" htmlFor="opening-amount" hint="Positive amount">
          <Input id="opening-amount" value={amount} onChange={(event) => setAmount(event.target.value)} placeholder="10.5" inputMode="decimal" />
        </Field>
        <Field label="Network (optional)" htmlFor="opening-network" className="sm:col-span-2" hint="Useful for tokens or assets sharing a symbol">
          <Input id="opening-network" value={network} onChange={(event) => setNetwork(event.target.value)} placeholder="Monero, Ethereum…" />
        </Field>
        <Field label="Balance date" htmlFor="opening-date" className="sm:col-span-2">
          <Input id="opening-date" type="datetime-local" value={occurredAt} onChange={(event) => setOccurredAt(event.target.value)} />
        </Field>
      </div>
      {error && <p className="mt-4 rounded-lg bg-bad-soft px-3 py-2 text-xs text-bad">{error}</p>}
    </Dialog>
  );
}

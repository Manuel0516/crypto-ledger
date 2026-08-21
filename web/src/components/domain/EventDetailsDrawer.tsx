import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowRight, Eye, Fingerprint, PencilLine, Plus, RotateCcw, Trash2 } from "lucide-react";
import type { EventDetail, EditableEventField, FeeDetail, Issue } from "../../types";
import { EDITABLE_EVENT_FIELDS, formatType } from "../../types";
import { getJson, patchJson, postJson, putJson, deleteJson } from "../../lib/api";
import { Drawer } from "../ui/Drawer";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Spinner } from "../ui/Button";
import { Field, Input, Select } from "../ui/Field";
import { ErrorState } from "../ui/EmptyState";
import { useConfirmDialog } from "../ui/ConfirmDialog";
import { CryptoAmount } from "./CryptoAmount";
import { MoneyValue } from "./MoneyValue";
import { EventTypeBadge } from "./EventTypeBadge";
import { formatDateTime, cx } from "../../lib/format";
import { ManualEventDialog } from "../../features/activity/ManualEventDialog";
import { MarketPriceButton } from "./MarketPriceButton";

const FIELD_LABELS: Record<EditableEventField, string> = {
  event_type: "Type",
  event_subtype: "Subtype",
  address_from: "From address",
  address_to: "To address",
  primary_amount: "Amount",
  secondary_amount: "Received amount (swap)",
  occurred_at: "Date/time (ISO-8601)",
  tx_hash: "Transaction hash",
  order_id: "Order ID",
  trade_id: "Trade ID",
  deposit_id: "Deposit ID",
  withdrawal_id: "Withdrawal ID",
  contract_address: "Contract address",
  block_hash: "Block hash",
};

interface EventDetailsDrawerProps {
  eventId: number;
  onClose: () => void;
  onChange?: () => void;
}

export function EventDetailsDrawer({ eventId, onClose, onChange }: EventDetailsDrawerProps) {
  const [data, setData] = useState<EventDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [showRawPayload, setShowRawPayload] = useState(false);
  const [shownOriginalField, setShownOriginalField] = useState<string | null>(null);
  const [restoringField, setRestoringField] = useState<string | null>(null);
  const [resolvingIssue, setResolvingIssue] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [blockingAsset, setBlockingAsset] = useState<number | null>(null);
  const { confirm, confirmDialog } = useConfirmDialog();

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await getJson<EventDetail>(`/api/events/${eventId}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load event");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventId]);

  const notifyChange = () => onChange?.();

  const [reviewing, setReviewing] = useState(false);
  const markReviewed = async () => {
    setReviewing(true);
    setError("");
    try {
      await postJson(`/api/events/${eventId}/review`, {});
      await load();
      notifyChange();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not mark this event reviewed");
    } finally {
      setReviewing(false);
    }
  };

  const restoreAutomaticValue = async (restoreField: string) => {
    setRestoringField(restoreField);
    setError("");
    try {
      await postJson(`/api/events/${eventId}/overrides/${restoreField}/restore`, {});
      await load();
      notifyChange();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not restore the automatic value");
    } finally {
      setRestoringField(null);
    }
  };

  const resolveIssue = async (issue: Issue) => {
    setResolvingIssue(issue.id);
    setError("");
    try {
      await postJson(`/api/issues/${issue.id}/resolve`, {});
      await load();
      notifyChange();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not resolve this issue");
    } finally {
      setResolvingIssue(null);
    }
  };

  const deleteEvent = async () => {
    if (!data?.event) return;
      const confirmed = await confirm({
        title: "Delete activity permanently?",
        message: `This permanently removes ${formatType(data.event.event_type)} activity, its stored source evidence, corrections, valuations, and attachments. It cannot be undone. A future source sync may re-import the record.`,
      confirmLabel: "Delete activity",
      destructive: true,
    });
    if (!confirmed) return;

    setDeleting(true);
    setError("");
    try {
      await deleteJson(`/api/events/${eventId}`);
      notifyChange();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete this activity");
    } finally {
      setDeleting(false);
    }
  };

  const blockAsset = async (assetId: number, symbol: string) => {
    const confirmed = await confirm({
      title: `Block ${symbol}?`,
      message: `This hides ${symbol} from Overview, Activity, tax reports, and issue counts. Its canonical activity and raw evidence stay stored, and you can unblock it later in Settings → Data.`,
      confirmLabel: `Block ${symbol}`,
    });
    if (!confirmed) return;
    setBlockingAsset(assetId);
    setError("");
    try {
      await patchJson(`/api/assets/${assetId}`, { is_blocked: true });
      notifyChange();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `Could not block ${symbol}`);
    } finally {
      setBlockingAsset(null);
    }
  };


  const event = data?.event;

  return (
    <>
      <Drawer open onClose={onClose} title={event ? formatType(event.event_type) : "Event details"} eyebrow="Event details">
      {error && <ErrorState message={error} />}
      {loading && !data && (
        <div className="flex justify-center py-12 text-soft">
          <Spinner className="size-5" />
        </div>
      )}
      {event && data && (
        <div className="space-y-6">
          {/* Amount hero */}
          <div className="flex items-center justify-between rounded-xl bg-base px-4 py-4">
            <div className="flex items-center gap-2.5">
              <CryptoAmount amount={event.primary_amount} symbol={event.asset_symbol} className="text-lg font-semibold text-ink" />
              {event.secondary_asset_symbol && event.secondary_amount && (
                <>
                  <ArrowRight size={15} className="shrink-0 text-faint" />
                  <CryptoAmount amount={event.secondary_amount} symbol={event.secondary_asset_symbol} className="text-lg font-semibold text-good" />
                </>
              )}
            </div>
            <div className="flex items-center gap-2">
              <EventTypeBadge eventType={event.event_type} />
              {event.direction === "-" && <Badge tone="danger" dot>Outgoing</Badge>}
              {event.direction === "+" && <Badge tone="success" dot>Incoming</Badge>}
            </div>
          </div>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-soft">
            <span>{formatDateTime(event.occurred_at)}</span>
            {event.network && <Badge tone="neutral">{event.network}</Badge>}
            {event.event_type === "TRANSFER" && <Badge tone="info">Internal transfer</Badge>}
            {event.has_open_issue && <Badge tone="warning">Unresolved issue</Badge>}
          </p>

          {event.status === "REQUIRES_REVIEW" && (
            <div className="flex flex-wrap items-center justify-between gap-2.5 rounded-xl bg-warn-soft px-4 py-3">
              <p className="text-xs text-warn">
                Flagged for review — this record's details could not be fully determined automatically. Check it over, then resolve the review issue.
              </p>
              <Button size="sm" variant="primary" onClick={() => void markReviewed()} loading={reviewing}>
                Resolve issue
              </Button>
            </div>
          )}

          {data.issues.length > 0 && (
            <Section title="Open issues">
              <div className="space-y-2.5">
                {data.issues.map((issue) => (
                  <div key={issue.id} className="flex flex-col gap-3 rounded-xl border border-warn/25 bg-warn-soft/50 px-3.5 py-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="flex min-w-0 items-start gap-2.5">
                      <AlertTriangle size={16} className="mt-0.5 shrink-0 text-warn" />
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-ink">{issue.title}</p>
                        <p className="mt-0.5 text-xs text-soft">{issue.detail}</p>
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-wrap gap-2 sm:flex-col sm:items-stretch">
                      <Button size="sm" variant="secondary" onClick={() => void resolveIssue(issue)} loading={resolvingIssue === issue.id} disabled={resolvingIssue !== null && resolvingIssue !== issue.id}>Resolve issue</Button>
                    </div>
                  </div>
                ))}
              </div>
            </Section>
          )}

          {/* Transaction facts */}
          <Section title="Transaction">
            <dl className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
              <DetailRow label="From" value={event.address_from_label || event.address_from || event.account_name || "—"} copyText={event.address_from || undefined} />
              <DetailRow label="To" value={event.address_to_label || event.address_to || "—"} copyText={event.address_to || undefined} />
              {event.secondary_asset_symbol && event.secondary_amount && (
                <DetailRow
                  label="Received"
                  value={<CryptoAmount amount={event.secondary_amount} symbol={event.secondary_asset_symbol} className="text-[13px] text-ink" />}
                />
              )}
              <DetailRow label="EUR value" value={<MoneyValue value={event.eur_value} />} />
              <DetailRow label="SEK value" value={<MoneyValue value={event.sek_value} currency="SEK" />} />
            </dl>
          </Section>

          {/* Fees — editable list, not just the first one (plan §19) */}
          <Section title="Fees">
            <FeesEditor eventId={eventId} fees={event.fees} onChange={async () => { await load(); notifyChange(); }} />
          </Section>

          {/* Structured network/exchange evidence (plan §17-18) */}
          {hasEvidence(data.evidence) && (
            <Section title="On-chain / exchange evidence">
              <dl className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
                {data.evidence.tx_hash && <DetailRow label="Transaction hash" value={<span className="font-mono text-[11px]">{data.evidence.tx_hash}</span>} copyText={data.evidence.tx_hash} wide />}
                {data.evidence.block_height !== null && <DetailRow label="Block height" value={data.evidence.block_height} />}
                {data.evidence.block_hash && <DetailRow label="Block hash" value={<span className="font-mono text-[11px]">{data.evidence.block_hash}</span>} copyText={data.evidence.block_hash} wide />}
                {data.evidence.log_index !== null && <DetailRow label="Log index" value={data.evidence.log_index} />}
                {data.evidence.contract_address && <DetailRow label="Contract" value={<span className="font-mono text-[11px]">{data.evidence.contract_address}</span>} copyText={data.evidence.contract_address} wide />}
                {data.evidence.order_id && <DetailRow label="Order ID" value={<span className="font-mono text-[11px]">{data.evidence.order_id}</span>} copyText={data.evidence.order_id} wide />}
                {data.evidence.trade_id && <DetailRow label="Trade ID" value={<span className="font-mono text-[11px]">{data.evidence.trade_id}</span>} copyText={data.evidence.trade_id} wide />}
                {data.evidence.deposit_id && <DetailRow label="Deposit ID" value={<span className="font-mono text-[11px]">{data.evidence.deposit_id}</span>} copyText={data.evidence.deposit_id} wide />}
                {data.evidence.withdrawal_id && <DetailRow label="Withdrawal ID" value={<span className="font-mono text-[11px]">{data.evidence.withdrawal_id}</span>} copyText={data.evidence.withdrawal_id} wide />}
                {data.evidence.gas_used && <DetailRow label="Gas used" value={data.evidence.gas_used} copyText={data.evidence.gas_used} />}
                {data.evidence.gas_price && <DetailRow label="Gas price" value={`${data.evidence.gas_price} wei`} copyText={data.evidence.gas_price} />}
                {data.evidence.transaction_nonce !== null && <DetailRow label="Nonce" value={data.evidence.transaction_nonce} />}
                {data.evidence.transaction_index !== null && <DetailRow label="Transaction index" value={data.evidence.transaction_index} />}
                {data.evidence.transaction_input && data.evidence.transaction_input !== "0x" && (
                  <DetailRow label="Input data" value={<span className="break-all font-mono text-[11px]">{data.evidence.transaction_input}</span>} copyText={data.evidence.transaction_input} wide />
                )}
              </dl>
            </Section>
          )}

          {/* Price + manual correction */}
          <Section title="Price">
            <PriceEditor eventId={eventId} event={event} valuations={data.valuations} onChange={async () => { await load(); notifyChange(); }} />
          </Section>

          <div className="border-t border-line pt-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h4 className="font-mono text-[10px] uppercase tracking-widest text-faint">Activity actions</h4>
                <p className="mt-1 text-xs text-soft">Modify this record, hide a junk asset, or permanently remove the record.</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {!event.asset_blocked && <Button size="sm" variant="secondary" onClick={() => void blockAsset(event.asset_id, event.asset_symbol)} loading={blockingAsset === event.asset_id}>Block {event.asset_symbol}</Button>}
                {event.secondary_asset_id !== null && event.secondary_asset_symbol && !event.secondary_asset_blocked && <Button size="sm" variant="secondary" onClick={() => void blockAsset(event.secondary_asset_id!, event.secondary_asset_symbol!)} loading={blockingAsset === event.secondary_asset_id}>Block {event.secondary_asset_symbol}</Button>}
                <Button size="sm" variant="secondary" onClick={() => setEditDialogOpen(true)} icon={<PencilLine size={14} />}>Modify activity</Button>
                <Button size="sm" variant="danger" onClick={() => void deleteEvent()} icon={<Trash2 size={14} />} loading={deleting}>Delete activity</Button>
              </div>
            </div>
          </div>

          <details className="border-t border-line pt-5">
            <summary className="cursor-pointer list-none font-mono text-[10px] uppercase tracking-widest text-faint">Audit data</summary>
            <div className="mt-3 space-y-5">
              {data.overrides.length > 0 && (
                <div>
                  <h4 className="font-mono text-[10px] uppercase tracking-widest text-faint">Corrections</h4>
                  <div className="mt-3 space-y-2">
                    {data.overrides.map((override, index) => (
                      <div key={index} className="rounded-lg border border-line px-3 py-2.5 text-xs">
                        <p className="font-semibold text-ink">{FIELD_LABELS[override.field as EditableEventField] ?? formatType(override.field)}</p>
                        <p className="mt-1 text-soft">
                          {override.old_value || "—"} <span className="text-faint">→</span> {override.new_value || "—"}
                        </p>
                        <p className="mt-1 text-faint">
                          {formatDateTime(override.changed_at)}
                          {override.reason ? ` · ${override.reason}` : ""}
                        </p>
                        {isRestorable(data, override.field) && (
                          <div className="mt-2 flex items-center gap-2">
                            <Button size="sm" variant="ghost" icon={<Eye size={12} />} onClick={() => setShownOriginalField((current) => current === override.field ? null : override.field)}>
                              View original
                            </Button>
                            <Button size="sm" variant="ghost" icon={<RotateCcw size={12} />} loading={restoringField === override.field} onClick={() => void restoreAutomaticValue(override.field)}>
                              Restore automatic
                            </Button>
                          </div>
                        )}
                        {shownOriginalField === override.field && <p className="mt-2 rounded bg-base px-2 py-1.5 text-[11px] text-soft">Original automatic value: {data.original_values[override.field] || "—"}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className={cx(data.overrides.length > 0 && "border-t border-line pt-5")}>
                <h4 className="font-mono text-[10px] uppercase tracking-widest text-faint">Raw evidence</h4>
                <div className="mt-3">
                  {data.raw ? (
                    <div className="space-y-2">
                      <div className="flex items-start gap-2.5 rounded-lg bg-base px-3 py-3">
                        <Fingerprint size={14} className="mt-0.5 shrink-0 text-faint" />
                        <div className="min-w-0">
                          <p className="font-mono text-[10px] uppercase tracking-wide text-faint">SHA-256 payload</p>
                          <CopyableValue text={data.raw.payload_hash}>
                            <span className="mt-1 break-all font-mono text-[10px] text-soft">{data.raw.payload_hash}</span>
                          </CopyableValue>
                        </div>
                      </div>
                      <p className="text-[11px] text-faint">Normalizer {event.normalizer_version || data.raw.connector_version}</p>
                      <dl className="grid grid-cols-1 gap-x-4 gap-y-3 text-xs sm:grid-cols-2">
                        <DetailRow label="Source" value={data.raw.source_id} copyText={data.raw.source_id} />
                        <DetailRow label="External ID" value={<span className="font-mono text-[10px]">{data.raw.external_id}</span>} copyText={data.raw.external_id} wide />
                        <DetailRow label="Imported" value={formatDateTime(data.raw.received_at)} />
                        <DetailRow label="Original timestamp" value={data.raw.source_timestamp ? formatDateTime(data.raw.source_timestamp) : "—"} />
                        {data.raw.source_timezone && <DetailRow label="Source timezone" value={data.raw.source_timezone} />}
                        {data.raw.source_reference && <DetailRow label="Source reference" value={<span className="font-mono text-[10px]">{data.raw.source_reference}</span>} copyText={data.raw.source_reference} wide />}
                      </dl>
                      <Button size="sm" variant="secondary" icon={<Eye size={13} />} onClick={() => setShowRawPayload((visible) => !visible)}>{showRawPayload ? "Hide original record" : "View original record"}</Button>
                      {showRawPayload && <pre className="max-h-72 overflow-auto rounded-lg bg-base p-3 text-[10px] leading-relaxed text-soft">{JSON.stringify(data.raw.payload, null, 2)}</pre>}
                    </div>
                  ) : (
                    <p className="text-xs text-soft">No raw source payload for this record.</p>
                  )}
                  {event.modified && (
                    <p className="mt-3 inline-flex items-center gap-1.5 text-[11px] text-warn">
                      <PencilLine size={12} />
                      Corrected activity · original evidence remains unchanged
                    </p>
                  )}
                </div>
              </div>
            </div>
          </details>
        </div>
      )}
      {editDialogOpen && data && <ManualEventDialog editEvent={data} onClose={() => setEditDialogOpen(false)} onSaved={async () => { setEditDialogOpen(false); await load(); notifyChange(); }} />}
      </Drawer>
      {confirmDialog}
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-line pt-5">
      <h4 className="font-mono text-[10px] uppercase tracking-widest text-faint">{title}</h4>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function DetailRow({ label, value, copyText, wide = false }: { label: string; value: React.ReactNode; copyText?: string; wide?: boolean }) {
  return (
    <div className={cx("min-w-0", wide && "sm:col-span-2")}>
      <dt className="text-[10px] font-medium uppercase tracking-wider text-faint">{label}</dt>
      <dd className="mt-1 min-w-0 max-w-full break-words text-[13px] text-ink [overflow-wrap:anywhere]">
        {copyText ? <CopyableValue text={copyText}>{value}</CopyableValue> : value}
      </dd>
    </div>
  );
}

function CopyableValue({ text, children }: { text: string; children: React.ReactNode }) {
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
  }, []);

  const copy = async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.setAttribute("readonly", "true");
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        textarea.remove();
      }
      setCopied(true);
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
      timeoutRef.current = window.setTimeout(() => setCopied(false), 1400);
    } catch {
      // Clipboard access can be denied by the browser; the value remains
      // selectable and fully visible, so this should stay a quiet failure.
    }
  };

  return (
    <button
      type="button"
      onClick={() => void copy()}
      title="Copy"
      className="inline-flex max-w-full cursor-copy flex-wrap items-baseline gap-x-2 gap-y-0.5 text-left align-top hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
    >
      <span className="min-w-0 max-w-full break-words [overflow-wrap:anywhere]">{children}</span>
      {copied && <span className="shrink-0 font-mono text-[10px] font-medium text-good" aria-live="polite">Copied</span>}
    </button>
  );
}

function hasEvidence(evidence: EventDetail["evidence"]): boolean {
  return Object.values(evidence).some((value) => value !== null && value !== "");
}

function isRestorable(data: EventDetail, field: string): boolean {
  if (!(EDITABLE_EVENT_FIELDS as readonly string[]).includes(field)) return false;
  const latest = [...data.overrides].reverse().find((override) => override.field === field);
  return Boolean(latest && latest.new_value !== data.original_values[field]);
}

const FEE_TYPES = ["NETWORK_FEE", "GAS_FEE", "EXCHANGE_FEE", "TRADING_FEE", "FUNDING_FEE", "LIGHTNING_FEE"];

function FeesEditor({ eventId, fees, onChange }: { eventId: number; fees: FeeDetail[]; onChange: () => Promise<void> }) {
  const [adding, setAdding] = useState(false);
  const [editingFeeId, setEditingFeeId] = useState<number | null>(null);
  const [feeType, setFeeType] = useState("NETWORK_FEE");
  const [asset, setAsset] = useState("");
  const [amount, setAmount] = useState("");
  const [recipient, setRecipient] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const resetEditor = () => {
    setAdding(false);
    setEditingFeeId(null);
    setFeeType("NETWORK_FEE");
    setAsset("");
    setAmount("");
    setRecipient("");
  };

  const startEdit = (fee: FeeDetail) => {
    setEditingFeeId(fee.id);
    setFeeType(fee.fee_type);
    setAsset(fee.asset_symbol);
    setAmount(fee.amount);
    setRecipient(fee.fee_recipient ?? "");
    setError("");
    setAdding(true);
  };

  const saveFee = async () => {
    setBusy(true);
    setError("");
    try {
      const payload = {
        fee_type: feeType,
        asset_symbol: asset,
        amount,
        fee_recipient: recipient || null,
      };
      if (editingFeeId === null) {
        await postJson(`/api/events/${eventId}/fees`, payload);
      } else {
        await patchJson(`/api/events/${eventId}/fees/${editingFeeId}`, payload);
      }
      resetEditor();
      await onChange();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save fee");
    } finally {
      setBusy(false);
    }
  };

  const removeFee = async (feeId: number) => {
    setBusy(true);
    setError("");
    try {
      await deleteJson(`/api/events/${eventId}/fees/${feeId}`);
      await onChange();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not remove fee");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      {fees.length === 0 && <p className="text-xs text-soft">No fees recorded for this event.</p>}
      {fees.map((fee) => (
        <div key={fee.id} className="flex items-start justify-between gap-3 rounded-lg border border-line px-3 py-2.5 text-xs">
          <div className="min-w-0">
            <CopyableValue text={`${fee.amount} ${fee.asset_symbol}`}>
              <span className="font-medium text-ink">
                {fee.amount} {fee.asset_symbol} <span className="text-faint">· {formatType(fee.fee_type)}</span>
                {fee.manual && <span className="ml-1.5 font-mono text-[9px] uppercase tracking-wide text-warn">manual</span>}
              </span>
            </CopyableValue>
            {fee.fee_recipient && (
              <CopyableValue text={fee.fee_recipient}>
                <span className="mt-0.5 text-faint">to {fee.fee_recipient}</span>
              </CopyableValue>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button
              onClick={() => startEdit(fee)}
              disabled={busy}
              aria-label={`Edit fee ${fee.amount} ${fee.asset_symbol}`}
              className="rounded-lg p-1.5 text-faint transition-colors hover:bg-base hover:text-ink disabled:opacity-50"
            >
              <PencilLine size={13} />
            </button>
            <button
              onClick={() => void removeFee(fee.id)}
              disabled={busy}
              aria-label="Remove fee"
              className="rounded-lg p-1.5 text-faint transition-colors hover:bg-bad-soft hover:text-bad disabled:opacity-50"
            >
              <Trash2 size={13} />
            </button>
          </div>
        </div>
      ))}

      {adding ? (
        <div className="space-y-2.5 rounded-lg border border-dashed border-line p-3">
          <div className="grid grid-cols-2 gap-2.5">
            <Select value={feeType} onChange={(e) => setFeeType(e.target.value)} aria-label="Fee type">
              {FEE_TYPES.map((t) => (
                <option key={t} value={t}>{formatType(t)}</option>
              ))}
            </Select>
            <Input value={asset} onChange={(e) => setAsset(e.target.value.toUpperCase())} placeholder="Asset (BTC)" aria-label="Fee asset" />
            <Input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="Amount" inputMode="decimal" aria-label="Fee amount" />
            <Input value={recipient} onChange={(e) => setRecipient(e.target.value)} placeholder="Recipient (optional)" aria-label="Fee recipient" />
          </div>
          {error && <p className="text-[11px] text-bad">{error}</p>}
          <div className="flex gap-2">
            <Button size="sm" variant="primary" onClick={() => void saveFee()} loading={busy} disabled={!asset || !amount}>
              {editingFeeId === null ? "Add fee" : "Save fee"}
            </Button>
            <Button size="sm" variant="ghost" onClick={resetEditor}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Button size="sm" variant="ghost" icon={<Plus size={14} />} onClick={() => setAdding(true)}>
          Add fee
        </Button>
      )}
    </div>
  );
}

function PriceEditor({ eventId, event, valuations, onChange }: { eventId: number; event: EventDetail["event"]; valuations: EventDetail["valuations"]; onChange: () => Promise<void> }) {
  const [editingCurrency, setEditingCurrency] = useState<string | null>(null);
  const [unitPrice, setUnitPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const currencies = Array.from(new Set(["EUR", "SEK", ...valuations.map((v) => v.quote_currency)]));

  const startEdit = (currency: string, current?: string) => {
    setEditingCurrency(currency);
    setUnitPrice(current ?? "");
    setError("");
  };

  const save = async (currency: string) => {
    setBusy(true);
    setError("");
    try {
      await putJson(`/api/events/${eventId}/valuations/${currency}`, { unit_price: unitPrice });
      setEditingCurrency(null);
      await onChange();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not set price");
    } finally {
      setBusy(false);
    }
  };

  const restore = async (currency: string) => {
    setBusy(true); setError("");
    try { await postJson(`/api/events/${eventId}/valuations/${currency}/restore`, {}); await onChange(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not restore automatic price"); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-2">
      {currencies.map((currency) => {
        const valuation = valuations.find((v) => v.quote_currency === currency);
        const editing = editingCurrency === currency;
        return (
          <div key={currency} className="rounded-lg border border-line px-3 py-2.5">
            {editing ? (
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className="w-10 shrink-0 font-mono text-[10px] uppercase text-faint">{currency}</span>
                  <Input
                    value={unitPrice}
                    onChange={(e) => setUnitPrice(e.target.value)}
                    placeholder="Unit price"
                    inputMode="decimal"
                    autoFocus
                  />
                </div>
                <MarketPriceButton
                  symbol={event.asset_symbol}
                  network={event.network}
                  amount={event.primary_amount}
                  occurredAt={event.occurred_at}
                  currencies={[currency]}
                  label="Use market price at activity time"
                  onFilled={(prices) => {
                    if (prices[currency]) setUnitPrice(prices[currency].unit_price);
                  }}
                />
                {error && <p className="text-[11px] text-bad">{error}</p>}
                <div className="flex gap-2">
                  <Button size="sm" variant="primary" onClick={() => void save(currency)} loading={busy} disabled={!unitPrice}>
                    Save price
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditingCurrency(null)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-ink">
                    <MoneyValue value={valuation?.total_value ?? null} currency={currency as "EUR" | "SEK"} mono={false} />
                  </p>
                  <p className="mt-0.5 text-[11px] text-faint">
                    {valuation ? `${valuation.unit_price} per unit · ${valuation.provider} · ${formatType(valuation.method)} · ${valuation.granularity}` : "No valuation yet"}
                  </p>
                  {valuation && <p className="mt-0.5 text-[10px] text-faint">Requested {formatDateTime(valuation.requested_timestamp)} · observed {formatDateTime(valuation.observation_timestamp)} · fetched {formatDateTime(valuation.fetched_at)}</p>}
                  {valuation?.manual_override && (
                    <span className="mt-0.5 inline-block font-mono text-[9px] uppercase tracking-wide text-warn">manually set</span>
                  )}
                </div>
                <div className="flex items-center gap-1"><Button size="sm" variant="ghost" icon={<PencilLine size={13} />} onClick={() => startEdit(currency, valuation?.unit_price)}>{valuation ? "Edit" : "Set"}</Button>{valuation?.manual_override && <Button size="sm" variant="ghost" icon={<RotateCcw size={13} />} onClick={() => void restore(currency)} loading={busy}>Restore</Button>}</div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

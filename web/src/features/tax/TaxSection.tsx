import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ChevronRight, FileSpreadsheet, FileText, Landmark, Paperclip, ShieldCheck, Upload } from "lucide-react";
import type { AppSettings, Attachment, TaxCountry, TaxLanguage, TaxReadiness, TaxReport } from "../../types";
import { formatType } from "../../types";
import { deleteJson, getJson, patchJson, postJson, triggerDownload, uploadFileWithFields } from "../../lib/api";
import { useData } from "../../hooks/useData";
import { Card } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Field, Input, Select } from "../../components/ui/Field";
import { MoneyValue } from "../../components/domain/MoneyValue";
import { formatDateTime, cx } from "../../lib/format";

const CURRENT_YEAR = new Date().getFullYear();
const YEAR_OPTIONS = Array.from({ length: 8 }, (_, i) => CURRENT_YEAR - i);
const LANGUAGE_NAMES: Record<string, string> = { en: "English", es: "Español", sv: "Svenska" };

interface TaxSectionProps {
  onOpenEvent: (id: number) => void;
}

export function TaxSection({ onOpenEvent }: TaxSectionProps) {
  const { data: countries } = useData<TaxCountry[]>(() => getJson("/api/tax/countries"), []);
  const { data: languages } = useData<TaxLanguage[]>(() => getJson("/api/tax/languages"), []);
  const { data: settings, refresh: refreshSettings } = useData<AppSettings>(() => getJson("/api/settings"), []);

  const [country, setCountry] = useState("");
  const [year, setYear] = useState(CURRENT_YEAR);
  const [taxpayerName, setTaxpayerName] = useState("");
  const [language, setLanguage] = useState("en");
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    if (initialized || !settings || !countries?.length) return;
    setCountry(settings.default_country && countries.some((c) => c.code === settings.default_country) ? settings.default_country : countries[0].code);
    setYear(settings.default_tax_year ?? CURRENT_YEAR);
    setTaxpayerName(settings.taxpayer_name ?? "");
    setLanguage(settings.default_language ?? "en");
    setInitialized(true);
  }, [settings, countries, initialized]);

  const {
    data: readiness,
    loading: readinessLoading,
    refresh: refreshReadiness,
  } = useData<TaxReadiness>(() => getJson(`/api/tax/readiness?country=${country}&year=${year}`), [country, year], {
    onError: () => {},
  });

  const { data: reportHistory, refresh: refreshHistory } = useData<TaxReport[]>(
    () => getJson(`/api/tax/reports?country=${country}&tax_year=${year}`),
    [country, year],
  );

  const [activeReport, setActiveReport] = useState<TaxReport | null>(null);
  useEffect(() => {
    setActiveReport(reportHistory && reportHistory.length > 0 ? reportHistory[0] : null);
  }, [reportHistory]);

  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState("");

  const persistPicker = async (nextCountry: string, nextYear: number, nextName: string, nextLanguage: string) => {
    await patchJson("/api/settings", { default_country: nextCountry, default_tax_year: nextYear, taxpayer_name: nextName || null, default_language: nextLanguage });
    void refreshSettings();
  };

  const changeCountry = (value: string) => {
    setCountry(value);
    void persistPicker(value, year, taxpayerName, language);
  };
  const changeYear = (value: number) => {
    setYear(value);
    void persistPicker(country, value, taxpayerName, language);
  };
  const changeLanguage = (value: string) => {
    setLanguage(value);
    void persistPicker(country, year, taxpayerName, value);
  };
  const commitName = () => void persistPicker(country, year, taxpayerName, language);

  const generate = async () => {
    setGenerating(true);
    setGenError("");
    try {
      const report = await postJson<TaxReport>("/api/tax/reports", {
        country,
        tax_year: year,
        language,
      });
      setActiveReport(report);
      await refreshHistory();
    } catch (reason) {
      setGenError(reason instanceof Error ? reason.message : "Could not generate the report");
    } finally {
      setGenerating(false);
    }
  };

  const selectedCountry = countries?.find((c) => c.code === country);
  const currency = (selectedCountry?.currency ?? "EUR") as "EUR" | "SEK";

  return (
    <div className="space-y-8">
      {/* Country / year / taxpayer picker */}
      <Card className="flex flex-col gap-4">
        <div className="flex items-center gap-3.5">
          <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent">
            <Landmark size={20} />
          </span>
          <div>
            <p className="font-mono text-[10px] uppercase tracking-widest text-faint">Report context</p>
            <h2 className="mt-0.5 text-lg font-semibold tracking-tight text-ink">
              {selectedCountry?.name ?? country} · {year}
            </h2>
            <p className="mt-0.5 text-xs text-soft">The ledger is unchanged when you switch country or tax year.</p>
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Country" htmlFor="tax-country">
            <Select id="tax-country" value={country} onChange={(e) => changeCountry(e.target.value)}>
              {(countries ?? []).map((c) => (
                <option key={c.code} value={c.code}>{c.name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Tax year" htmlFor="tax-year">
            <Select id="tax-year" value={year} onChange={(e) => changeYear(Number(e.target.value))}>
              {YEAR_OPTIONS.map((y) => (
                <option key={y} value={y}>{y}</option>
              ))}
            </Select>
          </Field>
          <Field label="Report language" htmlFor="tax-language" hint="Applies to the PDF/CSV we generate — RP2's own raw output stays in Spanish for Spain">
            <Select id="tax-language" value={language} onChange={(e) => changeLanguage(e.target.value)}>
              {(languages ?? [{ code: "en", default: true }]).map((l) => (
                <option key={l.code} value={l.code}>{LANGUAGE_NAMES[l.code] ?? l.code}</option>
              ))}
            </Select>
          </Field>
          <Field label="Taxpayer name" htmlFor="tax-name" hint="Used as the report's identity line">
            <Input id="tax-name" value={taxpayerName} onChange={(e) => setTaxpayerName(e.target.value)} onBlur={commitName} placeholder="Your name" />
          </Field>
        </div>
        {selectedCountry && (
          <p className="text-[11px] text-faint">
            {selectedCountry.methods.join(", ")} · priced in {selectedCountry.currency} · {selectedCountry.engine === "rp2" ? "computed via RP2" : "computed natively"}
          </p>
        )}
      </Card>

      {/* Readiness */}
      <section>
        <h3 className="mb-3 font-mono text-[10px] uppercase tracking-widest text-faint">Data readiness</h3>
        {readinessLoading && !readiness ? (
          <Card><p className="text-xs text-soft">Checking...</p></Card>
        ) : readiness ? (
          <Card className={cx("border", readiness.ready ? "border-line" : "border-warn/30")}>
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <span className={cx("grid size-10 place-items-center rounded-xl", readiness.ready ? "bg-good-soft text-good" : "bg-warn-soft text-warn")}>
                  {readiness.ready ? <ShieldCheck size={20} /> : <AlertTriangle size={20} />}
                </span>
                <div>
                  <h4 className="text-sm font-semibold text-ink">Ready to report</h4>
                  <p className="mt-0.5 text-xs text-soft">{readiness.activity_count ?? readiness.event_count} activities in {year}</p>
                </div>
              </div>
              <Badge tone="success">Ready</Badge>
            </div>

            {readiness.issues.length > 0 && (
              <div className="mt-3 space-y-2 border-t border-line pt-3">
                {readiness.issues.map((issue, i) => (
                  <IssueRow key={i} issue={issue} onOpenEvent={onOpenEvent} />
                ))}
              </div>
            )}

            <div className="mt-4 flex flex-wrap items-center gap-2.5 border-t border-line pt-4">
              <Button size="sm" variant="ghost" onClick={() => void refreshReadiness()}>Re-check</Button>
              <Button
                size="sm"
                variant="primary"
                loading={generating}
                onClick={() => void generate()}
              >
                Generate tax report
              </Button>
              {genError && <span className="text-xs text-bad">{genError}</span>}
            </div>
          </Card>
        ) : null}
      </section>

      {/* Report result */}
      {activeReport && (
        <section>
          <h3 className="mb-3 font-mono text-[10px] uppercase tracking-widest text-faint">Report result</h3>
          <ReportSummary report={activeReport} currency={currency} />
        </section>
      )}

      {/* History */}
      {reportHistory && reportHistory.length > 1 && (
        <section>
          <h3 className="mb-3 font-mono text-[10px] uppercase tracking-widest text-faint">Previous reports for {year}</h3>
          <Card className="!p-0 overflow-hidden">
            {reportHistory.map((report) => (
              <button
                key={report.id}
                onClick={() => setActiveReport(report)}
                className={cx(
                  "flex w-full items-center justify-between border-b border-line px-4 py-3 text-left text-xs last:border-b-0 hover:bg-base",
                  activeReport?.id === report.id && "bg-base",
                )}
              >
                <span>
                  Generated {formatDateTime(report.generated_at)} · {report.method}
                </span>
                <ChevronRight size={14} className="text-faint" />
              </button>
            ))}
          </Card>
        </section>
      )}

      {/* Universal exports */}
      <section>
        <h3 className="mb-3 font-mono text-[10px] uppercase tracking-widest text-faint">Universal exports</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <ExportRow icon={<FileSpreadsheet size={19} />} title="Full Ledger CSV" text="Every event, fee, valuation, and override — jurisdiction-neutral." onDownload={() => triggerDownload("/api/export/ledger.csv")} />
          <ExportRow icon={<FileText size={19} />} title="Accountant PDF" text="Jurisdiction-neutral ledger summary — no country's tax rules applied." onDownload={() => triggerDownload(`/api/export/accountant.pdf?language=${language}`)} />
        </div>
      </section>

      {/* Supporting documents */}
      <section>
        <h3 className="mb-3 font-mono text-[10px] uppercase tracking-widest text-faint">Supporting documents</h3>
        <AttachmentsPanel />
      </section>
    </div>
  );
}

const ATTACHMENT_KINDS = [
  { value: "receipt", label: "Receipt" },
  { value: "invoice", label: "Invoice" },
  { value: "exchange_statement", label: "Exchange statement" },
  { value: "csv_export", label: "CSV export" },
  { value: "staking_statement", label: "Staking statement" },
  { value: "payment_confirmation", label: "Payment confirmation" },
  { value: "other", label: "Other" },
];

function AttachmentsPanel() {
  const { data: attachments, refresh } = useData<Attachment[]>(() => getJson("/api/attachments"), []);
  const [kind, setKind] = useState("other");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const upload = async (file: File) => {
    setUploading(true);
    setError("");
    try {
      await uploadFileWithFields<Attachment>("/api/attachments", file, { kind });
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const remove = async (id: number) => {
    await deleteJson(`/api/attachments/${id}`);
    await refresh();
  };

  return (
    <Card>
      <p className="text-xs text-soft">
        Receipts, invoices, exchange statements, and other supporting documents for a report. Stored encrypted at rest,
        separate from the canonical ledger.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-2.5">
        <Field label="Kind" htmlFor="attachment-kind" className="w-52">
          <Select id="attachment-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            {ATTACHMENT_KINDS.map((k) => (
              <option key={k.value} value={k.value}>{k.label}</option>
            ))}
          </Select>
        </Field>
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
            e.target.value = "";
          }}
        />
        <Button size="sm" icon={<Upload size={14} />} loading={uploading} onClick={() => fileInputRef.current?.click()}>
          Upload file
        </Button>
        {error && <span className="text-xs text-bad">{error}</span>}
      </div>

      {attachments && attachments.length > 0 ? (
        <div className="mt-4 space-y-2 border-t border-line pt-4">
          {attachments.map((a) => (
            <div key={a.id} className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2.5 text-xs">
              <div className="flex min-w-0 items-center gap-2.5">
                <Paperclip size={14} className="shrink-0 text-faint" />
                <div className="min-w-0">
                  <p className="truncate font-medium text-ink">{a.filename}</p>
                  <p className="mt-0.5 text-[11px] text-faint">
                    {ATTACHMENT_KINDS.find((k) => k.value === a.kind)?.label ?? a.kind} · {(a.size_bytes / 1024).toFixed(1)} KB ·{" "}
                    {formatDateTime(a.uploaded_at)}
                  </p>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <button onClick={() => triggerDownload(`/api/attachments/${a.id}/file`)} className="font-medium text-accent hover:underline">
                  Download
                </button>
                <button onClick={() => void remove(a.id)} className="font-medium text-bad hover:underline">
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-3 text-xs text-faint">No supporting documents uploaded yet.</p>
      )}
    </Card>
  );
}

function IssueRow({
  issue,
  onOpenEvent,
}: {
  issue: TaxReadiness["issues"][number];
  onOpenEvent: (id: number) => void;
}) {
  return (
    <div className="flex items-start gap-2.5 text-xs">
      <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warn" />
      <div className="min-w-0 flex-1">
        <p className="font-medium text-ink">{issue.title}</p>
        <p className="mt-0.5 text-soft">{issue.detail}</p>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          {issue.event_id !== null && (
            <button onClick={() => onOpenEvent(issue.event_id as number)} className="font-medium text-accent hover:underline">
              Open event #{issue.event_id} →
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function ExportRow({ icon, title, text, onDownload, disabled }: { icon: React.ReactNode; title: string; text: string; onDownload: () => void; disabled?: boolean }) {
  return (
    <Card className={cx("flex items-start gap-3.5", disabled && "opacity-55")}>
      <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent">{icon}</span>
      <div className="min-w-0 flex-1">
        <h4 className="text-sm font-semibold text-ink">{title}</h4>
        <p className="mt-0.5 text-xs text-soft">{text}</p>
        <div className="mt-3">
          <Button size="sm" disabled={disabled} onClick={onDownload}>Download</Button>
        </div>
      </div>
    </Card>
  );
}

function ReportSummary({ report, currency }: { report: TaxReport; currency: "EUR" | "SEK" }) {
  const summary = report.summary;
  const netGain = Number(summary.total_short_term_gain) + Number(summary.total_long_term_gain);
  return (
    <div className="space-y-4">
      <Card>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <Stat label="Net gain/loss" value={<MoneyValue value={netGain} currency={currency} />} />
          <Stat label="Short-term" value={<MoneyValue value={summary.total_short_term_gain} currency={currency} />} />
          <Stat label="Long-term" value={<MoneyValue value={summary.total_long_term_gain} currency={currency} />} />
          <Stat label="Income" value={<MoneyValue value={summary.total_income} currency={currency} />} />
          <Stat label="Fees" value={<MoneyValue value={summary.total_fees} currency={currency} />} />
        </div>
        <div className="mt-4 flex flex-wrap gap-2.5 border-t border-line pt-4">
          <Button size="sm" icon={<FileText size={14} />} onClick={() => triggerDownload(`/api/tax/reports/${report.id}/pdf`)}>PDF</Button>
          <Button size="sm" icon={<FileSpreadsheet size={14} />} onClick={() => triggerDownload(`/api/tax/reports/${report.id}/tax-csv`)}>Tax CSV</Button>
          {report.has_rp2_outputs && (
            <Button size="sm" icon={<FileSpreadsheet size={14} />} onClick={() => triggerDownload(`/api/tax/reports/${report.id}/rp2-outputs.zip`)}>
              RP2 raw output
            </Button>
          )}
        </div>
        <div className="mt-3 border-t border-line pt-3 font-mono text-[10px] text-faint">
          <p>Ledger hash {report.ledger_snapshot_hash.slice(0, 16)}… · Price dataset hash {report.price_dataset_hash.slice(0, 16)}…</p>
        </div>
      </Card>

      {(summary.included_activity_count !== undefined || summary.schedule_only_activity_count !== undefined) && (
        <Card>
          <h4 className="mb-2 text-xs font-semibold text-ink">Activity coverage</h4>
          <div className="grid grid-cols-2 gap-4 text-center sm:grid-cols-4">
            <Stat label="Activities included" value={summary.included_activity_count ?? 0} />
            <Stat label="Schedule only" value={summary.schedule_only_activity_count ?? 0} />
            <Stat label="Activities shown" value={summary.event_schedule_total} />
            <Stat label="Warnings" value={summary.warnings.length} />
          </div>
        </Card>
      )}

      {summary.acquisition_rows.length > 0 && (
        <TaxTable
          title="Acquisitions"
          headers={["Asset", "Category", "Quantity", "Cost basis"]}
          rows={summary.acquisition_rows.map((row) => [row.asset, formatType(row.category), row.quantity, <MoneyValue value={row.cost_basis} currency={currency} />])}
        />
      )}

      {summary.gain_loss_rows.length > 0 && (
        <Card className="!p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-line bg-base/60 text-left font-mono text-[10px] uppercase tracking-widest text-faint">
                  <th className="px-4 py-2.5">Asset</th>
                  <th className="px-4 py-2.5">Category</th>
                  <th className="px-4 py-2.5">Term</th>
                  <th className="px-4 py-2.5">Quantity</th>
                  <th className="px-4 py-2.5">Proceeds</th>
                  <th className="px-4 py-2.5">Cost basis</th>
                  <th className="px-4 py-2.5">Gain/loss</th>
                </tr>
              </thead>
              <tbody>
                {summary.gain_loss_rows.map((row, i) => (
                  <tr key={i} className="border-b border-line last:border-b-0">
                    <td className="px-4 py-2.5 font-medium text-ink">{row.asset}</td>
                    <td className="px-4 py-2.5 text-soft">{formatType(row.category)}</td>
                    <td className="px-4 py-2.5 text-soft">{row.term ? formatType(row.term) : "—"}</td>
                    <td className="px-4 py-2.5 font-mono text-ink">{row.quantity}</td>
                    <td className="px-4 py-2.5"><MoneyValue value={row.proceeds} currency={currency} /></td>
                    <td className="px-4 py-2.5"><MoneyValue value={row.cost_basis} currency={currency} /></td>
                    <td className={cx("px-4 py-2.5 font-medium", Number(row.gain_loss) < 0 ? "text-bad" : "text-good")}>
                      <MoneyValue value={row.gain_loss} currency={currency} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {summary.income_rows.length > 0 && (
        <Card className="!p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-line bg-base/60 text-left font-mono text-[10px] uppercase tracking-widest text-faint">
                  <th className="px-4 py-2.5">Asset</th>
                  <th className="px-4 py-2.5">Category</th>
                  <th className="px-4 py-2.5">Quantity</th>
                  <th className="px-4 py-2.5">Value</th>
                </tr>
              </thead>
              <tbody>
                {summary.income_rows.map((row, i) => (
                  <tr key={i} className="border-b border-line last:border-b-0">
                    <td className="px-4 py-2.5 font-medium text-ink">{row.asset}</td>
                    <td className="px-4 py-2.5 text-soft">{formatType(row.category)}</td>
                    <td className="px-4 py-2.5 font-mono text-ink">{row.quantity}</td>
                    <td className="px-4 py-2.5"><MoneyValue value={row.fiat_value} currency={currency} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {summary.transfer_rows.length > 0 && (
        <TaxTable
          title="Transfers (internal, non-taxable)"
          headers={["Date", "Asset", "Quantity", "From", "To"]}
          rows={summary.transfer_rows.map((row) => [row.occurred_at.slice(0, 10), row.asset, row.quantity, row.from_label, row.to_label])}
        />
      )}

      {summary.correction_rows.length > 0 && (
        <TaxTable
          title="Manual corrections"
          headers={["Event", "Field", "Old value", "New value", "Changed"]}
          rows={summary.correction_rows.map((row) => [`#${row.event_id}`, row.field, row.old_value || "—", row.new_value || "—", row.changed_at.slice(0, 10)])}
        />
      )}

      {summary.event_schedule_rows.length > 0 && (
        <div>
          <TaxTable
            title="Detailed event schedule"
            headers={["Date", "Type", "Asset", "Amount", "From wallet", "To wallet"]}
            rows={summary.event_schedule_rows.map((row) => [row.occurred_at.slice(0, 10), formatType(row.event_type), row.asset, row.amount, row.source_wallet ?? "—", row.destination_wallet ?? "—"])}
          />
          {summary.event_schedule_total > summary.event_schedule_rows.length && (
            <p className="mt-2 text-[11px] text-faint">
              Showing {summary.event_schedule_rows.length} of {summary.event_schedule_total} events — download the Full Ledger CSV for the complete list.
            </p>
          )}
        </div>
      )}

      {summary.warnings.length > 0 && (
        <Card>
          <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-warn">
            <AlertTriangle size={13} /> Methodology notes
          </h4>
          <ul className="space-y-1.5 text-[11px] text-soft">
            {summary.warnings.map((w, i) => (
              <li key={i}>- {w}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function TaxTable({ title, headers, rows }: { title: string; headers: string[]; rows: React.ReactNode[][] }) {
  return (
    <Card className="!p-0 overflow-hidden">
      <h4 className="px-4 pt-4 text-xs font-semibold text-ink">{title}</h4>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-line bg-base/60 text-left font-mono text-[10px] uppercase tracking-widest text-faint">
              {headers.map((h) => (
                <th key={h} className="px-4 py-2.5">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-line last:border-b-0">
                {row.map((cell, j) => (
                  <td key={j} className={cx("px-4 py-2.5", j === 0 ? "font-medium text-ink" : "text-soft")}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="font-mono text-[10px] uppercase tracking-widest text-faint">{label}</p>
      <p className="mt-1 text-lg font-semibold text-ink">{value}</p>
    </div>
  );
}

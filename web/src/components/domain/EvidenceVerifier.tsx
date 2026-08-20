import { useState } from "react";
import { FolderInput, ShieldCheck, ShieldX } from "lucide-react";
import { ApiError, uploadFile } from "../../lib/api";
import { Button } from "../ui/Button";
import { Input } from "../ui/Field";

interface VerificationResult {
  valid: boolean;
  records: number;
  failures: Array<{ file: string; reason: string }>;
  ready_for_review?: boolean;
  message?: string;
}

/** Verify an archive before a user relies on it for recovery or audit work. */
export function EvidenceVerifier() {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<VerificationResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (path: string, importing = false) => {
    if (!file) return;
    setBusy(true); setError(""); setResult(null);
    try {
      let response: VerificationResult;
      try {
        response = await uploadFile<VerificationResult>(path, file);
      } catch (reason) {
        // Keep the UI compatible with an API process that predates the
        // review-import route; verification is the same safe operation.
        if (importing && reason instanceof ApiError && reason.status === 404) {
          response = await uploadFile<VerificationResult>("/api/reports/evidence/verify", file);
        } else {
          throw reason;
        }
      }
      setResult({
        ...response,
        message: importing && response.valid
          ? "Evidence archive imported for review; no ledger data was changed."
          : response.message,
      });
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not verify archive"); }
    finally { setBusy(false); }
  };
  return <div className="rounded-xl border border-line bg-surface p-4">
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
      <label className="min-w-0 flex-1"><span className="text-xs font-semibold text-ink">Evidence archive</span><span className="mt-0.5 block text-[11px] text-faint">Upload a ZIP to verify its hashes or import it for review. It will not modify the ledger.</span><Input className="mt-2 text-xs" type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" icon={<ShieldCheck size={13} />} onClick={() => void submit("/api/reports/evidence/verify")} loading={busy} disabled={!file}>Verify</Button>
        <Button size="sm" variant="primary" icon={<FolderInput size={13} />} onClick={() => void submit("/api/reports/evidence/import", true)} loading={busy} disabled={!file}>Import for review</Button>
      </div>
    </div>
    {result && <p className={result.valid ? "mt-3 inline-flex items-center gap-1.5 text-xs text-good" : "mt-3 inline-flex items-center gap-1.5 text-xs text-bad"}>{result.valid ? <ShieldCheck size={14} /> : <ShieldX size={14} />}{result.message ?? (result.valid ? `${result.records} record${result.records === 1 ? "" : "s"} verified.` : `Verification failed: ${result.failures[0]?.reason ?? "unknown error"}`)}</p>}
    {error && <p className="mt-3 text-xs text-bad">{error}</p>}
  </div>;
}

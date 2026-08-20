import { useState } from "react";
import { ArchiveRestore, CheckCircle2, Download, HardDriveDownload, ShieldCheck, TriangleAlert, UploadCloud } from "lucide-react";
import type { Backup, BackupState } from "../../types";
import { getJson, postJson, triggerDownload, uploadFile } from "../../lib/api";
import { useData } from "../../hooks/useData";
import { Card, CardHeader } from "../ui/Card";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Input } from "../ui/Field";
import { Skeleton } from "../ui/Skeleton";
import { useConfirmDialog } from "../ui/ConfirmDialog";
import { formatBytes, formatDateTime, relativeTime } from "../../lib/format";

interface BackupStatusProps {
  detailed?: boolean;
  actions?: boolean;
}

export function BackupStatus({ detailed = false, actions = true }: BackupStatusProps) {
  const { data, loading, error, refresh } = useData<BackupState>(() => getJson("/api/backups"), []);
  const [working, setWorking] = useState(false);
  const [actionError, setActionError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [uploadFileState, setUploadFileState] = useState<File | null>(null);
  const [uploadInputKey, setUploadInputKey] = useState(0);
  const { confirm, confirmDialog } = useConfirmDialog();

  const runBackup = async () => {
    setWorking(true);
    setActionError("");
    setActionMessage("");
    try {
      await postJson("/api/backups/run", {});
      await refresh();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Backup failed");
    } finally {
      setWorking(false);
    }
  };

  const verify = async (id: number) => {
    setWorking(true);
    setActionError("");
    setActionMessage("");
    try {
      await postJson(`/api/backups/${id}/verify`, {});
      await refresh();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Verification failed");
    } finally {
      setWorking(false);
    }
  };

  const restore = async (id: number, createdAt: string) => {
    if (!(await confirm({
      title: "Restore this backup?",
      message: `The backup from ${formatDateTime(createdAt)} will replace the current ledger. This is destructive and cannot be undone.`,
      confirmLabel: "Restore backup",
      destructive: true,
    }))) return;
    setWorking(true);
    setActionError("");
    setActionMessage("");
    try {
      await postJson(`/api/backups/${id}/restore`, {});
      window.location.reload();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Restore failed");
      setWorking(false);
    }
  };

  const uploadBackup = async () => {
    if (!uploadFileState) return;
    setWorking(true);
    setActionError("");
    setActionMessage("");
    try {
      const uploaded = await uploadFile<Backup>("/api/backups/upload", uploadFileState);
      setUploadFileState(null);
      setUploadInputKey((key) => key + 1);
      setActionMessage(`Uploaded and verified backup from ${formatDateTime(uploaded.created_at)}.`);
      await refresh();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Backup upload failed");
    } finally {
      setWorking(false);
    }
  };

  if (loading) return <Skeleton className={detailed ? "h-56" : "h-24"} />;

  const latest = data?.backups[0];

  return (
    <Card>
      {confirmDialog}
      <CardHeader
        eyebrow="Backup status"
        title={data?.has_backup_today ? "Protected today" : "Backup recommended"}
        subtitle={
          latest
            ? `Latest encrypted snapshot ${relativeTime(latest.created_at)}`
            : "No encrypted snapshot has been created yet."
        }
        action={
          <span className={data?.has_backup_today ? "text-good" : "text-warn"}>
            {data?.has_backup_today ? <CheckCircle2 size={22} /> : <TriangleAlert size={22} />}
          </span>
        }
      />

      {error && <p className="mb-3 rounded-lg bg-bad-soft px-3 py-2 text-xs text-bad">{error}</p>}
      {actionError && <p className="mb-3 rounded-lg bg-bad-soft px-3 py-2 text-xs text-bad">{actionError}</p>}
      {actionMessage && <p className="mb-3 rounded-lg bg-good-soft px-3 py-2 text-xs text-good">{actionMessage}</p>}

      {actions && <div className="flex flex-wrap gap-2.5">
          <Button variant="primary" size="sm" icon={<UploadCloud size={14} />} loading={working} onClick={() => void runBackup()}>
            Run encrypted backup
          </Button>
          {latest && (
            <Button size="sm" variant="secondary" icon={<ShieldCheck size={14} />} loading={working} onClick={() => void verify(latest.id)}>
              {latest.verified ? "Re-verify latest" : "Verify latest"}
            </Button>
          )}
        </div>}

      {detailed && actions && <div className="mt-5 border-t border-line pt-4">
        <p className="text-xs font-semibold text-ink">Upload an encrypted backup</p>
        <p className="mt-0.5 text-[11px] text-faint">The file is verified before it is added. Uploading does not restore or replace the current ledger.</p>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
          <Input key={uploadInputKey} type="file" accept=".enc,.db.enc,application/octet-stream" className="text-xs" onChange={(event) => setUploadFileState(event.target.files?.[0] ?? null)} />
          <Button size="sm" variant="secondary" icon={<UploadCloud size={14} />} loading={working} disabled={!uploadFileState} onClick={() => void uploadBackup()}>
            Upload backup
          </Button>
        </div>
      </div>}

      {detailed && data && data.backups.length > 0 && (
        <div className="mt-5 space-y-2 border-t border-line pt-4">
          <p className="font-mono text-[10px] uppercase tracking-widest text-faint">Recent snapshots</p>
          {data.backups.slice(0, 5).map((backup) => (
            <div key={backup.id} className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2">
              <div className="flex items-center gap-2.5">
                <HardDriveDownload size={15} className="text-soft" />
                <div>
                  <p className="text-sm text-ink">{formatDateTime(backup.created_at)}</p>
                  <p className="text-[11px] text-faint">{formatBytes(backup.size_bytes)}</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={backup.verified ? "success" : "neutral"} dot>
                  {backup.verified ? "Verified" : "Not verified"}
                </Badge>
                <Button size="sm" variant="ghost" icon={<Download size={13} />} onClick={() => triggerDownload(`/api/backups/${backup.id}/download`)} disabled={working}>
                  Download
                </Button>
                {actions && <>
                  <Button size="sm" variant="ghost" onClick={() => void verify(backup.id)} disabled={working}>
                    Verify
                  </Button>
                  <Button size="sm" variant="ghost" icon={<ArchiveRestore size={13} />} onClick={() => void restore(backup.id, backup.created_at)} disabled={working}>
                    Restore
                  </Button>
                </>}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

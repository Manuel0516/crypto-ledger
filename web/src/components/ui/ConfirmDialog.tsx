import { useCallback, useState } from "react";
import { AlertCard } from "./AlertCard";
import { Button } from "./Button";
import { Dialog } from "./Dialog";

export interface ConfirmRequest {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
}

/** Shared confirmation surface for destructive or security-sensitive actions. */
export function useConfirmDialog() {
  const [request, setRequest] = useState<(ConfirmRequest & { resolve: (value: boolean) => void }) | null>(null);

  const confirm = useCallback((options: ConfirmRequest) => new Promise<boolean>((resolve) => {
    setRequest({ ...options, resolve });
  }), []);

  const finish = (value: boolean) => {
    request?.resolve(value);
    setRequest(null);
  };

  const dialog = request ? (
    <Dialog
      open
      onClose={() => finish(false)}
      title={request.title}
      eyebrow="Confirmation required"
      narrow
      footer={<><Button variant="ghost" onClick={() => finish(false)}>{request.cancelLabel ?? "Cancel"}</Button><Button variant={request.destructive ? "danger" : "primary"} onClick={() => finish(true)}>{request.confirmLabel ?? "Continue"}</Button></>}
    >
      <AlertCard tone={request.destructive ? "danger" : "warning"} role="alert">
        <p>{request.message}</p>
      </AlertCard>
    </Dialog>
  ) : null;

  return { confirm, confirmDialog: dialog };
}

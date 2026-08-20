import { Badge, type BadgeTone } from "../ui/Badge";

export function statusTone(status: string): BadgeTone {
  switch (status) {
    case "COMPLETE":
    case "connected":
    case "ok":
      return "success";
    case "REQUIRES_REVIEW":
    case "warning":
    case "not_configured":
      return "warning";
    case "failed":
    case "error":
      return "danger";
    case "archived":
      return "neutral";
    case "paused":
      return "warning";
    default:
      return "neutral";
  }
}

export function statusLabel(status: string): string {
  switch (status) {
    case "COMPLETE":
      return "Complete";
    case "REQUIRES_REVIEW":
      return "Review";
    case "connected":
      return "Connected";
    case "not_configured":
      return "Not configured";
    case "ok":
      return "OK";
    case "archived":
      return "Archived";
    case "paused":
      return "Paused";
    case "error":
      return "Error";
    default:
      return status.split("_").join(" ");
  }
}

export function StatusPill({ status, dot = true }: { status: string; dot?: boolean }) {
  return (
    <Badge tone={statusTone(status)} dot={dot}>
      {statusLabel(status)}
    </Badge>
  );
}
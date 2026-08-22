import { Badge, type BadgeTone } from "../ui/Badge";
import { eventMeta } from "../../types";

const kindTone: Record<string, BadgeTone> = {
  trade: "accent",
  income: "success",
  move: "info",
  review: "warning",
  other: "neutral",
};

export function EventTypeBadge({ eventType }: { eventType: string }) {
  const meta = eventMeta(eventType);
  return <Badge className="max-w-full overflow-hidden text-ellipsis whitespace-nowrap" tone={kindTone[meta.kind] ?? "neutral"}>{meta.label}</Badge>;
}

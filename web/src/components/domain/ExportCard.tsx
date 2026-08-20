import type { ReactNode } from "react";
import { Download } from "lucide-react";
import { Card } from "../ui/Card";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { cx } from "../../lib/format";

interface ExportCardProps {
  icon: ReactNode;
  title: string;
  text: string;
  disabled?: boolean;
  badge?: string;
  onDownload?: () => void;
}

export function ExportCard({ icon, title, text, disabled = false, badge, onDownload }: ExportCardProps) {
  return (
    <Card className={cx("flex flex-col transition-opacity", disabled && "opacity-55")}>
      <div className="flex items-start justify-between gap-3">
        <span className="grid size-10 place-items-center rounded-xl bg-accent-soft text-accent">{icon}</span>
        {badge && <Badge tone="neutral">{badge}</Badge>}
      </div>
      <h3 className="mt-4 text-sm font-semibold text-ink">{title}</h3>
      <p className="mt-1 flex-1 text-xs text-soft">{text}</p>
      <div className="mt-4">
        <Button size="sm" icon={<Download size={14} />} disabled={disabled} onClick={onDownload}>
          Download
        </Button>
      </div>
    </Card>
  );
}
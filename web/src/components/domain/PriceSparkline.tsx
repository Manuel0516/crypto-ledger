import { TrendingDown, TrendingUp } from "lucide-react";
import type { PricePoint } from "../../types";
import { cx } from "../../lib/format";

type SparklineSize = "compact" | "large";

interface PriceSparklineProps {
  points: PricePoint[];
  changePct: number | null;
  label?: string;
  className?: string;
  /** "compact" for tight inline spots; "large" for a prominent, full-width feature. */
  size?: SparklineSize;
}

const WIDTH = 100;
const HEIGHT = 30;

// Fades the line and its fill in from the left edge, rather than starting
// abruptly — reads as a soft trend rather than a hard-edged data plot.
const FADE_MASK = "linear-gradient(to right, transparent 0%, rgba(0,0,0,0.55) 14%, black 34%)";

/** Live price trend chart — market price movement, independent of the
 * user's own holdings (contrast with the ledger-activity sparkline in the
 * hero, which counts the user's own events, not price). */
export function PriceSparkline({ points, changePct, label = "7d", className, size = "compact" }: PriceSparklineProps) {
  if (points.length < 2) return null;

  const large = size === "large";
  const prices = points.map((p) => p.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const span = max - min || 1;

  const coords = points.map((p, i) => {
    const x = (i / (points.length - 1)) * WIDTH;
    const y = HEIGHT - ((p.price - min) / span) * HEIGHT;
    return [x, y] as const;
  });

  const positive = (changePct ?? 0) >= 0;
  const lineColor = positive ? "var(--positive)" : "var(--negative)";
  const polylinePoints = coords.map(([x, y]) => `${x},${y}`).join(" ");
  const areaPath = `M${coords[0][0]},${HEIGHT} L${polylinePoints.split(" ").join(" L")} L${coords[coords.length - 1][0]},${HEIGHT} Z`;
  const gradientId = `price-spark-${positive ? "up" : "down"}-${size}`;

  const chart = (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      preserveAspectRatio="none"
      className={cx("w-full", large ? "h-9" : "h-5 max-w-16")}
      style={{ WebkitMaskImage: FADE_MASK, maskImage: FADE_MASK }}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={lineColor} stopOpacity={large ? "0.2" : "0.3"} />
          <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#${gradientId})`} stroke="none" />
      <polyline
        points={polylinePoints}
        fill="none"
        stroke={lineColor}
        strokeWidth={large ? "1.6" : "2"}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );

  if (large) {
    return (
      <div className={cx("w-full", className)}>
        <div className="flex items-center justify-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-widest text-faint">Price</span>
          {changePct !== null && (
            <span className={cx("inline-flex items-center gap-1 text-[11px] font-medium", positive ? "text-good" : "text-bad")}>
              {positive ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
              {positive ? "+" : ""}
              {changePct}%<span className="text-faint"> · {label}</span>
            </span>
          )}
        </div>
        <div className="mt-1">{chart}</div>
      </div>
    );
  }

  return (
    <div className={cx("flex items-center gap-2 opacity-90", className)}>
      {chart}
    </div>
  );
}

import { Activity as ActivityIcon, CalendarDays, Layers, PencilLine, ShieldCheck } from "lucide-react";
import type { LedgerStats } from "../../lib/ledgerStats";
import { MoneyValue } from "./MoneyValue";
import { formatAge, formatNumber, cx } from "../../lib/format";

interface PortfolioValueProps {
  displayValue: number;
  displayCurrency: string;
  secondaryValue: number;
  secondaryCurrency: string;
  assets: number;
  stats: LedgerStats;
  className?: string;
}

/** Overview page hero: portfolio value, ledger-at-a-glance pills, and a real
 * 7-day activity sparkline — one light, quiet card (plan §88's "keep it
 * exceptionally clean"), not a standalone dark block. */
export function PortfolioValue({ displayValue, displayCurrency, secondaryValue, secondaryCurrency, assets, stats, className }: PortfolioValueProps) {
  const max = Math.max(1, ...stats.weekly.map((entry) => entry.count));

  return (
    <div className={cx("relative overflow-hidden rounded-card border border-line bg-surface p-6 shadow-card sm:p-7", className)}>
      <div className="pointer-events-none absolute -right-20 -top-24 size-64 rounded-full bg-accent-soft opacity-70 blur-3xl" aria-hidden="true" />

      <div className="relative flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <p className="font-mono text-[10px] uppercase tracking-widest text-faint">Total portfolio · estimate</p>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-4xl font-semibold tracking-tight text-ink tabular sm:text-5xl">
              <MoneyValue value={displayValue} currency={displayCurrency} mono />
            </span>
            <span className="font-mono text-sm text-faint">{displayCurrency}</span>
          </div>
          <div className="mt-1.5 flex items-baseline gap-2">
            <span className="font-mono text-base text-soft">
              <MoneyValue value={secondaryValue} currency={secondaryCurrency} mono />
            </span>
            <span className="font-mono text-xs text-faint">{secondaryCurrency}</span>
          </div>

          <div className="mt-5 flex flex-wrap gap-2">
            <HeroPill icon={<Layers size={12} />} label={`${formatNumber(assets, 0)} assets`} />
            <HeroPill icon={<ActivityIcon size={12} />} label={`${formatNumber(stats.last24h, 0)} events · 24h`} />
            <HeroPill icon={<PencilLine size={12} />} label={`${formatNumber(stats.modified, 0)} manual edits`} />
            <HeroPill
              icon={<CalendarDays size={12} />}
              label={stats.ledgerSince ? `Ledger since ${formatAge(stats.ledgerSince)}` : "No history yet"}
            />
          </div>
        </div>

        <div className="w-full shrink-0 lg:w-44">
          <div className="flex items-center justify-between">
            <p className="text-[11px] font-medium text-soft">Last 7 days</p>
            <p className="font-mono text-[10px] text-faint">events</p>
          </div>
          <div className="mt-2.5 flex h-14 items-end gap-1.5">
            {stats.weekly.map((entry, index) => (
              <div
                key={index}
                className="group relative flex flex-1 flex-col items-center gap-1"
                title={`${entry.count} event${entry.count === 1 ? "" : "s"}`}
              >
                <div className="relative flex w-full flex-1 items-end rounded-full bg-base">
                  <div
                    className={cx("w-full rounded-full transition-all duration-300", entry.count ? "bg-accent" : "bg-transparent")}
                    style={{ height: entry.count ? `${Math.max(16, (entry.count / max) * 100)}%` : "8%" }}
                  />
                </div>
                <span className="text-[9px] text-faint">{entry.label.slice(0, 1)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="relative mt-6 flex items-center justify-between border-t border-line pt-3.5 text-[11px] text-faint">
        <span>Net holdings · latest available prices</span>
        <span className="inline-flex items-center gap-1.5 text-soft">
          <ShieldCheck size={13} className="text-accent" />
          Cached locally
        </span>
      </div>
    </div>
  );
}

function HeroPill({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-base px-2.5 py-1 text-[11px] font-medium text-soft">
      <span className="text-faint">{icon}</span>
      {label}
    </span>
  );
}

import { EvidenceVerifier } from "../components/domain/EvidenceVerifier";
import { TaxSection } from "../features/tax/TaxSection";

interface ReportsPageProps {
  onOpenEvent: (id: number) => void;
}

export function ReportsPage({ onOpenEvent }: ReportsPageProps) {
  return (
    <div className="space-y-8 animate-fade-in">
      <p className="max-w-xl text-sm text-soft">
        Generate reproducible exports from the canonical ledger. Tax interpretation stays in a country and year
        adapter — the ledger itself is jurisdiction-neutral.
      </p>

      <TaxSection onOpenEvent={onOpenEvent} />

      <section className="max-w-2xl">
        <EvidenceVerifier />
      </section>

    </div>
  );
}

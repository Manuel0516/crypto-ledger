import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "./Button";

type PageItem = number | "start-ellipsis" | "end-ellipsis";

interface PaginationProps {
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
}

function pageItems(page: number, pageCount: number): PageItem[] {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, index) => index + 1);
  const items: PageItem[] = [1];
  const start = Math.max(2, page - 1);
  const end = Math.min(pageCount - 1, page + 1);
  if (start > 2) items.push("start-ellipsis");
  for (let value = start; value <= end; value += 1) items.push(value);
  if (end < pageCount - 1) items.push("end-ellipsis");
  items.push(pageCount);
  return items;
}

export function Pagination({ page, pageCount, onPageChange }: PaginationProps) {
  if (pageCount <= 1) return null;
  return (
    <nav className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4" aria-label="Activity pages">
      <p className="text-xs text-faint">Page {page} of {pageCount}</p>
      <div className="flex items-center gap-1.5">
        <Button size="sm" variant="secondary" icon={<ChevronLeft size={14} />} disabled={page === 1} onClick={() => onPageChange(page - 1)} aria-label="Previous page">
          <span className="hidden sm:inline">Previous</span>
        </Button>
        <div className="flex items-center gap-1">
          {pageItems(page, pageCount).map((item) => item === "start-ellipsis" || item === "end-ellipsis" ? (
            <span key={item} className="grid size-8 place-items-center text-xs text-faint" aria-hidden="true">…</span>
          ) : (
            <button key={item} type="button" aria-label={`Go to page ${item}`} aria-current={item === page ? "page" : undefined} onClick={() => onPageChange(item)} className={item === page ? "grid size-8 place-items-center rounded-btn bg-accent-soft text-xs font-semibold text-accent" : "grid size-8 place-items-center rounded-btn text-xs font-semibold text-soft transition-colors hover:bg-base hover:text-ink"}>
              {item}
            </button>
          ))}
        </div>
        <Button size="sm" variant="secondary" icon={<ChevronRight size={14} />} disabled={page === pageCount} onClick={() => onPageChange(page + 1)} aria-label="Next page">
          <span className="hidden sm:inline">Next</span>
        </Button>
      </div>
    </nav>
  );
}

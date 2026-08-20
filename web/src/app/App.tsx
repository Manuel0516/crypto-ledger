import { useCallback, useEffect, useState } from "react";
import { Menu, Moon, RefreshCw, Settings, Sun } from "lucide-react";
import type { AppSettings, Page } from "../types";
import { useTheme } from "../hooks/useTheme";
import { getJson } from "../lib/api";
import { useData } from "../hooks/useData";
import { PRIMARY_NAV, routeToPage, type NavItem } from "./nav";
import { OverviewPage } from "../pages/OverviewPage";
import { AccountsPage } from "../pages/AccountsPage";
import { ActivityPage } from "../pages/ActivityPage";
import { ReportsPage } from "../pages/ReportsPage";
import { SettingsPage } from "../pages/SettingsPage";
import { EventDetailsDrawer } from "../components/domain/EventDetailsDrawer";
import { useScrollLock } from "../components/ui/overlay";
import { cx } from "../lib/format";

export function App() {
  const [page, setPage] = useState<Page>(routeToPage());
  const [navOpen, setNavOpen] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState<number | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const { data: appSettings, refresh: refreshSettings } = useData<AppSettings>(() => getJson("/api/settings"), []);
  const { theme, toggle } = useTheme(appSettings?.ui_theme ?? "system");

  useEffect(() => {
    window.localStorage.setItem("crypto-ledger:timezone", appSettings?.default_timezone ?? "UTC");
  }, [appSettings?.default_timezone]);

  useEffect(() => {
    const onSettingsChanged = () => { void refreshSettings(); };
    window.addEventListener("crypto-ledger:settings-changed", onSettingsChanged);
    return () => window.removeEventListener("crypto-ledger:settings-changed", onSettingsChanged);
  }, [refreshSettings]);

  useEffect(() => {
    const sync = () => setPage(routeToPage());
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const navigate = useCallback((next: Page) => {
    const item =
      next === "Settings"
        ? { hash: "#/settings" }
        : PRIMARY_NAV.find((nav) => nav.page === next);
    window.location.hash = item?.hash ?? "#/";
    setNavOpen(false);
  }, []);

  const refresh = () => {
    setRefreshKey((value) => value + 1);
    if (selectedEvent !== null) {
      setSelectedEvent(null);
    }
  };

  return (
    <div className="min-h-screen bg-bg text-ink">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r border-line bg-surface px-3.5 py-6 lg:flex">
        <Brand />
        <nav className="mt-8 space-y-0.5" aria-label="Primary">
          {PRIMARY_NAV.map(({ page: target, label, icon: Icon }: NavItem) => (
            <button key={target} onClick={() => navigate(target)} className={cx("nav-item w-full", page === target && "active")}>
              <Icon size={17} />
              {label}
            </button>
          ))}
        </nav>
        <div className="mt-auto space-y-5">
          <div className="border-t border-line pt-4">
            <button onClick={() => navigate("Settings")} className={cx("nav-item w-full", page === "Settings" && "active")}>
              <SettingsIcon size={17} />
              Settings
            </button>
          </div>
        </div>
      </aside>

      {/* Main column */}
      <div className="lg:pl-60">
        <header className="sticky top-0 z-20 border-b border-line bg-bg/80 backdrop-blur-md">
          <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
            <div className="flex min-w-0 items-center gap-3">
              <button
                onClick={() => setNavOpen(true)}
                aria-label="Open navigation"
                className="grid size-9 shrink-0 place-items-center rounded-lg border border-line bg-surface text-soft lg:hidden"
              >
                <Menu size={18} />
              </button>
              <div className="min-w-0">
                <p className="truncate font-mono text-[10px] uppercase tracking-widest text-faint">Private ledger / 2026</p>
                <h1 className="truncate text-lg font-semibold tracking-tight text-ink">{page}</h1>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button
                onClick={refresh}
                aria-label="Refresh data"
                title="Refresh"
                className="grid size-9 place-items-center rounded-lg border border-line bg-surface text-soft transition-colors hover:border-line-strong hover:text-ink"
              >
                <RefreshCw size={15} />
              </button>
              <button
                onClick={toggle}
                aria-label="Toggle theme"
                title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
                className="grid size-9 place-items-center rounded-lg border border-line bg-surface text-soft transition-colors hover:border-line-strong hover:text-ink"
              >
                {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-6xl px-4 pb-24 pt-6 sm:px-6 lg:pb-16 lg:pt-8">
          {page === "Overview" && (
            <OverviewPage key={`overview-${refreshKey}`} onOpenEvent={setSelectedEvent} navigate={navigate} />
          )}
          {page === "Linked Accounts" && <AccountsPage key={`accounts-${refreshKey}`} navigate={navigate} />}
          {page === "Activity" && <ActivityPage key={`activity-${refreshKey}`} onOpenEvent={setSelectedEvent} />}
          {page === "Reports" && <ReportsPage key={`reports-${refreshKey}`} onOpenEvent={setSelectedEvent} />}
          {page === "Settings" && <SettingsPage key={`settings-${refreshKey}`} navigate={navigate} />}
        </main>
      </div>

      {/* Mobile bottom navigation */}
      <nav
        aria-label="Primary"
        className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-line bg-surface/95 backdrop-blur-md lg:hidden"
      >
        {PRIMARY_NAV.map(({ page: target, label, icon: Icon }: NavItem) => (
          <button
            key={target}
            onClick={() => navigate(target)}
            className={cx(
              "flex flex-col items-center gap-1 py-2.5 text-[10px] font-medium transition-colors",
              page === target ? "text-accent" : "text-soft hover:text-ink",
            )}
            aria-current={page === target ? "page" : undefined}
          >
            <Icon size={19} />
            {label}
          </button>
        ))}
        <button onClick={() => navigate("Settings")} className={cx("flex flex-col items-center gap-1 py-2.5 text-[10px] font-medium transition-colors", page === "Settings" ? "text-accent" : "text-soft hover:text-ink")} aria-current={page === "Settings" ? "page" : undefined}>
          <Settings size={19} />
          Settings
        </button>
      </nav>

      {/* Mobile slide-over navigation */}
      {navOpen && <MobileNav open page={page} onNavigate={navigate} onClose={() => setNavOpen(false)} />}

      {selectedEvent !== null && (
        <EventDetailsDrawer eventId={selectedEvent} onClose={() => setSelectedEvent(null)} onChange={refresh} />
      )}
    </div>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <div className="grid size-12 place-items-center" aria-hidden="true">
        <img src="/crypto-ledger-logo.svg" alt="" className="size-full object-contain" />
      </div>
      <div>
        <p className="text-[15px] font-semibold leading-none tracking-tight text-ink">Ledger</p>
        <p className="mt-1 text-[10px] text-faint">private crypto records</p>
      </div>
    </div>
  );
}

function SettingsIcon({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function MobileNav({
  open,
  page,
  onNavigate,
  onClose,
}: {
  open: boolean;
  page: Page;
  onNavigate: (page: Page) => void;
  onClose: () => void;
}) {
  useScrollLock(open);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!open) return null;

  const items = [
    ...PRIMARY_NAV,
    { page: "Settings" as Page, label: "Settings", icon: SettingsIcon as unknown as typeof PRIMARY_NAV[number]["icon"], hash: "#/settings" },
  ];

  return (
    <div className="fixed inset-0 z-40 lg:hidden">
      <div className="absolute inset-0 bg-black/50 animate-fade-in" onClick={onClose} aria-hidden="true" />
      <div className="absolute inset-y-0 left-0 z-10 flex w-72 animate-slide-in-right flex-col bg-surface px-3.5 py-6 shadow-modal">
        <Brand />
        <nav className="mt-8 space-y-0.5">
          {items.map((item) => (
            <button
              key={item.page}
              onClick={() => onNavigate(item.page)}
              className={cx("nav-item w-full", page === item.page && "active")}
            >
              <item.icon size={17} />
              {item.label}
            </button>
          ))}
        </nav>
      </div>
    </div>
  );
}

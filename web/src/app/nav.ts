import type { LucideIcon } from "lucide-react";
import { Activity, FileText, LayoutDashboard, Settings, WalletCards } from "lucide-react";
import type { Page } from "../types";

export interface NavItem {
  page: Page;
  label: string;
  icon: LucideIcon;
  hash: string;
}

export const PRIMARY_NAV: NavItem[] = [
  { page: "Overview", label: "Overview", icon: LayoutDashboard, hash: "#/" },
  { page: "Linked Accounts", label: "Linked Accounts", icon: WalletCards, hash: "#/accounts" },
  { page: "Activity", label: "Activity", icon: Activity, hash: "#/activity" },
  { page: "Reports", label: "Reports", icon: FileText, hash: "#/reports" },
];

export const SETTINGS_ITEM: NavItem = { page: "Settings", label: "Settings", icon: Settings, hash: "#/settings" };

export function routeToPage(): Page {
  const route = window.location.hash.replace(/^#\/?/, "").toLowerCase();
  if (route.startsWith("accounts")) return "Linked Accounts";
  if (route.startsWith("activity")) return "Activity";
  if (route.startsWith("reports")) return "Reports";
  if (route.startsWith("settings")) return "Settings";
  return "Overview";
}
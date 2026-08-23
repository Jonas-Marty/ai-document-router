import { FileStack, History, Settings } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { cn } from "@/lib/utils";
import { OutageBanner } from "./OutageBanner";
import { ThemeToggle } from "./ThemeToggle";

const NAV_ITEMS = [
  { to: "/", label: "Review", icon: FileStack, end: true },
  { to: "/history", label: "History", icon: History, end: false },
  { to: "/settings", label: "Settings", icon: Settings, end: false },
] as const;

/** Top bar, nav, and the outage banner -- wraps every route. Mobile-first: nav labels
 * collapse to icon-only below the sm breakpoint rather than wrapping or overflowing. */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col bg-background text-foreground">
      <header className="sticky top-0 z-10 border-b border-border bg-background">
        <div className="flex h-14 items-center gap-2 px-3 sm:px-4">
          <span className="min-w-0 shrink truncate font-semibold">AI Document Router</span>
          <nav className="ml-auto flex items-center gap-1">
            {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    isActive
                      ? "bg-accent text-accent-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                  )
                }
              >
                <Icon className="size-4" aria-hidden="true" />
                <span className="hidden sm:inline">{label}</span>
              </NavLink>
            ))}
          </nav>
          <ThemeToggle />
        </div>
        <OutageBanner />
      </header>
      <main className="flex-1">{children}</main>
    </div>
  );
}

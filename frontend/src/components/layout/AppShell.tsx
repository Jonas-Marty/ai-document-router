import { FileStack, History, LogOut, Settings } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useCurrentUser, useLogout } from "@/hooks/useAuth";
import { useConfirmNavigation } from "@/hooks/useNavigationGuard";
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
  const confirmNavigation = useConfirmNavigation();
  const { data: user } = useCurrentUser();
  const logout = useLogout();

  return (
    <div className="flex h-dvh flex-col bg-background text-foreground">
      <header className="border-b border-border bg-background">
        <div className="flex h-14 items-center gap-2 px-3 sm:px-4">
          <span className="min-w-0 shrink truncate font-semibold">AI Document Router</span>
          <nav className="ml-auto flex items-center gap-1">
            {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                onClick={(e) => {
                  // Already on this route (react-router sets aria-current) -- nothing to
                  // leave, so don't prompt.
                  if (e.currentTarget.getAttribute("aria-current") === "page") return;
                  if (!confirmNavigation()) e.preventDefault();
                }}
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
          {user && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Sign out ${user.email}`}
                  disabled={logout.isPending}
                  onClick={() => {
                    if (confirmNavigation()) logout.mutate();
                  }}
                >
                  <LogOut className="size-4" aria-hidden="true" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Sign out {user.email}</TooltipContent>
            </Tooltip>
          )}
        </div>
        <OutageBanner />
      </header>
      <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}

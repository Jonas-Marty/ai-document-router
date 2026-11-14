import { Link, useLocation } from "react-router-dom";
import { cn } from "@/lib/utils";

const TABS = [
  {
    to: "/lab",
    label: "Runs",
    matches: (path: string) => path === "/lab" || path.startsWith("/lab/runs"),
  },
  {
    to: "/lab/configurations",
    label: "Configurations",
    matches: (path: string) => path.startsWith("/lab/configurations"),
  },
  {
    to: "/lab/test-sets",
    label: "Test sets",
    matches: (path: string) => path.startsWith("/lab/test-sets"),
  },
];

/** SPEC 8.13a: the Lab's three tabs. Links rather than tab buttons: each is its own page with
 * its own URL, so back and bookmarks work. */
export function LabTabs() {
  const { pathname } = useLocation();
  return (
    <nav aria-label="Lab" className="flex gap-1 overflow-x-auto border-b border-border">
      {TABS.map((tab) => {
        const current = tab.matches(pathname);
        return (
          <Link
            key={tab.to}
            to={tab.to}
            aria-current={current ? "page" : undefined}
            className={cn(
              "-mb-px shrink-0 border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap",
              current
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}

/** The heading and tabs every Lab page starts with, and an optional action on the right. */
export function LabHeader({ action }: { action?: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">Lab</h1>
        {action}
      </div>
      <LabTabs />
    </div>
  );
}

import { createContext, type ReactNode, useContext, useEffect, useRef } from "react";

type GuardRef = React.MutableRefObject<string | null>;

const NavigationGuardContext = createContext<GuardRef | null>(null);

/** Wraps the app once, above both AppShell (whose nav links need to check the guard) and the
 * routed pages (Settings is the one that sets it) -- see App.tsx. Not a data-router
 * `useBlocker`: SPEC 8.7 doesn't ask for back/forward-button coverage specifically, and that
 * would mean migrating off the plain `<Routes>`/`<BrowserRouter>` setup every existing test
 * harness (App.test.tsx et al.) already depends on, mid-milestone, for a case the spec never
 * names. This covers in-app nav-link clicks plus, via `beforeunload`, tab close/refresh/URL
 * bar navigation. */
export function NavigationGuardProvider({ children }: { children: ReactNode }) {
  const ref = useRef<string | null>(null);
  return <NavigationGuardContext.Provider value={ref}>{children}</NavigationGuardContext.Provider>;
}

function useGuardRef(): GuardRef {
  const ref = useContext(NavigationGuardContext);
  if (!ref) {
    throw new Error("useNavigationGuard hooks must be used within NavigationGuardProvider");
  }
  return ref;
}

/** SPEC 8.7: "unsaved-changes navigation guard." `message` is the confirmation prompt to
 * show, or `null` when there's nothing unsaved -- pass `null`, not `""`, to disarm the guard.
 * Also arms a `beforeunload` listener while `message` is set; browsers ignore any custom text
 * there and always show their own fixed wording, so the string itself only matters for the
 * in-app case `useConfirmNavigation` handles. */
export function useSetNavigationGuard(message: string | null): void {
  const ref = useGuardRef();

  useEffect(() => {
    ref.current = message;
    return () => {
      ref.current = null;
    };
  }, [ref, message]);

  useEffect(() => {
    if (!message) return;
    function handleBeforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [message]);
}

/** Used by AppShell's nav links: returns whether navigation should proceed, prompting the
 * user only if a guard is currently armed. Checking `ref.current` before ever calling
 * `window.confirm` matters beyond the obvious -- jsdom has no real `confirm()`, so a page with
 * no armed guard must short-circuit before reaching that call, or every test that clicks a
 * nav link (App.test.tsx's routing suite included) breaks on an unguarded page. */
export function useConfirmNavigation(): () => boolean {
  const ref = useGuardRef();
  return () => {
    if (!ref.current) return true;
    return window.confirm(ref.current);
  };
}

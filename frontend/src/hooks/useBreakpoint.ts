import { useSyncExternalStore } from "react";

// SPEC 8.2: >= 1024px is "Desktop", >= 1280px "Wide". SPEC 8.4 requires mobile to not render
// shortcut/split machinery at all, not just hide it with CSS -- so callers need a real
// boolean, not a Tailwind breakpoint class, to decide what to mount.
const DESKTOP_QUERY = "(min-width: 1024px)";
const WIDE_QUERY = "(min-width: 1280px)";

function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (callback) => {
      const mql = window.matchMedia(query);
      mql.addEventListener("change", callback);
      return () => mql.removeEventListener("change", callback);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

export function useIsDesktop(): boolean {
  return useMediaQuery(DESKTOP_QUERY);
}

/** Wide enough for the review list as a pane of its own beside preview and form. */
export function useIsWide(): boolean {
  return useMediaQuery(WIDE_QUERY);
}

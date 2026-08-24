import { useSyncExternalStore } from "react";

// SPEC 8.2: >= 1024px is "Desktop". SPEC 8.4 requires mobile to not render shortcut/split
// machinery at all, not just hide it with CSS -- so callers need a real boolean, not a
// Tailwind breakpoint class, to decide what to mount.
const DESKTOP_QUERY = "(min-width: 1024px)";

function subscribe(callback: () => void): () => void {
  const mql = window.matchMedia(DESKTOP_QUERY);
  mql.addEventListener("change", callback);
  return () => mql.removeEventListener("change", callback);
}

function getSnapshot(): boolean {
  return window.matchMedia(DESKTOP_QUERY).matches;
}

export function useIsDesktop(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}

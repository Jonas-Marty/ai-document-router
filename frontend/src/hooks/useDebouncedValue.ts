import { useEffect, useState } from "react";

/** SPEC 8.3: the sibling list and collision check "re-fetch with a 300ms debounce whenever
 * the target folder changes". Generic so the same debounce serves the folder path and the
 * filename (stem + extension) that make up the folder-context query's key. */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}

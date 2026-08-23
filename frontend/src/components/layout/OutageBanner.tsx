import { AlertTriangle } from "lucide-react";
import { useHealth } from "@/hooks/useHealth";
import { ApiError, NETWORK_ERROR_CODE } from "@/services/api/errors";

/** SPEC 8.10: "A WebDAV outage surfaces as a persistent banner in the top bar driven by
 * /health, not just per-request errors." Also covers the backend being fully unreachable
 * (M6's acceptance test: stopping the backend produces this banner, not a broken page) --
 * that's a distinct condition from WebDAV being down while the backend is up, so it gets
 * its own message. */
export function OutageBanner() {
  const { data, error, isError } = useHealth();

  const message = isError
    ? error instanceof ApiError && error.code === NETWORK_ERROR_CODE
      ? "Can't reach the server. Retrying…"
      : "The server returned an unexpected error. Retrying…"
    : data && !data.webdav_reachable
      ? "WebDAV is unreachable. Filing is unavailable until it's back."
      : null;

  if (!message) return null;

  return (
    <div
      role="alert"
      className="flex items-center justify-center gap-2 border-b border-amber-600/30 bg-amber-100 px-4 py-2 text-sm text-amber-900 dark:border-amber-400/30 dark:bg-amber-950 dark:text-amber-200"
    >
      <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
      <span>{message}</span>
    </div>
  );
}

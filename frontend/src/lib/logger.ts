/** Console logging with one prefix per area, so the browser console can be filtered to it.
 *
 * Debug lines only exist in development: a production console full of them hides the one
 * warning someone is trying to read to you. Warnings and errors always print, because they
 * are what gets pasted into a bug report -- together with the backend's request id, which
 * is the key to the matching line in the server log. */
type Level = "debug" | "info" | "warn" | "error";

const LEVEL_ORDER: Record<Level, number> = { debug: 0, info: 1, warn: 2, error: 3 };
const MIN_LEVEL: Level = import.meta.env.DEV ? "debug" : "warn";

export interface Logger {
  debug(message: string, ...details: unknown[]): void;
  info(message: string, ...details: unknown[]): void;
  warn(message: string, ...details: unknown[]): void;
  error(message: string, ...details: unknown[]): void;
}

export function createLogger(area: string): Logger {
  const emit =
    (level: Level) =>
    (message: string, ...details: unknown[]) => {
      if (LEVEL_ORDER[level] < LEVEL_ORDER[MIN_LEVEL]) return;
      console[level](`[${area}] ${message}`, ...details);
    };
  return { debug: emit("debug"), info: emit("info"), warn: emit("warn"), error: emit("error") };
}

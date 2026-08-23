import { createContext, type ReactNode, useContext, useEffect, useState } from "react";

type Theme = "light" | "dark" | "system";
type ResolvedTheme = "light" | "dark";

interface ThemeContextValue {
  /** What the user picked -- "system" until they override it. */
  theme: Theme;
  /** What's actually applied right now, with "system" already resolved. */
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: Theme) => void;
}

const STORAGE_KEY = "theme";
const ThemeContext = createContext<ThemeContextValue | null>(null);

function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function readStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

function resolve(theme: Theme): ResolvedTheme {
  return theme === "system" ? (systemPrefersDark() ? "dark" : "light") : theme;
}

function applyResolvedTheme(resolved: ResolvedTheme): void {
  document.documentElement.classList.toggle("dark", resolved === "dark");
}

/** System default, manual override, persisted (SPEC 8.10 / M6).
 *
 * A blocking inline script in index.html applies the stored theme before React mounts, so
 * this provider's job is to sync its own state with what's already on the page and react to
 * changes -- not to avoid an initial flash, which the script already handles.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(readStoredTheme);
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() => resolve(theme));

  useEffect(() => {
    const resolved = resolve(theme);
    setResolvedTheme(resolved);
    applyResolvedTheme(resolved);

    if (theme !== "system") return;

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = () => {
      const nowResolved = media.matches ? "dark" : "light";
      setResolvedTheme(nowResolved);
      applyResolvedTheme(nowResolved);
    };
    media.addEventListener("change", handleChange);
    return () => media.removeEventListener("change", handleChange);
  }, [theme]);

  function setTheme(next: Theme) {
    setThemeState(next);
    try {
      if (next === "system") {
        localStorage.removeItem(STORAGE_KEY);
      } else {
        localStorage.setItem(STORAGE_KEY, next);
      }
    } catch {
      // Storage unavailable (private browsing, etc). The theme still applies for this
      // session; it just won't persist across a reload.
    }
  }

  return (
    <ThemeContext.Provider value={{ theme, resolvedTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return context;
}

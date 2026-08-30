import { type ReactNode, useRef } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { useCurrentUser } from "@/hooks/useAuth";
import LoginPage from "@/pages/LoginPage";

type Decision = "app" | "sign-in";

/** The gate every screen sits behind.
 *
 * Renders the sign-in screen in place rather than redirecting to /login, so the URL someone
 * asked for survives signing in -- a bookmarked /settings comes back as /settings. The
 * backend is the actual boundary (every route but /health and /auth/* demands a session);
 * this only decides what to draw.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { data: user, isPending, error } = useCurrentUser();

  // An error that is not "nobody is signed in" means the API is unreachable, and that is the
  // outage banner's job -- a sign-in form would fail exactly the same way. useCurrentUser
  // turns a 401 into `null`, so anything left here is an outage.
  const current: Decision | null = isPending ? null : error || user ? "app" : "sign-in";

  // Latched, so a background revalidation (which briefly reports "pending" again) does not
  // drop the whole app back to a skeleton and remount every screen under it.
  const latched = useRef<Decision | null>(null);
  if (current !== null) latched.current = current;
  const decision = current ?? latched.current;

  if (decision === null) {
    return (
      <div className="mx-auto w-full max-w-md p-4">
        <Skeleton className="h-64 w-full" aria-busy="true" />
      </div>
    );
  }

  return decision === "app" ? <>{children}</> : <LoginPage />;
}

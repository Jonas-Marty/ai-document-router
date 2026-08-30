import { AppShell } from "@/components/layout/AppShell";
import { RequireAuth } from "@/components/layout/RequireAuth";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { NavigationGuardProvider } from "@/hooks/useNavigationGuard";
import { AppRoutes } from "./routes";

function App() {
  return (
    <NavigationGuardProvider>
      <TooltipProvider>
        {/* Outside RequireAuth: the sign-in screen raises toasts too. */}
        <Toaster />
        <RequireAuth>
          <AppShell>
            <AppRoutes />
          </AppShell>
        </RequireAuth>
      </TooltipProvider>
    </NavigationGuardProvider>
  );
}

export default App;

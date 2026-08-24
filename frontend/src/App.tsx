import { AppShell } from "@/components/layout/AppShell";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { NavigationGuardProvider } from "@/hooks/useNavigationGuard";
import { AppRoutes } from "./routes";

function App() {
  return (
    <NavigationGuardProvider>
      <TooltipProvider>
        <AppShell>
          <AppRoutes />
          <Toaster />
        </AppShell>
      </TooltipProvider>
    </NavigationGuardProvider>
  );
}

export default App;

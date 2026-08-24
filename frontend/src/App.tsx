import { AppShell } from "@/components/layout/AppShell";
import { Toaster } from "@/components/ui/sonner";
import { AppRoutes } from "./routes";

function App() {
  return (
    <AppShell>
      <AppRoutes />
      <Toaster />
    </AppShell>
  );
}

export default App;

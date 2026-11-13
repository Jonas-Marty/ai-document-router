import { Navigate, Route, Routes } from "react-router-dom";
import ActivityPage from "@/pages/ActivityPage";
import ConfigurationEditorPage from "@/pages/ConfigurationEditorPage";
import HistoryPage from "@/pages/HistoryPage";
import KnowledgePage from "@/pages/KnowledgePage";
import LabPage from "@/pages/LabPage";
import ReviewPage from "@/pages/ReviewPage";
import SettingsPage from "@/pages/SettingsPage";
import UsagePage from "@/pages/UsagePage";

// SPEC 8.1: "/" Review, "/history" History, "/lab" Lab, "/knowledge" Archive knowledge,
// "/settings" Settings, "/activity" Activity,
// "/activity/usage" AI usage.
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<ReviewPage />} />
      <Route path="/history" element={<HistoryPage />} />
      <Route path="/lab" element={<LabPage />} />
      <Route path="/lab/configurations/new" element={<ConfigurationEditorPage />} />
      <Route path="/lab/configurations/:id" element={<ConfigurationEditorPage />} />
      <Route path="/knowledge" element={<KnowledgePage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="/activity" element={<ActivityPage />} />
      <Route path="/activity/usage" element={<UsagePage />} />
      {/* Only reachable when already signed in -- RequireAuth renders the sign-in screen in
          place otherwise. The OIDC callback redirects here on failure. */}
      <Route path="/login" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

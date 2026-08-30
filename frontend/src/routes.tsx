import { Navigate, Route, Routes } from "react-router-dom";
import HistoryPage from "@/pages/HistoryPage";
import ReviewPage from "@/pages/ReviewPage";
import SettingsPage from "@/pages/SettingsPage";

// SPEC 8.1: "/" Review, "/history" History, "/settings" Settings.
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<ReviewPage />} />
      <Route path="/history" element={<HistoryPage />} />
      <Route path="/settings" element={<SettingsPage />} />
      {/* Only reachable when already signed in -- RequireAuth renders the sign-in screen in
          place otherwise. The OIDC callback redirects here on failure. */}
      <Route path="/login" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

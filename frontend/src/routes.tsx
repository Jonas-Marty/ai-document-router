import { Route, Routes } from "react-router-dom";
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
    </Routes>
  );
}

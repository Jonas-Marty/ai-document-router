import { useQuery } from "@tanstack/react-query";
import { fetchHealth } from "@/services/api/health";

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
  });
}

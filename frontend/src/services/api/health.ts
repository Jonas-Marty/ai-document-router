export interface HealthResponse {
  status: string;
  /** False while the WebDAV server is unreachable. Drives the outage banner. */
  webdav_reachable: boolean;
  /** Documents awaiting review: pending plus previously skipped. */
  queue_depth: number;
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch("/api/v1/health");
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`);
  }
  return response.json() as Promise<HealthResponse>;
}

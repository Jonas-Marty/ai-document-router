import { ApiError, NETWORK_ERROR_CODE } from "./errors";
import type {
  ApiErrorBody,
  ApproveRequest,
  CreateFolderRequest,
  Document,
  FolderContext,
  FolderNode,
  HealthResponse,
  HistoryPage,
  QueueResponse,
  RevertResponse,
  RoutedResponse,
  Settings,
  SettingsUpdate,
} from "./types";

/** Everything the frontend can ask of the backend. Implemented once, by HttpApiClient --
 * the interface exists so components depend on a contract, not on fetch. */
export interface ApiClient {
  getHealth(): Promise<HealthResponse>;

  getQueue(limit?: number): Promise<QueueResponse>;
  getDocument(id: string): Promise<Document>;
  /** Not a fetch: the content endpoint streams bytes, so this just builds the URL for a
   * viewer (react-pdf, <img>) to load directly. */
  getDocumentContentUrl(id: string): string;
  approveDocument(id: string, body: ApproveRequest): Promise<RoutedResponse>;
  skipDocument(id: string): Promise<Document>;
  trashDocument(id: string): Promise<RoutedResponse>;
  regenerateDocument(id: string): Promise<Document>;

  getFolderTree(path?: string): Promise<FolderNode[]>;
  createFolder(body: CreateFolderRequest): Promise<FolderNode>;
  getFolderContext(path: string, filename?: string): Promise<FolderContext>;

  getHistory(limit?: number, cursor?: string): Promise<HistoryPage>;
  revertHistoryEntry(id: string): Promise<RevertResponse>;

  getSettings(): Promise<Settings>;
  updateSettings(body: SettingsUpdate): Promise<Settings>;
}

const API_BASE = "/api/v1";

export class HttpApiClient implements ApiClient {
  getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>("GET", "/health");
  }

  getQueue(limit?: number): Promise<QueueResponse> {
    return this.request<QueueResponse>("GET", `/queue${query({ limit })}`);
  }

  getDocument(id: string): Promise<Document> {
    return this.request<Document>("GET", `/documents/${encodeURIComponent(id)}`);
  }

  getDocumentContentUrl(id: string): string {
    return `${API_BASE}/documents/${encodeURIComponent(id)}/content`;
  }

  approveDocument(id: string, body: ApproveRequest): Promise<RoutedResponse> {
    return this.request<RoutedResponse>(
      "POST",
      `/documents/${encodeURIComponent(id)}/approve`,
      body,
    );
  }

  async skipDocument(id: string): Promise<Document> {
    const response = await this.request<{ document: Document }>(
      "POST",
      `/documents/${encodeURIComponent(id)}/skip`,
    );
    return response.document;
  }

  trashDocument(id: string): Promise<RoutedResponse> {
    return this.request<RoutedResponse>("POST", `/documents/${encodeURIComponent(id)}/trash`);
  }

  async regenerateDocument(id: string): Promise<Document> {
    const response = await this.request<{ document: Document }>(
      "POST",
      `/documents/${encodeURIComponent(id)}/regenerate`,
    );
    return response.document;
  }

  getFolderTree(path?: string): Promise<FolderNode[]> {
    return this.request<FolderNode[]>("GET", `/folders/tree${query({ path })}`);
  }

  createFolder(body: CreateFolderRequest): Promise<FolderNode> {
    return this.request<FolderNode>("POST", "/folders", body);
  }

  getFolderContext(path: string, filename?: string): Promise<FolderContext> {
    return this.request<FolderContext>("GET", `/folders/context${query({ path, filename })}`);
  }

  getHistory(limit?: number, cursor?: string): Promise<HistoryPage> {
    return this.request<HistoryPage>("GET", `/history${query({ limit, cursor })}`);
  }

  revertHistoryEntry(id: string): Promise<RevertResponse> {
    return this.request<RevertResponse>("POST", `/history/${encodeURIComponent(id)}/revert`);
  }

  getSettings(): Promise<Settings> {
    return this.request<Settings>("GET", "/settings");
  }

  updateSettings(body: SettingsUpdate): Promise<Settings> {
    return this.request<Settings>("PUT", "/settings", body);
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${API_BASE}${path}`, {
        method,
        headers: {
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
          // AUTH: inject the Authentik bearer token here once auth exists. Nothing else
          // in this file should need to change (SPEC 6.5).
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch {
      throw new ApiError(
        NETWORK_ERROR_CODE,
        "Can't reach the server. Check your connection and try again.",
        null,
      );
    }

    if (!response.ok) {
      throw new ApiError(...(await parseErrorBody(response)));
    }

    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }
}

// Gateway status codes a reverse proxy (Vite's dev proxy, nginx in prod) returns itself,
// with no body, when the backend container/process is unreachable. These never came from
// our error envelope, so they mean "can't reach the server", not "the API returned an
// error" -- surfacing them as network_error is what makes the outage banner fire.
const GATEWAY_ERROR_STATUSES = new Set([502, 503, 504]);

async function parseErrorBody(
  response: Response,
): Promise<[code: string, message: string, status: number | null]> {
  try {
    const body = (await response.json()) as ApiErrorBody;
    return [body.error.code, body.error.message, response.status];
  } catch {
    if (GATEWAY_ERROR_STATUSES.has(response.status)) {
      return [
        NETWORK_ERROR_CODE,
        "Can't reach the server. Check your connection and try again.",
        null,
      ];
    }
    return [
      "unknown_error",
      `The server returned an unexpected error (${response.status}).`,
      response.status,
    ];
  }
}

function query(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(
    (entry): entry is [string, string | number] => entry[1] !== undefined,
  );
  if (entries.length === 0) return "";
  const search = new URLSearchParams(entries.map(([key, value]) => [key, String(value)]));
  return `?${search.toString()}`;
}

export const apiClient: ApiClient = new HttpApiClient();

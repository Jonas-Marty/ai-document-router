import { createLogger } from "@/lib/logger";
import { ApiError, NETWORK_ERROR_CODE } from "./errors";
import type {
  AddCasesRequest,
  AddedCases,
  AiEndpoint,
  AiEndpointWrite,
  AiModelsRequest,
  AiModelsResponse,
  AiUsageReport,
  AnalyseRequest,
  AnalysisEstimate,
  ApiErrorBody,
  ApproveRequest,
  ArchiveKnowledge,
  AuditCategory,
  AuditPage,
  AuditUsage,
  AuthConfig,
  AuthUser,
  BuiltinPrompts,
  BulkTrashResponse,
  Configuration,
  ConfigurationPreviewCall,
  ConfigurationWrite,
  CreateFolderRequest,
  Credentials,
  Document,
  EndpointStatus,
  FolderContext,
  FolderNode,
  HealthResponse,
  InboxPage,
  InboxQuery,
  KnownSender,
  KnownSenderWrite,
  LabField,
  LabResult,
  LabRun,
  LabRunDetail,
  NextcloudConnection,
  NextcloudWrite,
  RegenerateChoice,
  RetriedResponse,
  RevertResponse,
  RoutedResponse,
  Settings,
  SettingsUpdate,
  StartRunRequest,
  TestSet,
  TestSetDetail,
} from "./types";

/** Everything the frontend can ask of the backend. Implemented once, by HttpApiClient --
 * the interface exists so components depend on a contract, not on fetch. */
export interface ApiClient {
  getHealth(): Promise<HealthResponse>;

  getDocument(id: string): Promise<Document>;
  /** Not a fetch: the content endpoint streams bytes, so this just builds the URL for a
   * viewer (react-pdf, <img>) to load directly. */
  getDocumentContentUrl(id: string): string;
  approveDocument(id: string, body: ApproveRequest): Promise<RoutedResponse>;
  skipDocument(id: string): Promise<Document>;
  trashDocument(id: string): Promise<RoutedResponse>;
  regenerateDocument(id: string, choice?: RegenerateChoice): Promise<Document>;
  retryFailedProposals(): Promise<RetriedResponse>;

  getInbox(params: InboxQuery): Promise<InboxPage>;
  /** Not a fetch: a URL for an <img>, like getDocumentContentUrl. */
  getThumbnailUrl(id: string): string;
  analyseDocuments(body: AnalyseRequest): Promise<{ queued: number }>;
  trashDocuments(ids: string[]): Promise<BulkTrashResponse>;
  estimateAnalysis(ids: string[], configurationId: string): Promise<AnalysisEstimate>;

  getFolderTree(path?: string): Promise<FolderNode[]>;
  browseFolders(path?: string): Promise<FolderNode[]>;
  createFolder(body: CreateFolderRequest): Promise<FolderNode>;
  getFolderContext(path: string, filename?: string): Promise<FolderContext>;
  getFolderFiles(path: string): Promise<FolderContext>;

  getAudit(limit?: number, cursor?: string, category?: AuditCategory): Promise<AuditPage>;
  getAuditUsage(days?: number): Promise<AuditUsage>;
  revertHistoryEntry(id: string): Promise<RevertResponse>;

  getSettings(): Promise<Settings>;
  updateSettings(body: SettingsUpdate): Promise<Settings>;
  getNextcloud(): Promise<NextcloudConnection>;
  updateNextcloud(body: NextcloudWrite): Promise<NextcloudConnection>;
  testNextcloud(body: NextcloudWrite): Promise<void>;
  listAiModels(body: AiModelsRequest): Promise<AiModelsResponse>;
  getAiUsage(start: string, end: string): Promise<AiUsageReport>;
  listAiEndpoints(): Promise<AiEndpoint[]>;
  getEndpointStatuses(): Promise<EndpointStatus[]>;
  createAiEndpoint(body: AiEndpointWrite): Promise<AiEndpoint>;
  updateAiEndpoint(id: string, body: AiEndpointWrite): Promise<AiEndpoint>;
  deleteAiEndpoint(id: string): Promise<void>;
  listConfigurations(): Promise<Configuration[]>;
  getBuiltinPrompts(): Promise<BuiltinPrompts>;
  createConfiguration(body: ConfigurationWrite): Promise<Configuration>;
  updateConfiguration(id: string, body: ConfigurationWrite): Promise<Configuration>;
  deleteConfiguration(id: string): Promise<void>;
  duplicateConfiguration(id: string): Promise<Configuration>;
  makeDefaultConfiguration(id: string): Promise<Configuration>;
  previewConfiguration(id: string): Promise<ConfigurationPreviewCall[]>;
  listTestSets(): Promise<TestSet[]>;
  getTestSet(id: string): Promise<TestSetDetail>;
  createTestSet(name: string): Promise<TestSet>;
  renameTestSet(id: string, name: string): Promise<TestSet>;
  deleteTestSet(id: string): Promise<void>;
  addTestCases(id: string, body: AddCasesRequest): Promise<AddedCases>;
  deleteTestCase(id: string): Promise<void>;
  listLabRuns(): Promise<LabRun[]>;
  getLabRun(id: string): Promise<LabRunDetail>;
  startLabRun(body: StartRunRequest): Promise<LabRun>;
  cancelLabRun(id: string): Promise<LabRun>;
  deleteLabRun(id: string): Promise<void>;
  setLabVerdict(id: string, field: LabField, score: number | null): Promise<LabResult>;
  getKnowledge(): Promise<ArchiveKnowledge>;
  updateKnowledge(body: ArchiveKnowledge): Promise<ArchiveKnowledge>;
  listKnownSenders(): Promise<KnownSender[]>;
  updateKnownSender(id: string, body: KnownSenderWrite): Promise<KnownSender>;
  deleteKnownSender(id: string): Promise<void>;
  deleteKnownAccount(id: string): Promise<void>;
  getAuthConfig(): Promise<AuthConfig>;
  getCurrentUser(): Promise<AuthUser>;
  login(body: Credentials): Promise<AuthUser>;
  register(body: Credentials): Promise<AuthUser>;
  logout(): Promise<void>;
}

const API_BASE = "/api/v1";

const logger = createLogger("api");

export class HttpApiClient implements ApiClient {
  getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>("GET", "/health");
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

  async regenerateDocument(id: string, choice?: RegenerateChoice): Promise<Document> {
    const response = await this.request<{ document: Document }>(
      "POST",
      `/documents/${encodeURIComponent(id)}/regenerate`,
      choice,
    );
    return response.document;
  }

  retryFailedProposals(): Promise<RetriedResponse> {
    return this.request<RetriedResponse>("POST", "/documents/retry-failed");
  }

  getFolderTree(path?: string): Promise<FolderNode[]> {
    return this.request<FolderNode[]>("GET", `/folders/tree${query({ path })}`);
  }

  browseFolders(path?: string): Promise<FolderNode[]> {
    return this.request<FolderNode[]>("GET", `/folders/browse${query({ path })}`);
  }

  createFolder(body: CreateFolderRequest): Promise<FolderNode> {
    return this.request<FolderNode>("POST", "/folders", body);
  }

  getFolderContext(path: string, filename?: string): Promise<FolderContext> {
    return this.request<FolderContext>("GET", `/folders/context${query({ path, filename })}`);
  }

  getFolderFiles(path: string): Promise<FolderContext> {
    return this.request<FolderContext>("GET", `/folders/context${query({ path, all: "true" })}`);
  }

  getInbox(params: InboxQuery): Promise<InboxPage> {
    return this.request<InboxPage>("GET", `/inbox${query({ ...params })}`);
  }

  getThumbnailUrl(id: string): string {
    return `${API_BASE}/documents/${encodeURIComponent(id)}/thumbnail`;
  }

  analyseDocuments(body: AnalyseRequest): Promise<{ queued: number }> {
    return this.request<{ queued: number }>("POST", "/documents/analyse", body);
  }

  trashDocuments(ids: string[]): Promise<BulkTrashResponse> {
    return this.request<BulkTrashResponse>("POST", "/documents/trash", { document_ids: ids });
  }

  estimateAnalysis(ids: string[], configurationId: string): Promise<AnalysisEstimate> {
    return this.request<AnalysisEstimate>("POST", "/documents/estimate", {
      document_ids: ids,
      configuration_id: configurationId,
    });
  }

  getAudit(limit?: number, cursor?: string, category?: AuditCategory): Promise<AuditPage> {
    return this.request<AuditPage>("GET", `/audit${query({ limit, cursor, category })}`);
  }

  getAuditUsage(days?: number): Promise<AuditUsage> {
    return this.request<AuditUsage>("GET", `/audit/usage${query({ days })}`);
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

  getNextcloud(): Promise<NextcloudConnection> {
    return this.request<NextcloudConnection>("GET", "/settings/nextcloud");
  }

  updateNextcloud(body: NextcloudWrite): Promise<NextcloudConnection> {
    return this.request<NextcloudConnection>("PUT", "/settings/nextcloud", body);
  }

  testNextcloud(body: NextcloudWrite): Promise<void> {
    return this.request<void>("POST", "/settings/nextcloud/test", body);
  }

  getAiUsage(start: string, end: string): Promise<AiUsageReport> {
    return this.request<AiUsageReport>("GET", `/ai/usage${query({ start, end })}`);
  }

  listAiModels(body: AiModelsRequest): Promise<AiModelsResponse> {
    return this.request<AiModelsResponse>("POST", "/ai/models", body);
  }

  listAiEndpoints(): Promise<AiEndpoint[]> {
    return this.request<AiEndpoint[]>("GET", "/ai/endpoints");
  }

  getEndpointStatuses(): Promise<EndpointStatus[]> {
    return this.request<EndpointStatus[]>("GET", "/ai/endpoints/status");
  }

  createAiEndpoint(body: AiEndpointWrite): Promise<AiEndpoint> {
    return this.request<AiEndpoint>("POST", "/ai/endpoints", body);
  }

  updateAiEndpoint(id: string, body: AiEndpointWrite): Promise<AiEndpoint> {
    return this.request<AiEndpoint>("PUT", `/ai/endpoints/${id}`, body);
  }

  deleteAiEndpoint(id: string): Promise<void> {
    return this.request<void>("DELETE", `/ai/endpoints/${id}`);
  }

  listConfigurations(): Promise<Configuration[]> {
    return this.request<Configuration[]>("GET", "/configurations");
  }

  getBuiltinPrompts(): Promise<BuiltinPrompts> {
    return this.request<BuiltinPrompts>("GET", "/configurations/prompts");
  }

  createConfiguration(body: ConfigurationWrite): Promise<Configuration> {
    return this.request<Configuration>("POST", "/configurations", body);
  }

  updateConfiguration(id: string, body: ConfigurationWrite): Promise<Configuration> {
    return this.request<Configuration>("PUT", `/configurations/${encodeURIComponent(id)}`, body);
  }

  deleteConfiguration(id: string): Promise<void> {
    return this.request<void>("DELETE", `/configurations/${encodeURIComponent(id)}`);
  }

  duplicateConfiguration(id: string): Promise<Configuration> {
    return this.request<Configuration>(
      "POST",
      `/configurations/${encodeURIComponent(id)}/duplicate`,
    );
  }

  makeDefaultConfiguration(id: string): Promise<Configuration> {
    return this.request<Configuration>("POST", `/configurations/${encodeURIComponent(id)}/default`);
  }

  previewConfiguration(id: string): Promise<ConfigurationPreviewCall[]> {
    return this.request<ConfigurationPreviewCall[]>(
      "GET",
      `/configurations/${encodeURIComponent(id)}/preview`,
    );
  }

  listTestSets(): Promise<TestSet[]> {
    return this.request<TestSet[]>("GET", "/lab/test-sets");
  }

  getTestSet(id: string): Promise<TestSetDetail> {
    return this.request<TestSetDetail>("GET", `/lab/test-sets/${encodeURIComponent(id)}`);
  }

  createTestSet(name: string): Promise<TestSet> {
    return this.request<TestSet>("POST", "/lab/test-sets", { name });
  }

  renameTestSet(id: string, name: string): Promise<TestSet> {
    return this.request<TestSet>("PUT", `/lab/test-sets/${encodeURIComponent(id)}`, { name });
  }

  deleteTestSet(id: string): Promise<void> {
    return this.request<void>("DELETE", `/lab/test-sets/${encodeURIComponent(id)}`);
  }

  addTestCases(id: string, body: AddCasesRequest): Promise<AddedCases> {
    return this.request<AddedCases>("POST", `/lab/test-sets/${encodeURIComponent(id)}/cases`, body);
  }

  deleteTestCase(id: string): Promise<void> {
    return this.request<void>("DELETE", `/lab/test-cases/${encodeURIComponent(id)}`);
  }

  listLabRuns(): Promise<LabRun[]> {
    return this.request<LabRun[]>("GET", "/lab/runs");
  }

  getLabRun(id: string): Promise<LabRunDetail> {
    return this.request<LabRunDetail>("GET", `/lab/runs/${encodeURIComponent(id)}`);
  }

  startLabRun(body: StartRunRequest): Promise<LabRun> {
    return this.request<LabRun>("POST", "/lab/runs", body);
  }

  cancelLabRun(id: string): Promise<LabRun> {
    return this.request<LabRun>("POST", `/lab/runs/${encodeURIComponent(id)}/cancel`);
  }

  deleteLabRun(id: string): Promise<void> {
    return this.request<void>("DELETE", `/lab/runs/${encodeURIComponent(id)}`);
  }

  setLabVerdict(id: string, field: LabField, score: number | null): Promise<LabResult> {
    return this.request<LabResult>("PUT", `/lab/results/${encodeURIComponent(id)}/verdict`, {
      field,
      score,
    });
  }

  getKnowledge(): Promise<ArchiveKnowledge> {
    return this.request<ArchiveKnowledge>("GET", "/knowledge");
  }

  updateKnowledge(body: ArchiveKnowledge): Promise<ArchiveKnowledge> {
    return this.request<ArchiveKnowledge>("PUT", "/knowledge", body);
  }

  listKnownSenders(): Promise<KnownSender[]> {
    return this.request<KnownSender[]>("GET", "/knowledge/senders");
  }

  updateKnownSender(id: string, body: KnownSenderWrite): Promise<KnownSender> {
    return this.request<KnownSender>("PUT", `/knowledge/senders/${encodeURIComponent(id)}`, body);
  }

  deleteKnownSender(id: string): Promise<void> {
    return this.request<void>("DELETE", `/knowledge/senders/${encodeURIComponent(id)}`);
  }

  deleteKnownAccount(id: string): Promise<void> {
    return this.request<void>("DELETE", `/knowledge/accounts/${encodeURIComponent(id)}`);
  }

  getAuthConfig(): Promise<AuthConfig> {
    return this.request<AuthConfig>("GET", "/auth/config");
  }

  getCurrentUser(): Promise<AuthUser> {
    return this.request<AuthUser>("GET", "/auth/me");
  }

  login(body: Credentials): Promise<AuthUser> {
    return this.request<AuthUser>("POST", "/auth/login", body);
  }

  register(body: Credentials): Promise<AuthUser> {
    return this.request<AuthUser>("POST", "/auth/register", body);
  }

  logout(): Promise<void> {
    return this.request<void>("POST", "/auth/logout");
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${API_BASE}${path}`, {
        method,
        headers: {
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        },
        // The session is an HttpOnly cookie, so there is no token for this layer to hold
        // or for an XSS to read. Same-origin is the default, but stating it keeps the
        // credentialed request explicit.
        credentials: "same-origin",
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (cause) {
      logger.warn(`${method} ${path} never reached the server`, cause);
      throw new ApiError(
        NETWORK_ERROR_CODE,
        "Can't reach the server. Check your connection and try again.",
        null,
      );
    }

    if (!response.ok) {
      const requestId = response.headers.get("X-Request-ID");
      const [code, message, status] = await parseErrorBody(response);
      // 401 is how every page load learns nobody is signed in yet, not something going wrong.
      logger[response.status === 401 ? "debug" : "warn"](
        `${method} ${path} ${response.status} ${code}${requestId ? ` (req ${requestId})` : ""}`,
        message,
      );
      throw new ApiError(code, message, status, requestId);
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

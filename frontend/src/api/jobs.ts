export interface EngineOption {
  value: string;
  label: string;
  description: string;
}

export interface EnginesResponse {
  default_engine: string;
  engines: EngineOption[];
}

export interface AuthUser {
  username: string;
  account_id: string;
  role: string;
  display_name?: string;
}

export interface AuthMeResponse {
  auth_enabled: boolean;
  user: AuthUser;
}

export interface LoginResponse {
  access_token: string;
  token_type: "bearer" | string;
  auth_enabled?: boolean;
  user: AuthUser;
}

export type JobState = "queued" | "running" | "done" | "failed" | string;

export interface WebJob {
  job_id: string;
  account_id?: string;
  knowledge_base_id?: string;
  document_id?: string | null;
  file_sha256?: string | null;
  processing_fingerprint?: string | null;
  reused?: boolean;
  batch_id?: string | null;
  file_name: string;
  engine: string;
  engine_label?: string;
  queue_index?: number | null;
  queue_total?: number | null;
  state: JobState;
  progress: number;
  message: string;
  total_pages?: number | null;
  error?: string | null;
  markdown_url?: string | null;
  download_url?: string | null;
  pages_url?: string | null;
  created_at?: string;
  updated_at?: string;
  log_tail?: string[];
}

export interface BatchJobsResponse {
  batch_id: string;
  count: number;
  skipped: number;
  jobs: WebJob[];
}

export interface JobsResponse {
  account_id: string;
  knowledge_base_id: string;
  count: number;
  jobs: WebJob[];
}

export interface KnowledgeDocument {
  account_id: string;
  knowledge_base_id: string;
  document_id: string;
  file_name: string;
  file_ext?: string;
  file_size?: number | null;
  file_sha256?: string | null;
  status: string;
  ocr_engine?: string;
  page_count?: number | null;
  processed_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  markdown_url?: string | null;
  download_url?: string | null;
  pages_url?: string | null;
}

export interface KnowledgeDocumentsResponse {
  account_id: string;
  knowledge_base_id: string;
  count: number;
  documents: KnowledgeDocument[];
}

export interface OcrPage {
  page_number: number;
  markdown: string;
}

export interface PagesResponse {
  source_file: string;
  total_pages: number;
  pages: OcrPage[];
}

export interface KeywordResult {
  result_id: string;
  source_file: string;
  page_start: number;
  page_end: number;
  extraction_method: string;
  matched_keywords: string[];
  text: string;
}

export interface ExtractKeywordOptions {
  keywords: string;
  matchMode: "any" | "all";
  granularity: "paragraph" | "page";
  contextBefore: number;
  contextAfter: number;
  useRegex: boolean;
  caseSensitive: boolean;
  normalizeChinese: boolean;
  deduplicate: boolean;
}

export interface KeywordExtractionResponse {
  keywords: string[];
  match_mode: "any" | "all";
  granularity: "paragraph" | "page";
  use_regex: boolean;
  case_sensitive: boolean;
  normalize_chinese: boolean;
  deduplicate: boolean;
  count: number;
  markdown: string;
  results: KeywordResult[];
}

export interface LlmStatusResponse {
  provider: string;
  configured: boolean;
  model: string;
  temperature: number;
  max_tokens: number;
  web_search_enabled?: boolean;
}

export type LlmMessageRole = "system" | "user" | "assistant";

export interface LlmMessage {
  message_id: string;
  role: LlmMessageRole;
  content: string;
  created_at: string;
  pending?: boolean;
  error?: boolean;
}

export interface LlmConversation {
  conversation_id: string;
  account_id: string;
  knowledge_base_id: string;
  title: string;
  origin?: "global" | "reader" | string;
  system_prompt: string;
  context_mode?: "none" | "documents" | string;
  context_document_ids?: string[];
  context_documents?: LlmContextDocument[];
  message_count: number;
  messages?: LlmMessage[];
  created_at: string;
  updated_at: string;
}

export interface LlmContextDocument {
  document_id: string;
  file_name?: string;
  title?: string;
  account_id?: string;
  knowledge_base_id?: string;
  page_count?: number | null;
  order?: number;
}

export interface LlmConversationsResponse {
  account_id: string;
  knowledge_base_id: string;
  count: number;
  conversations: LlmConversation[];
}

export interface CreateLlmConversationOptions {
  accountId?: string;
  knowledgeBaseId?: string;
  title?: string;
  origin?: "global" | "reader";
  systemPrompt?: string;
  contextDocuments?: Array<{ document_id: string }>;
  messages?: Array<{ role: LlmMessageRole; content: string }>;
}

export interface SendLlmMessageResponse {
  conversation: LlmConversation;
  user_message: LlmMessage;
  assistant_message: LlmMessage;
}

export interface StreamLlmMessageHandlers {
  onDelta?: (delta: string) => void;
}

export interface SendLlmMessageOptions {
  contextDocuments?: Array<{ document_id: string }>;
  webSearchEnabled?: boolean;
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function listEngines(): Promise<EnginesResponse> {
  return fetchJson<EnginesResponse>("/api/engines");
}

export function setAuthToken(token: string) {
  window.localStorage.setItem(authTokenStorageKey, token);
}

export function getAuthToken(): string {
  return window.localStorage.getItem(authTokenStorageKey) || "";
}

export function clearAuthToken() {
  window.localStorage.removeItem(authTokenStorageKey);
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const data = await fetchJson<LoginResponse>("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });
  setAuthToken(data.access_token);
  return data;
}

export async function getCurrentUser(): Promise<AuthMeResponse> {
  return fetchJson<AuthMeResponse>("/api/auth/me");
}

export async function logout(): Promise<void> {
  try {
    await fetchJson<{ ok: boolean }>("/api/auth/logout", { method: "POST" });
  } finally {
    clearAuthToken();
  }
}

export async function listKnowledgeDocuments(options: {
  accountId: string;
  knowledgeBaseId: string;
  query?: string;
  limit?: number;
}): Promise<KnowledgeDocumentsResponse> {
  const params = new URLSearchParams({
    account_id: options.accountId,
    knowledge_base_id: options.knowledgeBaseId,
    limit: String(options.limit || 100)
  });
  if (options.query?.trim()) params.set("q", options.query.trim());
  return fetchJson<KnowledgeDocumentsResponse>(`/api/documents?${params.toString()}`);
}

export async function listJobs(options: {
  accountId: string;
  knowledgeBaseId: string;
  states?: string[];
  limit?: number;
}): Promise<JobsResponse> {
  const params = new URLSearchParams({
    account_id: options.accountId,
    knowledge_base_id: options.knowledgeBaseId,
    limit: String(options.limit || 100)
  });
  if (options.states?.length) params.set("state", options.states.join(","));
  return fetchJson<JobsResponse>(`/api/jobs?${params.toString()}`);
}

export function createJob(
  file: File,
  engine: string,
  onUploadProgress?: (percent: number) => void
): Promise<WebJob> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("engine", engine);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    applyXhrAuth(xhr);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onUploadProgress?.(Math.round((event.loaded / event.total) * 8));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as WebJob);
        } catch (error) {
          reject(error);
        }
        return;
      }
      reject(new Error(readErrorMessage(xhr.responseText) || "上传失败"));
    };
    xhr.onerror = () => reject(new Error("网络错误"));
    xhr.send(formData);
  });
}

export function createJobs(
  files: File[],
  engine: string,
  onUploadProgress?: (percent: number) => void
): Promise<BatchJobsResponse> {
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file, filePath(file));
  }
  formData.append("engine", engine);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs/batch");
    applyXhrAuth(xhr);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onUploadProgress?.(Math.round((event.loaded / event.total) * 8));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as BatchJobsResponse);
        } catch (error) {
          reject(error);
        }
        return;
      }
      reject(new Error(readErrorMessage(xhr.responseText) || "上传失败"));
    };
    xhr.onerror = () => reject(new Error("网络错误"));
    xhr.send(formData);
  });
}

export async function getJob(jobId: string): Promise<WebJob> {
  return fetchJson<WebJob>(`/api/jobs/${encodeURIComponent(jobId)}`);
}

export async function getMarkdown(url: string): Promise<string> {
  const response = await fetch(url, { headers: authHeaders() });
  if (!response.ok) {
    throw new Error(await readFetchError(response, "Markdown 加载失败"));
  }
  return response.text();
}

export async function downloadTextFile(url: string, fallbackName: string): Promise<void> {
  const response = await fetch(url, { headers: authHeaders() });
  if (!response.ok) {
    throw new Error(await readFetchError(response, "Download failed"));
  }
  const blob = await response.blob();
  const objectUrl = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = fileNameFromDisposition(response.headers.get("content-disposition")) || fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(objectUrl);
}

export async function getPages(url: string): Promise<PagesResponse> {
  return fetchJson<PagesResponse>(url);
}

export async function extractKeywords(
  jobId: string,
  options: ExtractKeywordOptions
): Promise<KeywordExtractionResponse> {
  const formData = new FormData();
  formData.append("keywords", options.keywords);
  formData.append("match_mode", options.matchMode);
  formData.append("context_before", String(options.contextBefore || 0));
  formData.append("context_after", String(options.contextAfter || 0));
  formData.append("granularity", options.granularity);
  formData.append("use_regex", String(options.useRegex));
  formData.append("case_sensitive", String(options.caseSensitive));
  formData.append("normalize_chinese", String(options.normalizeChinese));
  formData.append("deduplicate", String(options.deduplicate));

  return fetchJson<KeywordExtractionResponse>(`/api/jobs/${encodeURIComponent(jobId)}/extract-keywords`, {
    method: "POST",
    body: formData
  });
}

export async function extractDocumentKeywords(
  documentId: string,
  accountId: string,
  knowledgeBaseId: string,
  options: ExtractKeywordOptions
): Promise<KeywordExtractionResponse> {
  const formData = new FormData();
  formData.append("account_id", accountId);
  formData.append("knowledge_base_id", knowledgeBaseId);
  formData.append("keywords", options.keywords);
  formData.append("match_mode", options.matchMode);
  formData.append("context_before", String(options.contextBefore || 0));
  formData.append("context_after", String(options.contextAfter || 0));
  formData.append("granularity", options.granularity);
  formData.append("use_regex", String(options.useRegex));
  formData.append("case_sensitive", String(options.caseSensitive));
  formData.append("normalize_chinese", String(options.normalizeChinese));
  formData.append("deduplicate", String(options.deduplicate));

  return fetchJson<KeywordExtractionResponse>(`/api/documents/${encodeURIComponent(documentId)}/extract-keywords`, {
    method: "POST",
    body: formData
  });
}

export async function getLlmStatus(): Promise<LlmStatusResponse> {
  return fetchJson<LlmStatusResponse>("/api/llm/status");
}

export async function listLlmConversations(options: {
  accountId?: string;
  knowledgeBaseId?: string;
  documentId?: string;
  contextMode?: "none" | "documents";
  limit?: number;
} = {}): Promise<LlmConversationsResponse> {
  const params = new URLSearchParams({
    account_id: options.accountId || "local",
    knowledge_base_id: options.knowledgeBaseId || "default",
    limit: String(options.limit || 100)
  });
  if (options.documentId) params.set("document_id", options.documentId);
  if (options.contextMode) params.set("context_mode", options.contextMode);
  return fetchJson<LlmConversationsResponse>(`/api/llm/conversations?${params.toString()}`);
}

export async function createLlmConversation(options: CreateLlmConversationOptions = {}): Promise<LlmConversation> {
  return fetchJson<LlmConversation>("/api/llm/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      account_id: options.accountId,
      knowledge_base_id: options.knowledgeBaseId,
      title: options.title,
      origin: options.origin,
      system_prompt: options.systemPrompt,
      context_documents: options.contextDocuments,
      messages: options.messages
    })
  });
}

export async function getLlmConversation(conversationId: string): Promise<LlmConversation> {
  return fetchJson<LlmConversation>(`/api/llm/conversations/${encodeURIComponent(conversationId)}`);
}

export async function sendLlmMessage(
  conversationId: string,
  content: string,
  options: SendLlmMessageOptions = {}
): Promise<SendLlmMessageResponse> {
  return fetchJson<SendLlmMessageResponse>(`/api/llm/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content,
      context_documents: options.contextDocuments,
      options: llmRequestOptions(options)
    })
  });
}

export async function streamLlmMessage(
  conversationId: string,
  content: string,
  handlers: StreamLlmMessageHandlers = {},
  options: SendLlmMessageOptions = {}
): Promise<SendLlmMessageResponse> {
  const response = await fetch(
    `/api/llm/conversations/${encodeURIComponent(conversationId)}/messages/stream`,
    withAuth({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content,
        context_documents: options.contextDocuments,
        options: llmRequestOptions(options)
      })
    })
  );
  if (!response.ok) {
    throw new ApiError(response.status, await readFetchError(response, "发送失败"));
  }
  if (!response.body) {
    throw new Error("当前浏览器不支持流式响应");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: SendLlmMessageResponse | null = null;

  const processEvent = (raw: string) => {
    const lines = raw.split(/\r?\n/);
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) return;
    const data = JSON.parse(dataLines.join("\n"));
    if (eventName === "delta") {
      handlers.onDelta?.(String(data.delta || ""));
      return;
    }
    if (eventName === "done") {
      result = data as SendLlmMessageResponse;
      return;
    }
    if (eventName === "error") {
      throw new ApiError(Number(data.status || 502), String(data.detail || "发送失败"));
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const events = buffer.split(/\n\n/);
    buffer = events.pop() || "";
    for (const event of events) {
      if (event.trim()) processEvent(event);
    }
    if (done) break;
  }
  if (buffer.trim()) processEvent(buffer);
  if (!result) throw new Error("流式响应未返回完成事件");
  return result;
}

function llmRequestOptions(options: SendLlmMessageOptions): Record<string, unknown> | undefined {
  if (typeof options.webSearchEnabled !== "boolean") return undefined;
  return { web_search_enabled: options.webSearchEnabled };
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, withAuth(init));
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new ApiError(response.status, readErrorPayload(data) || response.statusText || "请求失败");
  }
  return data as T;
}

const authTokenStorageKey = "ocean.auth.token.v1";

function withAuth(init: RequestInit = {}): RequestInit {
  return {
    ...init,
    headers: {
      ...authHeaders(),
      ...(init.headers || {})
    }
  };
}

function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function applyXhrAuth(xhr: XMLHttpRequest) {
  const token = getAuthToken();
  if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
}

function fileNameFromDisposition(value: string | null): string {
  if (!value) return "";
  const utf8Match = value.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1]);
  const asciiMatch = value.match(/filename="?([^";]+)"?/i);
  return asciiMatch?.[1] || "";
}

async function readFetchError(response: Response, fallback: string): Promise<string> {
  const text = await response.text();
  return readErrorMessage(text) || fallback;
}

function readErrorMessage(text: string): string {
  if (!text) return "";
  try {
    return readErrorPayload(JSON.parse(text)) || text;
  } catch {
    return text;
  }
}

function readErrorPayload(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  return "";
}

function filePath(file: File): string {
  return (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
}

export interface EngineOption {
  value: string;
  label: string;
  description: string;
}

export interface EnginesResponse {
  default_engine: string;
  engines: EngineOption[];
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
}

export type LlmMessageRole = "system" | "user" | "assistant";

export interface LlmMessage {
  message_id: string;
  role: LlmMessageRole;
  content: string;
  created_at: string;
}

export interface LlmConversation {
  conversation_id: string;
  account_id: string;
  knowledge_base_id: string;
  title: string;
  system_prompt: string;
  message_count: number;
  messages?: LlmMessage[];
  created_at: string;
  updated_at: string;
}

export interface CreateLlmConversationOptions {
  accountId?: string;
  knowledgeBaseId?: string;
  title?: string;
  systemPrompt?: string;
  messages?: Array<{ role: LlmMessageRole; content: string }>;
}

export interface SendLlmMessageResponse {
  conversation: LlmConversation;
  user_message: LlmMessage;
  assistant_message: LlmMessage;
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
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(await readFetchError(response, "Markdown 加载失败"));
  }
  return response.text();
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

export async function createLlmConversation(options: CreateLlmConversationOptions = {}): Promise<LlmConversation> {
  return fetchJson<LlmConversation>("/api/llm/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      account_id: options.accountId,
      knowledge_base_id: options.knowledgeBaseId,
      title: options.title,
      system_prompt: options.systemPrompt,
      messages: options.messages
    })
  });
}

export async function sendLlmMessage(conversationId: string, content: string): Promise<SendLlmMessageResponse> {
  return fetchJson<SendLlmMessageResponse>(`/api/llm/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content })
  });
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new ApiError(response.status, readErrorPayload(data) || response.statusText || "请求失败");
  }
  return data as T;
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

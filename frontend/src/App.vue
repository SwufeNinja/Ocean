<template>
  <main class="shell">
    <nav class="view-tabs" aria-label="工作台页面切换">
      <button
        v-for="item in viewItems"
        :key="item.value"
        class="view-tab"
        :class="{ active: activeView === item.value }"
        type="button"
        :aria-label="item.label"
        :title="item.label"
        @click="switchView(item.value)"
      >
        <component :is="item.icon" :size="21" :stroke-width="1.9" aria-hidden="true" />
      </button>
    </nav>

    <div class="workspace">
      <section v-if="activeView === 'upload'" class="upload-page">
      <form class="upload" @submit.prevent="startUpload">
        <label
          class="drop"
          :class="{ dragging }"
          for="pdfFile"
          @dragenter.prevent="dragging = true"
          @dragover.prevent="dragging = true"
          @dragleave.prevent="dragging = false"
          @drop.prevent="onDrop"
        >
          <span>
            <strong>把 PDF 拖到这里</strong>
            或点击选择文件；大 PDF 会按当前引擎配置自动分段处理
          </span>
        </label>
        <input id="pdfFile" ref="fileInput" name="file" type="file" accept="application/pdf,.pdf" multiple @change="onFileChange" />
        <input
          id="folderInput"
          ref="folderInput"
          name="folder"
          type="file"
          accept="application/pdf,.pdf"
          multiple
          webkitdirectory
          directory
          @change="onFolderChange"
        />

        <div class="field">
          <label class="field-label">OCR 引擎</label>
          <div class="engine-grid">
            <label v-for="item in engines" :key="item.value" class="engine-card" :class="{ active: engine === item.value }">
              <input v-model="engine" type="radio" :value="item.value" :disabled="isBusy" />
              <b>{{ item.label }}</b>
              <small>{{ item.description }}</small>
            </label>
          </div>
        </div>

        <div class="actions">
          <label class="file-button" for="pdfFile">选择 PDF</label>
          <label class="file-button" for="folderInput">选择文件夹</label>
          <button id="startBtn" type="submit" :disabled="!selectedFiles.length || isBusy">{{ isBusy ? "处理中..." : "开始解析" }}</button>
          <button v-if="selectedFiles.length && !isBusy" class="secondary" type="button" @click="clearSelectedFiles">清空列表</button>
          <span class="file-name">{{ fileSummary }}</span>
        </div>

        <div class="status">
          <div class="status-line">
            <span>{{ statusText }}</span>
            <span>{{ roundedProgress }}%</span>
          </div>
          <div class="bar"><i :style="{ width: progress + '%' }"></i></div>
          <div v-if="logs.length" class="logs">{{ logs.join("\n") }}</div>
        </div>

        <div v-if="activeJobs.length" class="job-list" aria-label="OCR 任务队列">
          <button
            v-for="item in activeJobs"
            :key="item.job_id"
            class="job-item"
            :class="{ active: activeJob?.job_id === item.job_id, done: item.state === 'done', failed: item.state === 'failed' }"
            type="button"
            @click="selectJob(item, { openReader: item.state === 'done' })"
          >
            <span>{{ queueLabel(item) }}</span>
            <small>{{ item.file_name }} / {{ jobStateLabel(item) }}</small>
          </button>
        </div>
      </form>
      </section>

      <section v-else-if="activeView === 'library'" class="library-page">
        <div class="library-shell">
          <div class="library-toolbar">
            <label class="library-search">
              <Search :size="16" :stroke-width="2" aria-hidden="true" />
              <input v-model="libraryQuery" type="search" placeholder="搜索文件名或全文" @keyup.enter="loadLibraryDocuments" />
            </label>
            <button type="button" class="icon-text-button" :disabled="isLibraryLoading" @click="loadLibraryDocuments">
              <Search :size="16" :stroke-width="2" aria-hidden="true" />
              <span>搜索</span>
            </button>
            <button class="secondary icon-text-button" type="button" :disabled="isLibraryLoading" @click="loadLibraryDocuments">
              <RefreshCw :size="16" :stroke-width="2" aria-hidden="true" />
              <span>{{ isLibraryLoading ? "刷新中" : "刷新" }}</span>
            </button>
          </div>

          <div class="library-status">{{ libraryStatus }}</div>

          <div v-if="libraryDocuments.length" class="library-list" aria-label="知识库文件列表">
            <button
              v-for="item in libraryDocuments"
              :key="item.document_id"
              class="library-item"
              :class="{ active: activeLibraryDocumentId === item.document_id }"
              type="button"
              @click="openLibraryDocument(item)"
            >
              <span class="library-file-icon">
                <FileText :size="19" :stroke-width="1.9" aria-hidden="true" />
              </span>
              <span class="library-item-main">
                <strong>{{ item.file_name }}</strong>
                <small>{{ libraryDocumentMeta(item) }}</small>
              </span>
              <span class="library-item-state">{{ item.ocr_engine || "OCR" }}</span>
            </button>
          </div>

          <div v-else class="library-empty">
            <h2>知识库还没有文件</h2>
            <p>上传并解析 PDF 后，已处理文件会出现在这里。</p>
            <button type="button" @click="switchView('upload')">去上传解析</button>
          </div>
        </div>
      </section>

      <section v-else-if="activeView === 'reader'" class="reader">
        <div v-if="markdown" class="reader-ready">
          <div class="reader-layout" :style="readerLayoutStyle">
            <aside class="keyword-panel" aria-label="关键词搜索">
              <div class="keyword-panel-head">
                <h2>关键词段落提取</h2>
                <span v-if="keywordResults.length" class="keyword-count">{{ keywordResults.length }} 条</span>
              </div>
              <div class="field">
                <label class="field-label">关键词（换行或逗号分隔）</label>
                <textarea v-model="keywordInput" class="keyword-box" placeholder="请输入关键词..."></textarea>
              </div>
              <div class="option-row">
                <label class="mini-field">
                  <span class="field-label">匹配模式</span>
                  <select v-model="keywordMode">
                    <option value="any">任意命中</option>
                    <option value="all">全部命中</option>
                  </select>
                </label>
                <label class="mini-field">
                  <span class="field-label">提取粒度</span>
                  <select v-model="keywordGranularity">
                    <option value="paragraph">命中段落</option>
                    <option value="page">命中页面</option>
                  </select>
                </label>
                <label class="mini-field">
                  <span class="field-label">前文段数</span>
                  <input v-model.number="keywordContextBefore" type="number" min="0" max="5" :disabled="keywordGranularity === 'page'" />
                </label>
                <label class="mini-field">
                  <span class="field-label">后文段数</span>
                  <input v-model.number="keywordContextAfter" type="number" min="0" max="5" :disabled="keywordGranularity === 'page'" />
                </label>
              </div>
              <div class="check-row">
                <label><input v-model="keywordUseRegex" type="checkbox" /> 使用正则</label>
                <label><input v-model="keywordCaseSensitive" type="checkbox" /> 区分大小写</label>
                <label><input v-model="keywordNormalizeChinese" type="checkbox" /> 简繁转换匹配</label>
                <label><input v-model="keywordDeduplicate" type="checkbox" /> 去重合并</label>
              </div>
              <div class="actions">
                <button type="button" :disabled="isExtracting || activeJob?.state !== 'done'" @click="extractKeywordResults">
                  {{ isExtracting ? "提取中..." : "提取关键词" }}
                </button>
              </div>
              <div class="extract-status">{{ keywordStatus }}</div>
            </aside>

            <div
              class="reader-resizer"
              :class="{ active: activeReaderResizeTarget === 'left' }"
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize keyword panel"
              :aria-valuemin="minReaderLeftWidth"
              :aria-valuemax="maxReaderLeftWidth"
              :aria-valuenow="readerLeftWidth"
              @pointerdown="startReaderResize('left', $event)"
            ></div>

            <div class="reader-content">
              <div ref="readingFrame" class="reading-frame" :class="{ 'has-reader-inline-action': isKeywordResultView }" :style="readerZoomStyle">
                <div v-if="isReaderMenuOpen" class="reader-menu-scrim" aria-hidden="true" @click="closeReaderMenu"></div>
                <div class="reader-menu" @click.stop>
                  <button
                    class="reader-menu-button"
                    type="button"
                    aria-label="阅读器选项"
                    :aria-expanded="isReaderMenuOpen"
                    @click="toggleReaderMenu"
                  >
                    <MoreHorizontal :size="20" :stroke-width="2" aria-hidden="true" />
                  </button>
                  <div v-if="isReaderMenuOpen" class="reader-menu-panel">
                    <div class="reader-menu-section">
                      <span class="reader-menu-label">尺寸调整</span>
                      <div class="zoom-control reader-menu-zoom" aria-label="阅读器缩放">
                        <button class="secondary" type="button" :disabled="readerZoom <= minReaderZoom" @click="adjustZoom(-10)">-</button>
                        <input
                          v-model.number="zoomInput"
                          type="number"
                          :min="minReaderZoom"
                          :max="maxReaderZoom"
                          step="10"
                          aria-label="缩放百分比"
                          @change="applyZoomInput"
                          @keyup.enter="applyZoomInput"
                        />
                        <span class="zoom-label">%</span>
                        <button class="secondary" type="button" :disabled="readerZoom >= maxReaderZoom" @click="adjustZoom(10)">+</button>
                      </div>
                    </div>
                    <button class="secondary reader-menu-action" type="button" @click="toggleRawFromReaderMenu">
                      {{ showRaw ? "预览模式" : "查看源码" }}
                    </button>
                    <a v-if="downloadUrl" class="download-link reader-menu-action" :href="downloadUrl" @click="closeReaderMenu">下载 MD</a>
                    <button v-else class="secondary reader-menu-action" type="button" disabled>下载 MD</button>
                  </div>
                </div>
                <div v-if="isKeywordResultView" class="reader-inline-bar">
                  <button class="secondary reader-inline-original-button" type="button" @click="restoreOriginalDocument">
                    查看原文
                  </button>
                  <div v-if="currentKeywordLabel || currentSourcePageLabel" class="reader-inline-meta">
                    <span v-if="currentKeywordLabel">命中关键词：{{ currentKeywordLabel }}</span>
                    <span v-if="currentSourcePageLabel">原文页码：{{ currentSourcePageLabel }}</span>
                  </div>
                </div>
                <textarea v-if="showRaw" class="raw-box" :value="currentDisplayMarkdown" readonly></textarea>
                <article v-else class="markdown" v-html="renderedMarkdown"></article>
              </div>
              <div v-if="hasPageItems" class="page-nav">
                <button class="secondary" type="button" :disabled="currentPage <= 1" @click="goToPreviousPage">上一页</button>
                <input
                  v-model.number="pageInput"
                  type="number"
                  min="1"
                  :max="totalReaderPages"
                  aria-label="当前页码"
                  @change="jumpToPage"
                  @keyup.enter="jumpToPage"
                />
                <span>/ {{ totalReaderPages }}</span>
                <button class="secondary" type="button" :disabled="currentPage >= totalReaderPages" @click="goToNextPage">下一页</button>
              </div>
            </div>
            <div
              class="reader-resizer"
              :class="{ active: activeReaderResizeTarget === 'right' }"
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize tools panel"
              :aria-valuemin="minReaderRightWidth"
              :aria-valuemax="maxReaderRightWidth"
              :aria-valuenow="readerRightWidth"
              @pointerdown="startReaderResize('right', $event)"
            ></div>

            <aside class="reader-side-panel" aria-label="LLM 对话">
              <div class="llm-panel-head">
                <div class="llm-title">
                  <Bot :size="18" :stroke-width="2" aria-hidden="true" />
                  <span>文档对话</span>
                </div>
                <button
                  class="llm-icon-button"
                  type="button"
                  aria-label="新建对话"
                  title="新建对话"
                  :disabled="isLlmPreparing || !markdown"
                  @click="startLlmConversationForCurrentDocument"
                >
                  <Plus :size="17" :stroke-width="2" aria-hidden="true" />
                </button>
              </div>

              <div class="llm-context">
                <span class="llm-context-label">{{ llmStatusLabel }}</span>
                <strong>{{ activeJob?.file_name || readerTitle }}</strong>
              </div>

              <div ref="llmMessageList" class="llm-message-list" aria-live="polite">
                <div v-if="!llmVisibleMessages.length" class="llm-empty">
                  <Bot :size="22" :stroke-width="1.8" aria-hidden="true" />
                  <p>围绕当前文档提问，支持连续追问。</p>
                </div>
                <div
                  v-for="message in llmVisibleMessages"
                  :key="message.message_id"
                  class="llm-message"
                  :class="`llm-message-${message.role}`"
                >
                  <div class="llm-message-role">{{ message.role === "user" ? "你" : "助手" }}</div>
                  <div class="llm-message-content">{{ message.content }}</div>
                </div>
                <div v-if="isLlmSending" class="llm-message llm-message-assistant">
                  <div class="llm-message-role">助手</div>
                  <div class="llm-message-content">正在思考...</div>
                </div>
              </div>

              <form class="llm-compose" @submit.prevent="sendCurrentLlmMessage">
                <div v-if="llmError" class="llm-error">{{ llmError }}</div>
                <textarea
                  v-model="llmInput"
                  class="llm-input"
                  rows="3"
                  placeholder="询问当前文档..."
                  :disabled="isLlmSending || isLlmPreparing || !markdown"
                  @keydown="onLlmInputKeydown"
                ></textarea>
                <div class="llm-compose-actions">
                  <span>{{ llmConversation ? "多轮上下文已开启" : "会在发送前创建会话" }}</span>
                  <button
                    class="llm-send-button"
                    type="submit"
                    aria-label="发送"
                    :disabled="!canSendLlmMessage"
                  >
                    <Send :size="17" :stroke-width="2.1" aria-hidden="true" />
                  </button>
                </div>
              </form>
            </aside>
          </div>
        </div>
        <div v-else class="empty-state">
          <span class="empty-kicker">Markdown Reader</span>
          <h2>还没有可阅读的 Markdown。</h2>
          <p>先在“上传解析”页完成 OCR，处理成功后这里会显示分页阅读器和下载入口。</p>
          <button type="button" @click="switchView('upload')">去上传解析</button>
        </div>
      </section>
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import type { Component, CSSProperties } from "vue";
import { BookOpen, Bot, Database, FileText, MoreHorizontal, Plus, RefreshCw, Search, Send, Upload } from "@lucide/vue";
import DOMPurify from "dompurify";
import { marked } from "marked";
import {
  createJobs,
  extractDocumentKeywords,
  extractKeywords,
  createLlmConversation,
  getJob,
  getLlmStatus,
  getMarkdown,
  getPages,
  listKnowledgeDocuments,
  listEngines,
  sendLlmMessage,
  type EngineOption,
  type KnowledgeDocument,
  type KeywordExtractionResponse,
  type KeywordResult,
  type LlmConversation,
  type LlmMessage,
  type LlmStatusResponse,
  type WebJob
} from "./api/jobs";

interface ReaderPageItem {
  pageNumber: number;
  markdown: string;
  keywordLabel?: string;
  matchedKeywords?: string[];
  sourcePageLabel?: string;
  sourcePageNumber?: number;
}

interface OriginalReaderState {
  markdown: string;
  displayMarkdown: string;
  pageItems: ReaderPageItem[];
  readerTitle: string;
}

type ActiveView = "upload" | "library" | "reader";
type ReaderResizeTarget = "left" | "right";

interface ViewItem {
  value: ActiveView;
  label: string;
  icon: Component;
}

const defaultEngines: EngineOption[] = [
  { value: "mineru", label: "MinerU", description: "默认：适合版面复杂的长文档解析" },
  { value: "paddleocr", label: "PaddleOCR", description: "适合快速文档 OCR" }
];

const activeView = ref<ActiveView>("upload");
const engines = ref<EngineOption[]>(defaultEngines);
const engine = ref("mineru");
const selectedFiles = ref<File[]>([]);
const dragging = ref(false);
const isBusy = ref(false);
const activeJobs = ref<WebJob[]>([]);
const activeJobId = ref<string | null>(null);
const accountId = ref("local");
const knowledgeBaseId = ref("default");
const libraryDocuments = ref<KnowledgeDocument[]>([]);
const libraryQuery = ref("");
const libraryStatus = ref("正在加载知识库");
const isLibraryLoading = ref(false);
const activeLibraryDocumentId = ref<string | null>(null);
let pollTimer: number | undefined;

const progress = ref(0);
const statusText = ref("等待上传");
const logs = ref<string[]>([]);
const markdown = ref("");
const displayMarkdown = ref("");
const originalReaderState = ref<OriginalReaderState | null>(null);
const readerTitle = ref("Markdown 阅读器");
const downloadUrl = ref("");
const showRaw = ref(false);
const isReaderMenuOpen = ref(false);
const isKeywordResultView = ref(false);
const readerZoom = ref(100);
const zoomInput = ref(100);
const minReaderZoom = 50;
const maxReaderZoom = 200;
const minReaderLeftWidth = 260;
const maxReaderLeftWidth = 460;
const minReaderRightWidth = 260;
const maxReaderRightWidth = 460;
const readerLeftWidth = ref(300);
const readerRightWidth = ref(300);
const activeReaderResizeTarget = ref<ReaderResizeTarget | null>(null);
let readerResizeState:
  | {
      target: ReaderResizeTarget;
      startX: number;
      startWidth: number;
    }
  | null = null;
const pageItems = ref<ReaderPageItem[]>([]);
const currentPage = ref(1);
const pageInput = ref(1);

const keywordInput = ref("");
const keywordMode = ref<"any" | "all">("any");
const keywordGranularity = ref<"paragraph" | "page">("paragraph");
const keywordUseRegex = ref(false);
const keywordCaseSensitive = ref(true);
const keywordNormalizeChinese = ref(false);
const keywordDeduplicate = ref(true);
const keywordContextBefore = ref(1);
const keywordContextAfter = ref(1);
const keywordResults = ref<KeywordResult[]>([]);
const keywordStatus = ref("OCR 完成后可以在这里提取关键词段落。");
const isExtracting = ref(false);

const llmStatus = ref<LlmStatusResponse | null>(null);
const llmConversation = ref<LlmConversation | null>(null);
const llmInput = ref("");
const llmError = ref("");
const isLlmPreparing = ref(false);
const isLlmSending = ref(false);
const llmContextLimit = 12000;

const fileInput = ref<HTMLInputElement | null>(null);
const folderInput = ref<HTMLInputElement | null>(null);
const readingFrame = ref<HTMLDivElement | null>(null);
const llmMessageList = ref<HTMLDivElement | null>(null);

const viewItems = computed<ViewItem[]>(() => [
  {
    value: "upload",
    label: "上传解析",
    icon: Upload
  },
  {
    value: "library",
    label: "知识库",
    icon: Database
  },
  {
    value: "reader",
    label: "Markdown 阅读",
    icon: BookOpen
  }
]);
const fileSummary = computed(() => {
  if (!selectedFiles.value.length) return "还没有选择文件";
  const totalSize = selectedFiles.value.reduce((sum, item) => sum + item.size, 0);
  if (selectedFiles.value.length === 1) {
    return `${filePath(selectedFiles.value[0])} / ${(totalSize / 1024 / 1024).toFixed(2)} MB`;
  }
  return `${selectedFiles.value.length} 个 PDF / ${(totalSize / 1024 / 1024).toFixed(2)} MB`;
});
const roundedProgress = computed(() => Math.round(progress.value));
const activeJob = computed(() => activeJobs.value.find((item) => item.job_id === activeJobId.value) || null);
const readerHint = computed(() => {
  if (!activeJob.value) return "处理完成后会自动显示";
  const pages = activeJob.value.total_pages ? ` / ${activeJob.value.total_pages} 页` : "";
  return `${activeJob.value.file_name}${pages} / ${activeJob.value.engine_label || activeJob.value.engine}`;
});
const readerZoomStyle = computed<CSSProperties>(() => ({ "--reader-zoom": String(readerZoom.value / 100) }));
const readerLayoutStyle = computed<CSSProperties>(
  () =>
    ({
      "--reader-left-width": `${readerLeftWidth.value}px`,
      "--reader-right-width": `${readerRightWidth.value}px`
    }) as CSSProperties
);
const hasPageItems = computed(() => pageItems.value.length > 0);
const totalReaderPages = computed(() => pageItems.value.length || 1);
const currentPageItem = computed(() => pageItems.value[currentPage.value - 1] || null);
const currentDisplayMarkdown = computed(() => currentPageItem.value?.markdown || displayMarkdown.value);
const currentSourcePageLabel = computed(() => currentPageItem.value?.sourcePageLabel || "");
const currentKeywordLabel = computed(() => currentPageItem.value?.keywordLabel || "");
const renderedMarkdown = computed(() => {
  if (!currentDisplayMarkdown.value) return "";
  const html = marked.parse(currentDisplayMarkdown.value, { async: false }) as string;
  const cleanHtml = DOMPurify.sanitize(html);
  if (!isKeywordResultView.value) return cleanHtml;
  return highlightKeywordsInHtml(cleanHtml, currentPageItem.value?.matchedKeywords || []);
});
const llmVisibleMessages = computed<LlmMessage[]>(() =>
  (llmConversation.value?.messages || []).filter((message) => message.role === "user" || message.role === "assistant")
);
const llmStatusLabel = computed(() => {
  if (!llmStatus.value) return "正在检查 LLM";
  if (!llmStatus.value.configured) return "LLM 未配置";
  return llmStatus.value.model ? `LLM 已连接 · ${llmStatus.value.model}` : "LLM 已连接";
});
const canSendLlmMessage = computed(() =>
  Boolean(markdown.value && llmInput.value.trim() && !isLlmPreparing.value && !isLlmSending.value)
);

onMounted(() => {
  void loadEngineOptions();
  void loadLibraryDocuments();
  void loadLlmStatus();
});

onBeforeUnmount(() => {
  clearPollTimer();
  stopReaderResize();
});

function clearPollTimer() {
  if (pollTimer !== undefined) {
    window.clearTimeout(pollTimer);
    pollTimer = undefined;
  }
}

function resetReader() {
  markdown.value = "";
  displayMarkdown.value = "";
  originalReaderState.value = null;
  isKeywordResultView.value = false;
  pageItems.value = [];
  currentPage.value = 1;
  pageInput.value = 1;
  llmConversation.value = null;
  llmInput.value = "";
  llmError.value = "";
}

function switchView(view: ActiveView) {
  activeView.value = view;
  closeReaderMenu();
  if (view === "library") void loadLibraryDocuments();
  if (view === "reader") scrollReaderToTop();
}

async function onDocumentChange(event: Event) {
  const target = event.target as HTMLSelectElement;
  const job = activeJobs.value.find((item) => item.job_id === target.value);
  if (job) await selectJob(job, { openReader: false });
}

function documentOptionLabel(job: WebJob) {
  const index = queueLabel(job);
  return `${index} · ${job.file_name} · ${jobStateLabel(job)}`;
}

function documentStateClass(job: WebJob) {
  return {
    done: job.state === "done",
    failed: job.state === "failed",
    running: job.state === "running",
    queued: job.state === "queued"
  };
}

function setZoom(value: number) {
  const normalized = Math.round((Number(value) || 100) / 10) * 10;
  const clamped = Math.max(minReaderZoom, Math.min(maxReaderZoom, normalized));
  readerZoom.value = clamped;
  zoomInput.value = clamped;
}

function adjustZoom(delta: number) {
  setZoom(readerZoom.value + delta);
}

function applyZoomInput() {
  setZoom(zoomInput.value);
}

function resetZoom() {
  setZoom(100);
}

function toggleReaderMenu() {
  isReaderMenuOpen.value = !isReaderMenuOpen.value;
}

function closeReaderMenu() {
  isReaderMenuOpen.value = false;
}

function toggleRawFromReaderMenu() {
  if (!markdown.value) return;
  showRaw.value = !showRaw.value;
  closeReaderMenu();
}

function clampReaderPanelWidth(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, Math.round(value)));
}

function startReaderResize(target: ReaderResizeTarget, event: PointerEvent) {
  if (window.matchMedia("(max-width: 980px)").matches) return;
  event.preventDefault();
  stopReaderResize();
  readerResizeState = {
    target,
    startX: event.clientX,
    startWidth: target === "left" ? readerLeftWidth.value : readerRightWidth.value
  };
  activeReaderResizeTarget.value = target;
  document.body.classList.add("is-reader-resizing");
  window.addEventListener("pointermove", onReaderResizeMove);
  window.addEventListener("pointerup", stopReaderResize);
  window.addEventListener("pointercancel", stopReaderResize);
}

function onReaderResizeMove(event: PointerEvent) {
  if (!readerResizeState) return;
  const deltaX = event.clientX - readerResizeState.startX;
  if (readerResizeState.target === "left") {
    readerLeftWidth.value = clampReaderPanelWidth(
      readerResizeState.startWidth + deltaX,
      minReaderLeftWidth,
      maxReaderLeftWidth
    );
    return;
  }
  readerRightWidth.value = clampReaderPanelWidth(
    readerResizeState.startWidth - deltaX,
    minReaderRightWidth,
    maxReaderRightWidth
  );
}

function stopReaderResize() {
  window.removeEventListener("pointermove", onReaderResizeMove);
  window.removeEventListener("pointerup", stopReaderResize);
  window.removeEventListener("pointercancel", stopReaderResize);
  readerResizeState = null;
  activeReaderResizeTarget.value = null;
  document.body.classList.remove("is-reader-resizing");
}

async function loadEngineOptions() {
  try {
    const data = await listEngines();
    engines.value = data.engines?.length ? data.engines : defaultEngines;
    engine.value = data.default_engine || engine.value;
  } catch (error) {
    console.warn("Failed to load engines", error);
  }
}

async function loadLlmStatus() {
  try {
    llmStatus.value = await getLlmStatus();
    llmError.value = llmStatus.value.configured ? "" : "请先在后端配置 LLM_API_BASE_URL、LLM_API_KEY 和 LLM_MODEL。";
  } catch (error) {
    llmStatus.value = null;
    llmError.value = `LLM 状态检查失败：${getErrorMessage(error)}`;
  }
}

async function startLlmConversationForCurrentDocument() {
  if (!markdown.value || isLlmPreparing.value) return;
  isLlmPreparing.value = true;
  llmError.value = "";
  try {
    llmConversation.value = await createLlmConversation({
      accountId: activeJob.value?.account_id || accountId.value,
      knowledgeBaseId: activeJob.value?.knowledge_base_id || knowledgeBaseId.value,
      title: activeJob.value?.file_name || readerTitle.value,
      systemPrompt: buildLlmSystemPrompt()
    });
    await scrollLlmMessagesToBottom();
  } catch (error) {
    llmConversation.value = null;
    llmError.value = `创建对话失败：${getErrorMessage(error)}`;
  } finally {
    isLlmPreparing.value = false;
  }
}

function buildLlmSystemPrompt() {
  const source = activeJob.value?.file_name || readerTitle.value || "当前文档";
  const pages = totalReaderPages.value > 1 ? `共 ${totalReaderPages.value} 页。` : "";
  const rawContext = stripPageHeadings(markdown.value || displayMarkdown.value);
  const context = rawContext.length > llmContextLimit
    ? `${rawContext.slice(0, llmContextLimit)}\n\n[文档内容已截断，后续可结合用户问题继续定位。]`
    : rawContext;
  return [
    "你是一个严谨的文档阅读助手。",
    "优先依据提供的文档内容回答；如果文档内容不足以支持结论，要明确说明不确定。",
    "回答应简洁、结构清晰，可以引用页码、表格或原文要点。",
    `当前文档：${source}。${pages}`,
    "",
    "文档内容：",
    context
  ].join("\n");
}

async function sendCurrentLlmMessage() {
  const content = llmInput.value.trim();
  if (!content || isLlmSending.value || isLlmPreparing.value) return;
  llmError.value = "";
  if (!llmConversation.value) {
    await startLlmConversationForCurrentDocument();
  }
  if (!llmConversation.value) return;

  isLlmSending.value = true;
  llmInput.value = "";
  try {
    const data = await sendLlmMessage(llmConversation.value.conversation_id, content);
    llmConversation.value = data.conversation;
    await scrollLlmMessagesToBottom();
  } catch (error) {
    llmInput.value = content;
    llmError.value = `发送失败：${getErrorMessage(error)}`;
    void loadLlmStatus();
  } finally {
    isLlmSending.value = false;
    await scrollLlmMessagesToBottom();
  }
}

function onLlmInputKeydown(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  void sendCurrentLlmMessage();
}

async function scrollLlmMessagesToBottom() {
  await nextTick();
  const element = llmMessageList.value;
  if (element) element.scrollTop = element.scrollHeight;
}

async function loadLibraryDocuments() {
  isLibraryLoading.value = true;
  libraryStatus.value = "正在加载知识库";
  try {
    const data = await listKnowledgeDocuments({
      accountId: accountId.value,
      knowledgeBaseId: knowledgeBaseId.value,
      query: libraryQuery.value,
      limit: 200
    });
    libraryDocuments.value = data.documents || [];
    libraryStatus.value = libraryDocuments.value.length
      ? `共 ${libraryDocuments.value.length} 个文件`
      : libraryQuery.value.trim()
        ? "没有匹配的文件"
        : "暂无文件";
  } catch (error) {
    libraryDocuments.value = [];
    libraryStatus.value = `加载失败：${getErrorMessage(error)}`;
  } finally {
    isLibraryLoading.value = false;
  }
}

async function openLibraryDocument(item: KnowledgeDocument) {
  activeLibraryDocumentId.value = item.document_id;
  const job: WebJob = {
    job_id: `document:${item.document_id}`,
    account_id: item.account_id || accountId.value,
    knowledge_base_id: item.knowledge_base_id || knowledgeBaseId.value,
    document_id: item.document_id,
    file_sha256: item.file_sha256 || null,
    reused: true,
    file_name: item.file_name,
    engine: item.ocr_engine || "library",
    engine_label: item.ocr_engine || "知识库",
    state: "done",
    progress: 100,
    message: "来自知识库",
    total_pages: item.page_count || null,
    markdown_url: item.markdown_url || null,
    download_url: item.download_url || null,
    pages_url: item.pages_url || null,
    created_at: item.created_at || "",
    updated_at: item.updated_at || item.processed_at || ""
  };
  activeJobs.value = [job];
  activeJobId.value = job.job_id;
  keywordResults.value = [];
  keywordStatus.value = "OCR 完成后可以在这里提取关键词段落。";
  await loadJobResult(job, { openReader: true });
}

function libraryDocumentMeta(item: KnowledgeDocument) {
  return [
    item.page_count ? `${item.page_count} 页` : "页数未知",
    formatFileSize(item.file_size),
    formatDate(item.processed_at || item.updated_at || item.created_at)
  ].filter(Boolean).join(" / ");
}

function formatFileSize(value?: number | null) {
  if (!value) return "";
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(2)} MB`;
}

function formatDate(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  setFiles(input.files);
  input.value = "";
}

function onFolderChange(event: Event) {
  const input = event.target as HTMLInputElement;
  setFiles(input.files);
  input.value = "";
}

function onDrop(event: DragEvent) {
  dragging.value = false;
  setFiles(event.dataTransfer?.files || null);
}

function setFiles(fileList: FileList | File[] | null) {
  if (isBusy.value) return;
  const incomingFiles = Array.from(fileList || []);
  const pdfFiles = incomingFiles.filter((item) => filePath(item).toLowerCase().endsWith(".pdf"));
  const ignoredCount = incomingFiles.length - pdfFiles.length;

  activeView.value = "upload";
  if (activeJobs.value.length) {
    activeJobs.value = [];
    activeJobId.value = null;
    activeLibraryDocumentId.value = null;
    resetReader();
  }

  const existingKeys = new Set(selectedFiles.value.map(fileKey));
  const additions = pdfFiles.filter((item) => !existingKeys.has(fileKey(item)));
  selectedFiles.value = [...selectedFiles.value, ...additions];

  resetReader();
  readerTitle.value = "Markdown 阅读器";
  downloadUrl.value = "";
  keywordResults.value = [];
  keywordStatus.value = "OCR 完成后可以在这里提取关键词段落。";
  logs.value = ignoredCount ? [`已忽略 ${ignoredCount} 个非 PDF 文件`] : [];
  if (additions.length) {
    statusText.value = `已加入 ${additions.length} 个 PDF，待处理 ${selectedFiles.value.length} 个`;
  } else if (ignoredCount) {
    statusText.value = selectedFiles.value.length ? "没有新增 PDF 文件" : "未找到 PDF 文件";
  } else {
    statusText.value = "没有新增文件";
  }
}

function clearSelectedFiles() {
  selectedFiles.value = [];
  activeJobs.value = [];
  activeJobId.value = null;
  activeLibraryDocumentId.value = null;
  logs.value = [];
  progress.value = 0;
  statusText.value = "等待上传";
  resetReader();
}

async function startUpload() {
  if (!selectedFiles.value.length || isBusy.value) return;
  activeView.value = "upload";
  isBusy.value = true;
  progress.value = 0;
  statusText.value = "正在上传 PDF";
  logs.value = [];
  activeLibraryDocumentId.value = null;
  resetReader();
  readerTitle.value = "Markdown 阅读器";
  downloadUrl.value = "";
  keywordResults.value = [];
  keywordStatus.value = "OCR 完成后可以在这里提取关键词段落。";

  try {
    const batch = await createJobs(selectedFiles.value, engine.value, (percent) => setProgress(percent, "正在上传 PDF"));
    activeJobs.value = batch.jobs || [];
    activeJobId.value = activeJobs.value[0]?.job_id || null;
    setProgress(8, batch.count > 1 ? `已提交 ${batch.count} 个 OCR 任务` : "已提交 OCR 任务");
    if (batch.skipped) logs.value = [`已忽略 ${batch.skipped} 个非 PDF 文件`];
    void pollJobs();
  } catch (error) {
    failUpload(getErrorMessage(error));
  }
}

function failUpload(message: string) {
  setProgress(100, "上传失败");
  logs.value = [message];
  isBusy.value = false;
}

async function pollJobs() {
  clearPollTimer();
  if (!activeJobs.value.length) {
    isBusy.value = false;
    return;
  }

  try {
    const jobs = await Promise.all(activeJobs.value.map((item) => getJob(item.job_id)));
    activeJobs.value = jobs;

    const totalProgress = jobs.reduce((sum, item) => sum + (Number(item.progress) || 0), 0) / jobs.length;
    const finished = jobs.filter((item) => item.state === "done" || item.state === "failed").length;
    const failed = jobs.filter((item) => item.state === "failed").length;
    const running = jobs.find((item) => item.state === "running");
    const queued = jobs.find((item) => item.state === "queued");
    const selected = activeJobId.value ? jobs.find((item) => item.job_id === activeJobId.value) : null;
    const current = running || queued || selected || jobs[0] || null;
    if (!selected) {
      activeJobId.value = current?.job_id || null;
    }
    logs.value = activeJob.value?.log_tail || [];
    const prefix = jobs.length > 1 ? `队列 ${finished}/${jobs.length}` : "";
    const message = current?.message || (failed ? "部分任务失败" : "处理完成");
    setProgress(totalProgress, prefix ? `${prefix}：${message}` : message);

    if (jobs.length === 1 && activeJob.value?.state === "done" && activeJob.value.markdown_url && !markdown.value) {
      await loadJobResult(activeJob.value);
    }

    if (activeView.value !== "upload" && activeJob.value?.state === "done" && activeJob.value.markdown_url && !markdown.value) {
      await loadJobResult(activeJob.value, { openReader: false });
    }

    if (finished === jobs.length) {
      const selectedDone = activeJob.value?.state === "done" ? activeJob.value : null;
      const firstDone = jobs.find((item) => item.state === "done");
      if (!markdown.value && (selectedDone || firstDone)) await loadJobResult(selectedDone || firstDone);
      void loadLibraryDocuments();
      isBusy.value = false;
      return;
    }

    pollTimer = window.setTimeout(() => void pollJobs(), 2000);
  } catch (error) {
    statusText.value = `轮询失败：${getErrorMessage(error)}`;
    pollTimer = window.setTimeout(() => void pollJobs(), 4000);
  }
}

async function selectJob(job: WebJob, options: { openReader?: boolean } = {}) {
  activeJobId.value = job.job_id;
  activeLibraryDocumentId.value = isLibraryJob(job) ? job.document_id || null : null;
  logs.value = job.log_tail || [];
  keywordResults.value = [];
  keywordStatus.value = "OCR 完成后可以在这里提取关键词段落。";
  if (job.state === "done" && job.markdown_url) {
    await loadJobResult(job, { openReader: options.openReader ?? true });
    return;
  }
  resetReader();
  readerTitle.value = "Markdown 阅读器";
  downloadUrl.value = "";
}

async function loadJobResult(job: WebJob, options: { openReader?: boolean } = {}) {
  activeJobId.value = job.job_id;
  if (job.markdown_url) await loadMarkdown(job.markdown_url, job.pages_url, options);
  downloadUrl.value = job.download_url || "";
}

async function loadMarkdown(url: string, pagesUrl?: string | null, options: { openReader?: boolean } = {}) {
  markdown.value = await getMarkdown(url);
  displayMarkdown.value = stripPageHeadings(markdown.value);
  originalReaderState.value = null;
  isKeywordResultView.value = false;
  pageItems.value = [];
  if (pagesUrl) {
    try {
      await loadPageItems(pagesUrl);
    } catch (error) {
      console.warn("Failed to load OCR pages", error);
      pageItems.value = [{
        pageNumber: 1,
        markdown: displayMarkdown.value
      }];
    }
  }
  currentPage.value = 1;
  pageInput.value = 1;
  readerTitle.value = "OCR Markdown 阅读器";
  showRaw.value = false;
  await startLlmConversationForCurrentDocument();
  if (options.openReader !== false) activeView.value = "reader";
  await nextTick();
  scrollReaderToTop();
  if (options.openReader !== false) {
    document.querySelector(".reader")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function loadPageItems(url: string) {
  const data = await getPages(url);
  pageItems.value = (data.pages || []).map((item, index) => ({
    pageNumber: item.page_number || index + 1,
    markdown: stripPageHeadings(item.markdown || "")
  }));
}

function stripPageHeadings(value: string) {
  return String(value || "")
    .replace(/^##\s+Page\s+\d+\s*$/gim, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function setReaderPage(page: number) {
  const target = Math.max(1, Math.min(totalReaderPages.value, Number(page) || 1));
  currentPage.value = target;
  pageInput.value = target;
  scrollReaderToTop();
}

function jumpToPage() {
  setReaderPage(pageInput.value);
}

function goToPreviousPage() {
  setReaderPage(currentPage.value - 1);
}

function goToNextPage() {
  setReaderPage(currentPage.value + 1);
}

function scrollReaderToTop() {
  void nextTick(() => {
    if (readingFrame.value) readingFrame.value.scrollTop = 0;
  });
}

async function extractKeywordResults() {
  if (!activeJob.value || activeJob.value.state !== "done") {
    keywordStatus.value = "请先选择已完成的 OCR 任务。";
    return;
  }
  if (!keywordInput.value.trim()) {
    keywordStatus.value = "请先输入至少一个关键词。";
    return;
  }
  isExtracting.value = true;
  keywordStatus.value = "正在提取关键词段落...";
  try {
    const currentJob = activeJob.value;
    const extractionOptions = {
      keywords: keywordInput.value,
      matchMode: keywordMode.value,
      contextBefore: keywordContextBefore.value,
      contextAfter: keywordContextAfter.value,
      granularity: keywordGranularity.value,
      useRegex: keywordUseRegex.value,
      caseSensitive: keywordCaseSensitive.value,
      normalizeChinese: keywordNormalizeChinese.value,
      deduplicate: keywordDeduplicate.value
    };
    const data = isLibraryJob(currentJob) && currentJob?.document_id
      ? await extractDocumentKeywords(
          currentJob.document_id,
          currentJob.account_id || accountId.value,
          currentJob.knowledge_base_id || knowledgeBaseId.value,
          extractionOptions
        )
      : await extractKeywords(currentJob?.job_id || "", extractionOptions);
    keywordResults.value = data.results || [];
    saveOriginalReaderState();
    pageItems.value = buildKeywordPageItems(data);
    currentPage.value = 1;
    pageInput.value = 1;
    displayMarkdown.value = stripPageHeadings(data.markdown || buildKeywordMarkdown(data));
    readerTitle.value = "关键词提取结果";
    isKeywordResultView.value = true;
    showRaw.value = false;
    keywordStatus.value = `命中 ${data.count || 0} 条结果；匹配模式：${data.match_mode}。`;
    activeView.value = "reader";
    await nextTick();
    scrollReaderToTop();
    document.querySelector(".reader")?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    keywordResults.value = [];
    keywordStatus.value = `提取失败：${getErrorMessage(error)}`;
  } finally {
    isExtracting.value = false;
  }
}

function buildKeywordPageItems(data: KeywordExtractionResponse): ReaderPageItem[] {
  const results = data.results || [];
  if (!results.length) {
    return [
      {
        pageNumber: 1,
        markdown: "没有命中结果。",
        keywordLabel: "",
        matchedKeywords: [],
        sourcePageLabel: ""
      }
    ];
  }
  return results.map((item, index) => ({
    pageNumber: index + 1,
    markdown: stripPageHeadings(item.text || ""),
    keywordLabel: (item.matched_keywords || []).join("、") || "无",
    matchedKeywords: item.matched_keywords || [],
    sourcePageLabel: pageLabel(item),
    sourcePageNumber: item.page_start
  }));
}

function highlightKeywordsInHtml(html: string, keywords: string[]) {
  const terms = uniqueKeywords(keywords);
  if (!terms.length || typeof document === "undefined") return html;

  const template = document.createElement("template");
  template.innerHTML = html;
  highlightTextNodes(template.content, terms);
  return template.innerHTML;
}

function highlightTextNodes(root: ParentNode, terms: string[]) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || parent.closest("mark, code, pre, script, style")) return NodeFilter.FILTER_REJECT;
      return node.textContent && findKeywordRanges(node.textContent, terms).length
        ? NodeFilter.FILTER_ACCEPT
        : NodeFilter.FILTER_REJECT;
    }
  });
  const nodes: Text[] = [];
  let node = walker.nextNode();
  while (node) {
    nodes.push(node as Text);
    node = walker.nextNode();
  }

  for (const textNode of nodes) {
    textNode.replaceWith(...highlightTextParts(textNode.textContent || "", terms));
  }
}

function highlightTextParts(text: string, terms: string[]) {
  const parts: Array<Text | HTMLElement> = [];
  const ranges = findKeywordRanges(text, terms);
  let cursor = 0;

  for (const range of ranges) {
    if (range.start > cursor) parts.push(document.createTextNode(text.slice(cursor, range.start)));

    const mark = document.createElement("mark");
    mark.className = "keyword-hit";
    mark.textContent = text.slice(range.start, range.end);
    parts.push(mark);
    cursor = range.end;
  }

  if (cursor < text.length) parts.push(document.createTextNode(text.slice(cursor)));
  return parts.length ? parts : [document.createTextNode(text)];
}

function findKeywordRanges(text: string, terms: string[]) {
  const haystack = keywordCaseSensitive.value ? text : text.toLowerCase();
  const normalizedTerms = keywordCaseSensitive.value ? terms : terms.map((item) => item.toLowerCase());
  const ranges: Array<{ start: number; end: number }> = [];
  let index = 0;

  while (index < text.length) {
    const term = normalizedTerms.find((item) => item && haystack.startsWith(item, index));
    if (term) {
      ranges.push({ start: index, end: index + term.length });
      index += term.length;
    } else {
      index += 1;
    }
  }

  return ranges;
}

function uniqueKeywords(keywords: string[]) {
  return Array.from(new Set(keywords.map((item) => item.trim()).filter(Boolean))).sort((a, b) => b.length - a.length);
}

function saveOriginalReaderState() {
  if (isKeywordResultView.value || originalReaderState.value) return;
  originalReaderState.value = {
    markdown: markdown.value,
    displayMarkdown: displayMarkdown.value,
    pageItems: pageItems.value.map((item) => ({ ...item })),
    readerTitle: readerTitle.value
  };
}

async function restoreOriginalDocument() {
  const original = originalReaderState.value;
  const sourcePage = currentPageItem.value?.sourcePageNumber || 1;
  if (!original) return;

  markdown.value = original.markdown;
  displayMarkdown.value = original.displayMarkdown;
  pageItems.value = original.pageItems.map((item) => ({ ...item }));
  readerTitle.value = original.readerTitle || "OCR Markdown 阅读器";
  isKeywordResultView.value = false;
  showRaw.value = false;
  activeView.value = "reader";
  setReaderPage(sourcePage);
  await nextTick();
  document.querySelector(".reader")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function buildKeywordMarkdown(data: KeywordExtractionResponse) {
  const lines = ["# 关键词提取结果", "", `- 命中结果：${data.count || 0} 条`, ""];
  for (const item of data.results || []) {
    const page = item.page_start === item.page_end ? `第 ${item.page_start} 页` : `第 ${item.page_start}-${item.page_end} 页`;
    lines.push(`## ${item.result_id}`, "", `- 来源文件：${item.source_file}`, `- 页码：${page}`, "", item.text || "", "");
  }
  return lines.join("\n");
}

function pageLabel(item: KeywordResult) {
  if (item.page_start === item.page_end) return `第 ${item.page_start} 页`;
  return `第 ${item.page_start}-${item.page_end} 页`;
}

function isLibraryJob(job: WebJob | null | undefined) {
  return Boolean(job?.job_id?.startsWith("document:"));
}

function queueLabel(job: WebJob) {
  if (isLibraryJob(job)) return "文档";
  if (!job.queue_index || !job.queue_total || job.queue_total <= 1) return "任务";
  return `${job.queue_index}/${job.queue_total}`;
}

function jobStateLabel(job: WebJob) {
  if (isLibraryJob(job)) return "知识库";
  if (job.state === "done") return "已完成";
  if (job.state === "failed") return job.error || "失败";
  if (job.state === "running") return `${Math.round(job.progress || 0)}%`;
  return "排队中";
}

function fileKey(file: File) {
  return `${filePath(file)}:${file.size}:${file.lastModified}`;
}

function filePath(file: File) {
  return (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
}

function setProgress(percent: number, text?: string) {
  progress.value = Math.max(0, Math.min(100, Number(percent) || 0));
  if (text) statusText.value = text;
}

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
</script>

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

        <div v-if="selectedFiles.length && !activeJobs.length" class="job-list pending-list" aria-label="待处理文件">
          <div v-for="(item, index) in selectedFiles" :key="fileKey(item)" class="job-item pending">
            <span>{{ index + 1 }}/{{ selectedFiles.length }}</span>
            <small>{{ filePath(item) }} / {{ pendingFileDetail(item) }}</small>
            <button class="remove-file" type="button" :aria-label="`移除 ${filePath(item)}`" title="移除" @click="removeSelectedFile(index)">×</button>
          </div>
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

      <section v-else-if="activeView === 'reader'" class="reader">
      <div v-if="markdown" class="reader-content">
        <div class="reader-head">
          <div v-if="activeJobs.length" class="document-switcher reader-document-switcher" aria-label="当前文档">
            <label class="document-select-label" for="readerDocumentSelect">当前文档</label>
            <select id="readerDocumentSelect" class="document-select" :value="activeJobId || ''" @change="onDocumentChange">
              <option v-for="item in activeJobs" :key="item.job_id" :value="item.job_id">
                {{ documentOptionLabel(item) }}
              </option>
            </select>
            <span v-if="activeJob" class="document-state" :class="documentStateClass(activeJob)">
              {{ jobStateLabel(activeJob) }}
            </span>
          </div>
          <div v-if="isKeywordResultView && (currentKeywordLabel || currentSourcePageLabel)" class="reader-meta-pills">
            <button class="secondary reader-original-button" type="button" @click="restoreOriginalDocument">查看原文</button>
            <div v-if="currentKeywordLabel" class="reader-page-source">命中关键词：{{ currentKeywordLabel }}</div>
            <div v-if="currentSourcePageLabel" class="reader-page-source">原文页码：{{ currentSourcePageLabel }}</div>
          </div>
          <div class="reader-actions">
            <div class="zoom-control" aria-label="阅读器缩放">
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
              <button class="secondary" type="button" @click="resetZoom">100%</button>
            </div>
            <button class="secondary" type="button" @click="showRaw = !showRaw">{{ showRaw ? "预览模式" : "查看源码" }}</button>
            <a v-if="downloadUrl" class="download-link" :href="downloadUrl">下载 Markdown</a>
          </div>
        </div>
        <div ref="readingFrame" class="reading-frame" :style="readerZoomStyle">
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
      <div v-else class="empty-state">
        <div v-if="activeJobs.length" class="document-switcher reader-document-switcher" aria-label="当前文档">
          <label class="document-select-label" for="readerEmptyDocumentSelect">当前文档</label>
          <select id="readerEmptyDocumentSelect" class="document-select" :value="activeJobId || ''" @change="onDocumentChange">
            <option v-for="item in activeJobs" :key="item.job_id" :value="item.job_id">
              {{ documentOptionLabel(item) }}
            </option>
          </select>
          <span v-if="activeJob" class="document-state" :class="documentStateClass(activeJob)">
            {{ jobStateLabel(activeJob) }}
          </span>
        </div>
        <span class="empty-kicker">Markdown Reader</span>
        <h2>还没有可阅读的 Markdown。</h2>
        <p>先在“上传解析”页完成 OCR，处理成功后这里会显示分页阅读器和下载入口。</p>
        <button type="button" @click="switchView('upload')">去上传解析</button>
      </div>
      </section>

      <section v-else class="extractor">
      <div v-if="activeJobs.length" class="document-switcher" aria-label="当前文档">
        <label class="document-select-label" for="extractorDocumentSelect">当前文档</label>
        <select id="extractorDocumentSelect" class="document-select" :value="activeJobId || ''" @change="onDocumentChange">
          <option v-for="item in activeJobs" :key="item.job_id" :value="item.job_id">
            {{ documentOptionLabel(item) }}
          </option>
        </select>
        <span v-if="activeJob" class="document-state" :class="documentStateClass(activeJob)">
          {{ jobStateLabel(activeJob) }}
        </span>
      </div>
      <div v-if="markdown" class="extractor-grid">
        <div class="extractor-form">
          <h2>关键词段落提取</h2>
          <p class="hint">复用后端同一套关键词匹配算法。当前是包含匹配：段落里出现关键词即命中。</p>
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
        </div>
      </div>
      <div v-else class="empty-state">
        <span class="empty-kicker">关键词提取</span>
        <h2>请先生成 OCR Markdown。</h2>
        <p>关键词提取依赖当前 OCR 任务。完成解析后再进入这里配置关键词、匹配模式和上下文段落。</p>
        <button type="button" @click="switchView('upload')">去上传解析</button>
      </div>
      </section>
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import type { Component, CSSProperties } from "vue";
import { BookOpen, Search, Upload } from "@lucide/vue";
import DOMPurify from "dompurify";
import { marked } from "marked";
import {
  createJobs,
  extractKeywords,
  getJob,
  getMarkdown,
  getPages,
  listEngines,
  type EngineOption,
  type KeywordExtractionResponse,
  type KeywordResult,
  type WebJob
} from "./api/jobs";

interface ReaderPageItem {
  pageNumber: number;
  markdown: string;
  keywordLabel?: string;
  sourcePageLabel?: string;
  sourcePageNumber?: number;
}

interface OriginalReaderState {
  markdown: string;
  displayMarkdown: string;
  pageItems: ReaderPageItem[];
  readerTitle: string;
}

type ActiveView = "upload" | "reader" | "extractor";

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
const isKeywordResultView = ref(false);
const readerZoom = ref(100);
const zoomInput = ref(100);
const minReaderZoom = 50;
const maxReaderZoom = 200;
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

const fileInput = ref<HTMLInputElement | null>(null);
const folderInput = ref<HTMLInputElement | null>(null);
const readingFrame = ref<HTMLDivElement | null>(null);

const viewItems = computed<ViewItem[]>(() => [
  {
    value: "upload",
    label: "上传解析",
    icon: Upload
  },
  {
    value: "reader",
    label: "Markdown 阅读",
    icon: BookOpen
  },
  {
    value: "extractor",
    label: "关键词提取",
    icon: Search
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
const hasPageItems = computed(() => pageItems.value.length > 0);
const totalReaderPages = computed(() => pageItems.value.length || 1);
const currentPageItem = computed(() => pageItems.value[currentPage.value - 1] || null);
const currentDisplayMarkdown = computed(() => currentPageItem.value?.markdown || displayMarkdown.value);
const currentSourcePageLabel = computed(() => currentPageItem.value?.sourcePageLabel || "");
const currentKeywordLabel = computed(() => currentPageItem.value?.keywordLabel || "");
const renderedMarkdown = computed(() => {
  if (!currentDisplayMarkdown.value) return "";
  const html = marked.parse(currentDisplayMarkdown.value, { async: false }) as string;
  return DOMPurify.sanitize(html);
});

onMounted(() => {
  void loadEngineOptions();
});

onBeforeUnmount(() => {
  clearPollTimer();
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
}

function switchView(view: ActiveView) {
  activeView.value = view;
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

async function loadEngineOptions() {
  try {
    const data = await listEngines();
    engines.value = data.engines?.length ? data.engines : defaultEngines;
    engine.value = data.default_engine || engine.value;
  } catch (error) {
    console.warn("Failed to load engines", error);
  }
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
  logs.value = [];
  progress.value = 0;
  statusText.value = "等待上传";
  resetReader();
}

function removeSelectedFile(index: number) {
  if (isBusy.value) return;
  const removed = selectedFiles.value[index];
  selectedFiles.value = selectedFiles.value.filter((_, itemIndex) => itemIndex !== index);
  logs.value = [];
  statusText.value = selectedFiles.value.length
    ? `已移除 ${removed ? filePath(removed) : "文件"}，待处理 ${selectedFiles.value.length} 个`
    : "等待上传";
}

async function startUpload() {
  if (!selectedFiles.value.length || isBusy.value) return;
  activeView.value = "upload";
  isBusy.value = true;
  progress.value = 0;
  statusText.value = "正在上传 PDF";
  logs.value = [];
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
  if (pagesUrl) await loadPageItems(pagesUrl);
  currentPage.value = 1;
  pageInput.value = 1;
  readerTitle.value = "OCR Markdown 阅读器";
  showRaw.value = false;
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
    const data = await extractKeywords(activeJob.value.job_id, {
      keywords: keywordInput.value,
      matchMode: keywordMode.value,
      contextBefore: keywordContextBefore.value,
      contextAfter: keywordContextAfter.value,
      granularity: keywordGranularity.value,
      useRegex: keywordUseRegex.value,
      caseSensitive: keywordCaseSensitive.value,
      normalizeChinese: keywordNormalizeChinese.value,
      deduplicate: keywordDeduplicate.value
    });
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
        sourcePageLabel: ""
      }
    ];
  }
  return results.map((item, index) => ({
    pageNumber: index + 1,
    markdown: stripPageHeadings(item.text || ""),
    keywordLabel: (item.matched_keywords || []).join("、") || "无",
    sourcePageLabel: pageLabel(item),
    sourcePageNumber: item.page_start
  }));
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

function queueLabel(job: WebJob) {
  if (!job.queue_index || !job.queue_total || job.queue_total <= 1) return "任务";
  return `${job.queue_index}/${job.queue_total}`;
}

function jobStateLabel(job: WebJob) {
  if (job.state === "done") return "已完成";
  if (job.state === "failed") return job.error || "失败";
  if (job.state === "running") return `${Math.round(job.progress || 0)}%`;
  return "排队中";
}

function pendingFileDetail(file: File) {
  return `${(file.size / 1024 / 1024).toFixed(2)} MB`;
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

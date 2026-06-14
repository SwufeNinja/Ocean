<template>
  <main class="shell">
    <header class="workbench-head">
      <div class="brand-block">
        <span class="brand-mark">Ocean</span>
        <div>
          <h1>文档解析助手</h1>
          <p>PDF OCR、Markdown 阅读和关键词提取集中处理</p>
        </div>
      </div>
      <nav class="view-tabs" aria-label="工作台页面切换">
        <button
          v-for="item in viewItems"
          :key="item.value"
          class="view-tab"
          :class="{ active: activeView === item.value }"
          type="button"
          @click="switchView(item.value)"
        >
          <span>{{ item.label }}</span>
          <small>{{ item.description }}</small>
        </button>
      </nav>
    </header>

    <section v-if="activeView === 'upload'" class="hero">
      <div class="panel intro">
        <span class="eyebrow">Processing Flow</span>
        <h2>解析流程</h2>
        <p class="lede">
          选择 PDF 和解析引擎后开始处理。完成后自动进入阅读页，需要定位内容时再切到关键词提取。
        </p>
        <div class="flow-steps" aria-label="解析流程">
          <span>上传 PDF</span>
          <span>选择引擎</span>
          <span>阅读/提取</span>
        </div>
      </div>

      <form class="panel upload" @submit.prevent="startUpload">
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
        <input id="pdfFile" ref="fileInput" name="file" type="file" accept="application/pdf,.pdf" @change="onFileChange" />

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
          <button id="startBtn" type="submit" :disabled="!file || isBusy">{{ isBusy ? "处理中..." : "开始解析" }}</button>
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
      </form>
    </section>

    <section v-else-if="activeView === 'reader'" class="panel reader">
      <div v-if="markdown" class="reader-content">
        <div class="reader-head">
          <div class="reader-title-block">
            <h2>{{ readerTitle }}</h2>
            <div class="hint">{{ readerHint }}</div>
          </div>
          <div v-if="currentKeywordLabel || currentSourcePageLabel" class="reader-meta-pills">
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
        <span class="empty-kicker">Markdown Reader</span>
        <h2>还没有可阅读的 Markdown。</h2>
        <p>先在“上传解析”页完成 OCR，处理成功后这里会显示分页阅读器和下载入口。</p>
        <button type="button" @click="switchView('upload')">去上传解析</button>
      </div>
    </section>

    <section v-else class="panel extractor">
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
            <button type="button" :disabled="isExtracting || !activeJob" @click="extractKeywordResults">
              {{ isExtracting ? "提取中..." : "提取关键词" }}
            </button>
          </div>
          <div class="extract-status">{{ keywordStatus }}</div>
        </div>

        <div class="extractor-note">
          <span class="empty-kicker">Extraction</span>
          <h3>结果会进入阅读页</h3>
          <p class="hint">提取完成后，页面会自动切到“Markdown 阅读”，逐条展示命中内容；原 OCR Markdown 不再占用阅读区。</p>
        </div>
      </div>
      <div v-else class="empty-state">
        <span class="empty-kicker">Keyword Extractor</span>
        <h2>请先生成 OCR Markdown。</h2>
        <p>关键词提取依赖当前 OCR 任务。完成解析后再进入这里配置关键词、匹配模式和上下文段落。</p>
        <button type="button" @click="switchView('upload')">去上传解析</button>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import type { CSSProperties } from "vue";
import DOMPurify from "dompurify";
import { marked } from "marked";
import {
  createJob,
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
}

type ActiveView = "upload" | "reader" | "extractor";

interface ViewItem {
  value: ActiveView;
  label: string;
  description: string;
}

const defaultEngines: EngineOption[] = [
  { value: "paddleocr", label: "PaddleOCR", description: "默认：适合快速文档 OCR" },
  { value: "mineru", label: "MinerU", description: "适合版面复杂的长文档解析" }
];

const activeView = ref<ActiveView>("upload");
const engines = ref<EngineOption[]>(defaultEngines);
const engine = ref("paddleocr");
const file = ref<File | null>(null);
const dragging = ref(false);
const isBusy = ref(false);
const activeJob = ref<WebJob | null>(null);
let pollTimer: number | undefined;

const progress = ref(0);
const statusText = ref("等待上传");
const logs = ref<string[]>([]);
const markdown = ref("");
const displayMarkdown = ref("");
const readerTitle = ref("Markdown 阅读器");
const downloadUrl = ref("");
const showRaw = ref(false);
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
const readingFrame = ref<HTMLDivElement | null>(null);

const viewItems = computed<ViewItem[]>(() => [
  {
    value: "upload",
    label: "上传解析",
    description: isBusy.value ? "处理中" : file.value ? "已选择文件" : "选择 PDF"
  },
  {
    value: "reader",
    label: "Markdown 阅读",
    description: markdown.value ? "可阅读" : "等待结果"
  },
  {
    value: "extractor",
    label: "关键词提取",
    description: markdown.value ? "可提取" : "等待 OCR"
  }
]);
const fileSummary = computed(() => {
  if (!file.value) return "还没有选择文件";
  return `${file.value.name} / ${(file.value.size / 1024 / 1024).toFixed(2)} MB`;
});
const roundedProgress = computed(() => Math.round(progress.value));
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
  pageItems.value = [];
  currentPage.value = 1;
  pageInput.value = 1;
}

function switchView(view: ActiveView) {
  activeView.value = view;
  if (view === "reader") scrollReaderToTop();
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
  setFile(input.files?.[0] || null);
}

function onDrop(event: DragEvent) {
  dragging.value = false;
  setFile(event.dataTransfer?.files?.[0] || null);
}

function setFile(selectedFile: File | null) {
  if (!selectedFile) return;
  activeView.value = "upload";
  file.value = selectedFile;
  resetReader();
  readerTitle.value = "Markdown 阅读器";
  downloadUrl.value = "";
  keywordResults.value = [];
  keywordStatus.value = "OCR 完成后可以在这里提取关键词段落。";
  logs.value = [];
  statusText.value = "已选择文件，等待开始解析";
  if (fileInput.value && fileInput.value.files?.[0] !== selectedFile && typeof DataTransfer !== "undefined") {
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(selectedFile);
    fileInput.value.files = dataTransfer.files;
  }
}

async function startUpload() {
  if (!file.value || isBusy.value) return;
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
    const job = await createJob(file.value, engine.value, (percent) => setProgress(percent, "正在上传 PDF"));
    activeJob.value = job;
    setProgress(job.progress || 8, job.message || "已提交 OCR 任务");
    void pollJob(job.job_id);
  } catch (error) {
    failUpload(getErrorMessage(error));
  }
}

function failUpload(message: string) {
  setProgress(100, "上传失败");
  logs.value = [message];
  isBusy.value = false;
}

async function pollJob(jobId: string) {
  clearPollTimer();
  try {
    const job = await getJob(jobId);
    activeJob.value = job;
    setProgress(job.progress, job.message);
    logs.value = job.log_tail || [];
    if (job.state === "done") {
      if (job.markdown_url) await loadMarkdown(job.markdown_url, job.pages_url);
      downloadUrl.value = job.download_url || "";
      isBusy.value = false;
      return;
    }
    if (job.state === "failed") {
      setProgress(100, job.error || "处理失败");
      isBusy.value = false;
      return;
    }
    pollTimer = window.setTimeout(() => void pollJob(jobId), 2000);
  } catch (error) {
    statusText.value = `轮询失败：${getErrorMessage(error)}`;
    pollTimer = window.setTimeout(() => void pollJob(jobId), 4000);
  }
}

async function loadMarkdown(url: string, pagesUrl?: string | null) {
  markdown.value = await getMarkdown(url);
  displayMarkdown.value = stripPageHeadings(markdown.value);
  pageItems.value = [];
  if (pagesUrl) await loadPageItems(pagesUrl);
  currentPage.value = 1;
  pageInput.value = 1;
  readerTitle.value = "OCR Markdown 阅读器";
  showRaw.value = false;
  activeView.value = "reader";
  await nextTick();
  scrollReaderToTop();
  document.querySelector(".reader")?.scrollIntoView({ behavior: "smooth", block: "start" });
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
  if (!activeJob.value || !keywordInput.value.trim()) {
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
    pageItems.value = buildKeywordPageItems(data);
    currentPage.value = 1;
    pageInput.value = 1;
    displayMarkdown.value = stripPageHeadings(data.markdown || buildKeywordMarkdown(data));
    readerTitle.value = "关键词提取结果";
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
    sourcePageLabel: pageLabel(item)
  }));
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

function setProgress(percent: number, text?: string) {
  progress.value = Math.max(0, Math.min(100, Number(percent) || 0));
  if (text) statusText.value = text;
}

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
</script>

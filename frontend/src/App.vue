<template>
  <main v-if="authStatus === 'checking'" class="auth-page">
    <div class="auth-panel">
      <h1>OCR Assistant</h1>
      <p>正在检查登录状态...</p>
    </div>
  </main>

  <main v-else-if="authStatus === 'login'" class="auth-page">
    <form class="auth-panel" @submit.prevent="submitLogin">
      <h1>OCR Assistant</h1>
      <label>
        <span>用户名</span>
        <input v-model="loginUsername" type="text" autocomplete="username" :disabled="isLoggingIn" />
      </label>
      <label>
        <span>密码</span>
        <input v-model="loginPassword" type="password" autocomplete="current-password" :disabled="isLoggingIn" />
      </label>
      <div v-if="loginError" class="auth-error">{{ loginError }}</div>
      <button type="submit" :disabled="isLoggingIn || !loginUsername.trim() || !loginPassword">
        {{ isLoggingIn ? "登录中..." : "登录" }}
      </button>
    </form>
  </main>

  <main v-else class="shell">
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
      <button
        class="view-tab view-tab-bottom"
        type="button"
        aria-label="设置"
        title="设置"
        @click="toggleSettings"
      >
        <Settings :size="20" :stroke-width="1.9" aria-hidden="true" />
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
              <span class="engine-card-title">
                <b>{{ item.label }}</b>
                <small v-if="item.value === defaultEngine">默认</small>
              </span>
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

        <div v-if="visibleUploadJobs.length" class="job-list" aria-label="OCR 任务队列">
          <button
            v-for="item in visibleUploadJobs"
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

      <section v-else-if="activeView === 'chat'" class="chat-page" :class="{ 'is-history-collapsed': isChatHistoryCollapsed }">
        <aside class="chat-history-panel" :class="{ collapsed: isChatHistoryCollapsed }" aria-label="对话记录">
          <div class="chat-panel-head">
            <h2 v-if="!isChatHistoryCollapsed">对话记录</h2>
            <div class="chat-panel-actions">
              <button
                v-if="!isChatHistoryCollapsed"
                class="secondary icon-text-button"
                type="button"
                :disabled="isGlobalLlmHistoryLoading"
                @click="loadGlobalLlmConversations"
              >
                <RefreshCw :size="15" :stroke-width="2" aria-hidden="true" />
                <span>刷新</span>
              </button>
              <button
                class="llm-icon-button"
                type="button"
                :aria-label="isChatHistoryCollapsed ? '展开对话记录' : '折叠对话记录'"
                :title="isChatHistoryCollapsed ? '展开对话记录' : '折叠对话记录'"
                @click="isChatHistoryCollapsed = !isChatHistoryCollapsed"
              >
                <PanelLeftClose v-if="!isChatHistoryCollapsed" :size="17" :stroke-width="2" aria-hidden="true" />
                <PanelLeftOpen v-else :size="17" :stroke-width="2" aria-hidden="true" />
              </button>
            </div>
          </div>
          <template v-if="!isChatHistoryCollapsed">
            <button
              class="chat-history-item"
              :class="{ active: !globalLlmConversation }"
              type="button"
              @click="startNewGlobalConversation"
            >
              <strong>新对话</strong>
            </button>
            <button
              v-for="conversation in globalLlmConversations"
              :key="conversation.conversation_id"
              class="chat-history-item"
              :class="{ active: globalLlmConversation?.conversation_id === conversation.conversation_id }"
              type="button"
              @click="openGlobalLlmConversation(conversation.conversation_id)"
            >
              <strong>{{ conversation.title || "未命名对话" }}</strong>
              <small>{{ conversationMeta(conversation) }}</small>
            </button>
          </template>
        </aside>

        <div class="chat-main">
          <div class="chat-center" :class="{ 'is-empty': !globalLlmVisibleMessages.length }">
            <div v-if="!globalLlmVisibleMessages.length" class="chat-hero">
              <Bot :size="28" :stroke-width="2" aria-hidden="true" />
              <h2>开始对话</h2>
            </div>

            <div ref="globalLlmMessageList" class="chat-message-list" aria-live="polite">
              <div
                v-for="message in globalLlmVisibleMessages"
                :key="message.message_id"
                class="llm-message"
                :class="[`llm-message-${message.role}`, { pending: message.pending, error: message.error }]"
              >
                <div class="llm-message-role">
                  {{ message.role === "user" ? "你" : "助手" }}<span v-if="message.pending"> · 发送中</span><span v-if="message.error"> · 发送失败</span>
                </div>
                <div class="llm-message-content" v-html="renderMessageMarkdown(message.content)"></div>
              </div>
            </div>

            <form class="global-chat-compose" @submit.prevent="sendGlobalLlmMessage">
              <div v-if="globalLlmError" class="llm-error">{{ globalLlmError }}</div>
              <div ref="globalContextPickerRoot" class="llm-input-wrap global-chat-input-wrap">
                <div
                  v-if="isGlobalContextPickerOpen && canEditGlobalContextDocuments"
                  class="context-popover"
                  :class="{ 'is-above': globalLlmVisibleMessages.length }"
                >
                  <label class="context-search">
                    <Search :size="15" :stroke-width="2" aria-hidden="true" />
                    <input v-model="globalContextQuery" type="search" placeholder="搜索知识库文档" />
                    <button
                      class="context-search-close"
                      type="button"
                      aria-label="关闭文档搜索"
                      title="关闭文档搜索"
                      @click="isGlobalContextPickerOpen = false"
                    >
                      <X :size="15" :stroke-width="2" aria-hidden="true" />
                    </button>
                  </label>
                  <div class="context-doc-list">
                    <button
                      v-for="document in filteredGlobalContextDocuments"
                      :key="document.document_id"
                      class="context-doc-item"
                      :class="{ active: selectedGlobalContextDocumentIds.includes(document.document_id) }"
                      type="button"
                      @click="toggleGlobalContextDocument(document.document_id)"
                    >
                      <FileText :size="15" :stroke-width="2" aria-hidden="true" />
                      <span>{{ document.file_name }}</span>
                    </button>
                  </div>
                  <div class="context-popover-foot">
                    <span>{{ selectedGlobalContextDocumentIds.length }}/5</span>
                  </div>
                </div>
                <div v-if="globalSelectedContextDocuments.length" class="selected-context-docs" aria-label="已引用文档">
                  <span v-for="document in globalSelectedContextDocuments" :key="document.document_id">
                    <FileText :size="13" :stroke-width="2" aria-hidden="true" />
                    {{ document.file_name || document.title || document.document_id }}
                  </span>
                </div>
                <textarea
                  v-model="globalLlmInput"
                  class="llm-input global-chat-input"
                  :class="{
                    'has-context-docs': globalSelectedContextDocuments.length,
                    'without-context-picker': !canEditGlobalContextDocuments
                  }"
                  rows="3"
                  placeholder="向 LLM 提问..."
                  :disabled="isGlobalLlmSending || isGlobalLlmPreparing"
                  @keydown="onGlobalLlmInputKeydown"
                ></textarea>
                <button
                  v-if="canEditGlobalContextDocuments"
                  class="context-picker-button"
                  type="button"
                  aria-label="选择上下文文档"
                  title="选择上下文文档"
                  @click="toggleGlobalContextPicker"
                >
                  <Plus :size="18" :stroke-width="2.2" aria-hidden="true" />
                  <span v-if="selectedGlobalContextDocumentIds.length">{{ selectedGlobalContextDocumentIds.length }}</span>
                </button>
                <label
                  class="llm-web-search-toggle with-context-button"
                  :class="{ active: llmWebSearchEnabled, disabled: !canUseLlmWebSearch }"
                  title="联网搜索"
                  aria-label="联网搜索"
                >
                  <input v-model="llmWebSearchEnabled" type="checkbox" :disabled="!canUseLlmWebSearch || isGlobalLlmSending || isGlobalLlmPreparing" />
                  <Globe :size="15" :stroke-width="2" aria-hidden="true" />
                  <span>联网</span>
                </label>
                <button class="llm-send-button" type="submit" aria-label="发送" :disabled="!canSendGlobalLlmMessage">
                  <Send :size="17" :stroke-width="2.1" aria-hidden="true" />
                </button>
              </div>
            </form>
          </div>
        </div>
      </section>

      <section v-else-if="activeView === 'reader'" class="reader">
        <div v-if="markdown" class="reader-ready">
          <div class="reader-layout" :class="{ 'is-llm-panel-closed': !isLlmPanelOpen }" :style="readerLayoutStyle">
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
                  <button
                    v-if="!isLlmPanelOpen"
                    class="reader-llm-restore-button"
                    type="button"
                    aria-label="打开文档对话"
                    title="打开文档对话"
                    @click="openLlmPanel"
                  >
                    <Bot :size="19" :stroke-width="2" aria-hidden="true" />
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
                    <button v-if="downloadUrl" class="secondary reader-menu-action" type="button" @click="downloadCurrentMarkdown">下载 MD</button>
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
              v-if="isLlmPanelOpen"
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

            <aside v-if="isLlmPanelOpen" class="reader-side-panel" aria-label="LLM 对话">
              <div class="llm-panel-head">
                <div class="llm-title">
                  <Bot :size="18" :stroke-width="2" aria-hidden="true" />
                  <span>文档对话</span>
                </div>
                <div class="llm-head-actions">
                  <button
                    v-if="llmPanelMode === 'chat'"
                    class="llm-icon-button"
                    type="button"
                    aria-label="查看对话记录"
                    title="查看对话记录"
                    :disabled="isLlmHistoryLoading || !currentReaderDocumentId"
                    @click="showDocumentLlmHistory"
                  >
                    <Plus :size="17" :stroke-width="2" aria-hidden="true" />
                  </button>
                  <button
                    class="llm-icon-button"
                    type="button"
                    aria-label="关闭文档对话"
                    title="关闭文档对话"
                    @click="closeLlmPanel"
                  >
                    <X :size="17" :stroke-width="2" aria-hidden="true" />
                  </button>
                </div>
              </div>

              <div class="llm-context">
                <span class="llm-context-label">{{ llmStatusLabel }}</span>
                <strong>{{ activeJob?.file_name || readerTitle }}</strong>
              </div>

              <div v-if="llmPanelMode === 'history'" class="llm-history-list">
                <button
                  v-for="conversation in documentLlmConversations"
                  :key="conversation.conversation_id"
                  class="chat-history-item"
                  type="button"
                  @click="openDocumentLlmConversation(conversation.conversation_id)"
                >
                  <strong>{{ conversation.title || "未命名对话" }}</strong>
                  <small>{{ conversationMeta(conversation) }}</small>
                </button>
                <div v-if="!documentLlmConversations.length" class="llm-empty">
                  <p>{{ isLlmHistoryLoading ? "正在加载对话记录..." : "还没有使用当前文档作为上下文的对话。" }}</p>
                </div>
              </div>

              <div v-else ref="llmMessageList" class="llm-message-list" aria-live="polite">
                <div v-if="!llmVisibleMessages.length" class="llm-empty">
                  <Bot :size="22" :stroke-width="1.8" aria-hidden="true" />
                  <p>对这篇文章有什么疑问吗？</p>
                </div>
                <div
                  v-for="message in llmVisibleMessages"
                  :key="message.message_id"
                  class="llm-message"
                  :class="[`llm-message-${message.role}`, { pending: message.pending, error: message.error }]"
                >
                  <div class="llm-message-role">
                    {{ message.role === "user" ? "你" : "助手" }}<span v-if="message.pending"> · 发送中</span><span v-if="message.error"> · 发送失败</span>
                  </div>
                  <div class="llm-message-content" v-html="renderMessageMarkdown(message.content)"></div>
                </div>
              </div>

              <form class="llm-compose" @submit.prevent="sendCurrentLlmMessage">
                <div v-if="llmError" class="llm-error">{{ llmError }}</div>
                <div class="llm-input-wrap">
                  <textarea
                    v-model="llmInput"
                    class="llm-input"
                    rows="3"
                    :placeholder="llmPanelMode === 'history' ? '输入后将基于当前文档创建新对话...' : '询问当前文档...'"
                    :disabled="isLlmSending || isLlmPreparing || !markdown"
                    @keydown="onLlmInputKeydown"
                  ></textarea>
                  <label
                    class="llm-web-search-toggle"
                    :class="{ active: llmWebSearchEnabled, disabled: !canUseLlmWebSearch }"
                    title="联网搜索"
                    aria-label="联网搜索"
                  >
                    <input v-model="llmWebSearchEnabled" type="checkbox" :disabled="!canUseLlmWebSearch || isLlmSending || isLlmPreparing" />
                    <Globe :size="15" :stroke-width="2" aria-hidden="true" />
                    <span>联网</span>
                  </label>
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

    <div v-if="isSettingsOpen" class="settings-scrim" aria-hidden="true" @click="closeSettings"></div>
    <aside v-if="isSettingsOpen" class="settings-panel" aria-label="设置">
      <div class="settings-panel-head">
        <h2>设置</h2>
        <button class="settings-close" type="button" aria-label="关闭设置" title="关闭设置" @click="closeSettings">
          <X :size="17" :stroke-width="2" aria-hidden="true" />
        </button>
      </div>
      <div class="settings-user">
        <span>{{ currentUser?.display_name || currentUser?.username || "本地模式" }}</span>
        <small>{{ authEnabled ? currentUser?.account_id || accountId : "local 模式" }}</small>
      </div>
      <button class="settings-action" type="button" @click="handleLogout">
        <LogOut :size="17" :stroke-width="2" aria-hidden="true" />
        <span>退出登录</span>
      </button>
    </aside>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { Component, CSSProperties } from "vue";
import { BookOpen, Bot, Database, FileText, Globe, LogOut, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Plus, RefreshCw, Search, Send, Settings, Upload, X } from "@lucide/vue";
import DOMPurify from "dompurify";
import { marked } from "marked";
import {
  createJobs,
  downloadTextFile,
  extractDocumentKeywords,
  extractKeywords,
  createLlmConversation,
  getCurrentUser,
  getJob,
  getLlmConversation,
  getLlmStatus,
  getMarkdown,
  getPages,
  login as apiLogin,
  listLlmConversations,
  listJobs,
  listKnowledgeDocuments,
  listEngines,
  logout as apiLogout,
  streamLlmMessage,
  ApiError,
  type AuthUser,
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

interface PersistedReaderState {
  source: "document" | "job";
  documentId?: string;
  jobId?: string;
  accountId?: string;
  knowledgeBaseId?: string;
  fileName?: string;
  engine?: string;
  engineLabel?: string;
  markdownUrl?: string | null;
  pagesUrl?: string | null;
  downloadUrl?: string | null;
  totalPages?: number | null;
  page?: number;
  zoom?: number;
  leftWidth?: number;
  rightWidth?: number;
  llmPanelOpen?: boolean;
}

interface PersistedUploadJobsState {
  accountId: string;
  knowledgeBaseId: string;
  jobIds: string[];
  activeJobId?: string | null;
  savedAt: number;
}

type ActiveView = "upload" | "library" | "chat" | "reader";
type ReaderResizeTarget = "left" | "right";
type AuthStatus = "checking" | "login" | "ready";
type LlmPanelMode = "chat" | "history";

interface ViewItem {
  value: ActiveView;
  label: string;
  icon: Component;
}

const defaultEngines: EngineOption[] = [
  { value: "mineru", label: "MinerU", description: "默认：适合版面复杂的长文档解析" },
  { value: "paddleocr", label: "PaddleOCR", description: "适合快速文档 OCR" }
];
const readerStateStorageKey = "ocean.reader.state.v1";
const uploadJobsStorageKey = "ocean.upload.jobs.v1";

const authStatus = ref<AuthStatus>("checking");
const authEnabled = ref(false);
const currentUser = ref<AuthUser | null>(null);
const loginUsername = ref("");
const loginPassword = ref("");
const loginError = ref("");
const isLoggingIn = ref(false);
const activeView = ref<ActiveView>("upload");
const engines = ref<EngineOption[]>(defaultEngines);
const engine = ref("mineru");
const defaultEngine = ref("mineru");
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
const isSettingsOpen = ref(false);
const isKeywordResultView = ref(false);
const isLlmPanelOpen = ref(true);
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
const llmWebSearchEnabled = ref(false);
const llmConversation = ref<LlmConversation | null>(null);
const llmPanelMode = ref<LlmPanelMode>("chat");
const documentLlmConversations = ref<LlmConversation[]>([]);
const llmInput = ref("");
const llmError = ref("");
const isLlmPreparing = ref(false);
const isLlmSending = ref(false);
const isLlmHistoryLoading = ref(false);
const globalLlmConversation = ref<LlmConversation | null>(null);
const globalLlmConversations = ref<LlmConversation[]>([]);
const selectedGlobalContextDocumentIds = ref<string[]>([]);
const isChatHistoryCollapsed = ref(false);
const isGlobalContextPickerOpen = ref(false);
const globalContextQuery = ref("");
const globalLlmInput = ref("");
const globalLlmError = ref("");
const isGlobalLlmPreparing = ref(false);
const isGlobalLlmSending = ref(false);
const isGlobalLlmHistoryLoading = ref(false);

const fileInput = ref<HTMLInputElement | null>(null);
const folderInput = ref<HTMLInputElement | null>(null);
const readingFrame = ref<HTMLDivElement | null>(null);
const llmMessageList = ref<HTMLDivElement | null>(null);
const globalLlmMessageList = ref<HTMLDivElement | null>(null);
const globalContextPickerRoot = ref<HTMLDivElement | null>(null);
let didInitializeLlmWebSearchToggle = false;

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
    value: "chat",
    label: "AI 对话",
    icon: Bot
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
const visibleUploadJobs = computed(() => activeJobs.value.filter((item) => !isLibraryJob(item)));
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
const globalLlmVisibleMessages = computed<LlmMessage[]>(() =>
  (globalLlmConversation.value?.messages || []).filter((message) => message.role === "user" || message.role === "assistant")
);
const filteredGlobalContextDocuments = computed(() => {
  const query = globalContextQuery.value.trim().toLowerCase();
  return [...libraryDocuments.value]
    .sort((left, right) => left.file_name.localeCompare(right.file_name, "zh-CN"))
    .filter((document) => {
      if (!query) return true;
      return `${document.file_name} ${document.document_id}`.toLowerCase().includes(query);
    });
});
const globalSelectedContextDocuments = computed(() => {
  const documentsById = new Map<string, { document_id: string; file_name?: string; title?: string }>();
  for (const document of globalLlmConversation.value?.context_documents || []) {
    const documentId = String(document.document_id || "").trim();
    if (documentId) documentsById.set(documentId, document);
  }
  for (const document of libraryDocuments.value) {
    documentsById.set(document.document_id, document);
  }
  return selectedGlobalContextDocumentIds.value.map((documentId) => (
    documentsById.get(documentId) || { document_id: documentId, file_name: documentId }
  ));
});
const canEditGlobalContextDocuments = computed(() => !isReaderOriginConversation(globalLlmConversation.value));
const canUseLlmWebSearch = computed(() => Boolean(llmStatus.value?.web_search_enabled));
const llmStatusLabel = computed(() => {
  if (!llmStatus.value) return "正在检查 LLM";
  if (!llmStatus.value.configured) return "LLM 未配置";
  return llmStatus.value.model ? `LLM 已连接 · ${llmStatus.value.model}` : "LLM 已连接";
});
const currentReaderDocumentId = computed(() => activeJob.value?.document_id || activeLibraryDocumentId.value || "");
const canSendLlmMessage = computed(() =>
  Boolean(markdown.value && llmInput.value.trim() && !isLlmPreparing.value && !isLlmSending.value)
);
const canSendGlobalLlmMessage = computed(() =>
  Boolean(globalLlmInput.value.trim() && !isGlobalLlmPreparing.value && !isGlobalLlmSending.value)
);

onMounted(() => {
  document.addEventListener("pointerdown", onDocumentPointerDown);
  void initializeApp();
});

watch([currentPage, readerZoom, readerLeftWidth, readerRightWidth, isLlmPanelOpen], () => {
  persistCurrentReaderState();
});

onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", onDocumentPointerDown);
  clearPollTimer();
  stopReaderResize();
});

function onDocumentPointerDown(event: PointerEvent) {
  if (!isGlobalContextPickerOpen.value) return;
  const root = globalContextPickerRoot.value;
  if (root?.contains(event.target as Node)) return;
  isGlobalContextPickerOpen.value = false;
}

function clearPollTimer() {
  if (pollTimer !== undefined) {
    window.clearTimeout(pollTimer);
    pollTimer = undefined;
  }
}

function toggleSettings() {
  isSettingsOpen.value = !isSettingsOpen.value;
}

function closeSettings() {
  isSettingsOpen.value = false;
}

async function initializeApp() {
  authStatus.value = "checking";
  try {
    const data = await getCurrentUser();
    authEnabled.value = data.auth_enabled;
    applyAuthenticatedUser(data.user);
    authStatus.value = "ready";
    await loadInitialData();
  } catch (error) {
    authEnabled.value = true;
    currentUser.value = null;
    loginError.value = "";
    authStatus.value = "login";
  }
}

async function loadInitialData() {
  const initialView = readActiveViewFromUrl();
  await Promise.all([loadEngineOptions(), loadLibraryDocuments(), loadLlmStatus(), loadGlobalLlmConversations()]);
  if (initialView && initialView !== "reader") {
    activeView.value = initialView;
  } else {
    await restoreReaderFromPersistedState();
  }
  await restoreActiveUploadJobs();
  if (!window.location.hash) writeActiveViewToUrl(activeView.value);
}

function applyAuthenticatedUser(user: AuthUser) {
  currentUser.value = user;
  accountId.value = user.account_id || "local";
}

async function submitLogin() {
  if (isLoggingIn.value) return;
  isLoggingIn.value = true;
  loginError.value = "";
  try {
    const data = await apiLogin(loginUsername.value.trim(), loginPassword.value);
    authEnabled.value = data.auth_enabled ?? true;
    applyAuthenticatedUser(data.user);
    loginPassword.value = "";
    authStatus.value = "ready";
    await loadInitialData();
  } catch (error) {
    loginError.value = getErrorMessage(error) || "登录失败";
  } finally {
    isLoggingIn.value = false;
  }
}

async function handleLogout() {
  closeSettings();
  await apiLogout();
  currentUser.value = null;
  activeJobs.value = [];
  activeJobId.value = null;
  activeLibraryDocumentId.value = null;
  libraryDocuments.value = [];
  clearPersistedUploadJobs();
  clearPollTimer();
  resetReader();
  loginPassword.value = "";
  authStatus.value = "login";
}

async function restoreReaderFromPersistedState() {
  const state = readReaderStateFromUrl() || readReaderStateFromStorage();
  if (!state) return;
  applyReaderUiState(state);
  try {
    if (state.source === "document" && state.documentId) {
      await restoreDocumentReader(state);
      return;
    }
    if (state.source === "job" && state.jobId) {
      await restoreJobReader(state);
    }
  } catch (error) {
    console.warn("Failed to restore reader state", error);
    libraryStatus.value = `恢复上次文档失败：${getErrorMessage(error)}`;
  }
}

async function restoreDocumentReader(state: PersistedReaderState) {
  const scopedAccountId = state.accountId || accountId.value;
  const scopedKnowledgeBaseId = state.knowledgeBaseId || knowledgeBaseId.value;
  accountId.value = scopedAccountId;
  knowledgeBaseId.value = scopedKnowledgeBaseId;
  const query = new URLSearchParams({
    account_id: scopedAccountId,
    knowledge_base_id: scopedKnowledgeBaseId
  }).toString();
  const documentId = state.documentId || "";
  const job: WebJob = {
    job_id: `document:${documentId}`,
    account_id: scopedAccountId,
    knowledge_base_id: scopedKnowledgeBaseId,
    document_id: documentId,
    reused: true,
    file_name: state.fileName || documentId,
    engine: state.engine || "library",
    engine_label: state.engineLabel || state.engine || "知识库",
    state: "done",
    progress: 100,
    message: "来自知识库",
    total_pages: state.totalPages ?? null,
    markdown_url: state.markdownUrl || `/api/documents/${encodeURIComponent(documentId)}/markdown?${query}`,
    download_url: state.downloadUrl || `/api/documents/${encodeURIComponent(documentId)}/download?${query}`,
    pages_url: state.pagesUrl || `/api/documents/${encodeURIComponent(documentId)}/pages?${query}`,
    created_at: "",
    updated_at: ""
  };
  activeJobs.value = mergeJobsById([job, ...activeJobs.value.filter((item) => !isLibraryJob(item))]);
  activeJobId.value = job.job_id;
  activeLibraryDocumentId.value = documentId;
  keywordResults.value = [];
  keywordStatus.value = "OCR 完成后可以在这里提取关键词段落。";
  await loadJobResult(job, { openReader: true, page: state.page, skipPersist: true });
  persistCurrentReaderState();
}

async function restoreJobReader(state: PersistedReaderState) {
  if (!state.jobId) return;
  const job = await getJob(state.jobId);
  activeJobs.value = mergeJobsById([job, ...activeJobs.value.filter((existing) => !isLibraryJob(existing))]);
  activeJobId.value = job.job_id;
  activeLibraryDocumentId.value = null;
  await loadJobResult(job, { openReader: true, page: state.page, skipPersist: true });
  persistCurrentReaderState();
}

function applyReaderUiState(state: PersistedReaderState) {
  if (state.zoom) setZoom(state.zoom);
  if (state.leftWidth) readerLeftWidth.value = clampReaderPanelWidth(state.leftWidth, minReaderLeftWidth, maxReaderLeftWidth);
  if (state.rightWidth) readerRightWidth.value = clampReaderPanelWidth(state.rightWidth, minReaderRightWidth, maxReaderRightWidth);
  if (typeof state.llmPanelOpen === "boolean") isLlmPanelOpen.value = state.llmPanelOpen;
}

function persistCurrentReaderState() {
  const state = currentReaderState();
  if (!state) return;
  try {
    window.localStorage.setItem(readerStateStorageKey, JSON.stringify(state));
  } catch (error) {
    console.warn("Failed to save reader state", error);
  }
  if (activeView.value === "reader") writeReaderStateToUrl(state);
}

function currentReaderState(): PersistedReaderState | null {
  const job = activeJob.value;
  if (!job || !markdown.value) return null;
  const base = {
    accountId: job.account_id || accountId.value,
    knowledgeBaseId: job.knowledge_base_id || knowledgeBaseId.value,
    fileName: job.file_name,
    engine: job.engine,
    engineLabel: job.engine_label,
    markdownUrl: job.markdown_url || null,
    pagesUrl: job.pages_url || null,
    downloadUrl: job.download_url || null,
    totalPages: job.total_pages ?? null,
    page: currentPage.value,
    zoom: readerZoom.value,
    leftWidth: readerLeftWidth.value,
    rightWidth: readerRightWidth.value,
    llmPanelOpen: isLlmPanelOpen.value
  };
  if (isLibraryJob(job) && job.document_id) {
    return {
      ...base,
      source: "document",
      documentId: job.document_id
    };
  }
  return {
    ...base,
    source: "job",
    jobId: job.job_id,
    documentId: job.document_id || undefined
  };
}

function readReaderStateFromUrl(): PersistedReaderState | null {
  const hash = window.location.hash || "";
  if (!hash.startsWith("#/reader")) return null;
  const queryStart = hash.indexOf("?");
  const params = new URLSearchParams(queryStart >= 0 ? hash.slice(queryStart + 1) : "");
  const source = params.get("source");
  if (source !== "document" && source !== "job") return null;
  return {
    source,
    documentId: params.get("document_id") || undefined,
    jobId: params.get("job_id") || undefined,
    accountId: params.get("account_id") || undefined,
    knowledgeBaseId: params.get("knowledge_base_id") || undefined,
    fileName: params.get("file_name") || undefined,
    page: numberParam(params, "page"),
    zoom: numberParam(params, "zoom"),
    leftWidth: numberParam(params, "left_width"),
    rightWidth: numberParam(params, "right_width"),
    llmPanelOpen: boolParam(params, "llm_panel")
  };
}

function readActiveViewFromUrl(): ActiveView | null {
  const route = (window.location.hash || "").split("?")[0];
  if (route === "#/upload") return "upload";
  if (route === "#/library") return "library";
  if (route === "#/chat") return "chat";
  if (route === "#/reader") return "reader";
  return null;
}

function readReaderStateFromStorage(): PersistedReaderState | null {
  try {
    const raw = window.localStorage.getItem(readerStateStorageKey);
    if (!raw) return null;
    const data = JSON.parse(raw) as PersistedReaderState;
    return data?.source === "document" || data?.source === "job" ? data : null;
  } catch {
    return null;
  }
}

function writeReaderStateToUrl(state: PersistedReaderState) {
  const params = new URLSearchParams();
  params.set("source", state.source);
  if (state.documentId) params.set("document_id", state.documentId);
  if (state.jobId) params.set("job_id", state.jobId);
  if (state.accountId) params.set("account_id", state.accountId);
  if (state.knowledgeBaseId) params.set("knowledge_base_id", state.knowledgeBaseId);
  if (state.fileName) params.set("file_name", state.fileName);
  params.set("page", String(state.page || 1));
  params.set("zoom", String(state.zoom || 100));
  params.set("left_width", String(state.leftWidth || readerLeftWidth.value));
  params.set("right_width", String(state.rightWidth || readerRightWidth.value));
  params.set("llm_panel", state.llmPanelOpen === false ? "0" : "1");
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#/reader?${params.toString()}`);
}

function writeActiveViewToUrl(view: ActiveView) {
  if (view === "reader") {
    const state = currentReaderState();
    if (state) {
      writeReaderStateToUrl(state);
      return;
    }
  }
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#/${view}`);
}

function clearPersistedReaderState() {
  try {
    window.localStorage.removeItem(readerStateStorageKey);
  } catch {
    // Ignore storage cleanup failures.
  }
  if (window.location.hash.startsWith("#/reader")) {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#/upload`);
  }
}

async function restoreActiveUploadJobs() {
  const persisted = readUploadJobsState();
  const persistedJobs = await loadPersistedUploadJobs(persisted);
  const serverJobs = await loadServerActiveJobs();
  const uploadJobs = mergeJobsById([...persistedJobs, ...serverJobs])
    .filter((job) => !isLibraryJob(job) && isUnfinishedJob(job));
  if (!uploadJobs.length) {
    clearPersistedUploadJobs();
    return;
  }

  const libraryJobs = activeJobs.value.filter((job) => isLibraryJob(job));
  activeJobs.value = mergeJobsById([...libraryJobs, ...uploadJobs]);
  const activeStillExists = activeJobId.value && activeJobs.value.some((job) => job.job_id === activeJobId.value);
  if (!activeStillExists) {
    activeJobId.value = uploadJobs.find((job) => job.job_id === persisted?.activeJobId)?.job_id || uploadJobs[0].job_id;
  }
  isBusy.value = true;
  if (activeView.value !== "reader" && !readActiveViewFromUrl()) activeView.value = "upload";
  updateProgressFromJobs(uploadJobs);
  persistActiveUploadJobs();
  void pollJobs();
}

async function loadPersistedUploadJobs(state: PersistedUploadJobsState | null): Promise<WebJob[]> {
  if (!state?.jobIds?.length) return [];
  const results = await Promise.all(
    state.jobIds.map(async (jobId) => {
      try {
        return await getJob(jobId);
      } catch {
        return null;
      }
    })
  );
  return results.filter((job): job is WebJob => Boolean(job));
}

async function loadServerActiveJobs(): Promise<WebJob[]> {
  try {
    const data = await listJobs({
      accountId: accountId.value,
      knowledgeBaseId: knowledgeBaseId.value,
      states: ["queued", "running"],
      limit: 100
    });
    return data.jobs || [];
  } catch (error) {
    console.warn("Failed to restore active OCR jobs", error);
    return [];
  }
}

function persistActiveUploadJobs() {
  const uploadJobs = activeJobs.value.filter((job) => !isLibraryJob(job) && isUnfinishedJob(job));
  if (!uploadJobs.length) {
    clearPersistedUploadJobs();
    return;
  }
  const state: PersistedUploadJobsState = {
    accountId: accountId.value,
    knowledgeBaseId: knowledgeBaseId.value,
    jobIds: uploadJobs.map((job) => job.job_id),
    activeJobId: activeJobId.value && uploadJobs.some((job) => job.job_id === activeJobId.value) ? activeJobId.value : uploadJobs[0].job_id,
    savedAt: Date.now()
  };
  try {
    window.localStorage.setItem(uploadJobsStorageKey, JSON.stringify(state));
  } catch (error) {
    console.warn("Failed to save active OCR jobs", error);
  }
}

function readUploadJobsState(): PersistedUploadJobsState | null {
  try {
    const raw = window.localStorage.getItem(uploadJobsStorageKey);
    if (!raw) return null;
    const data = JSON.parse(raw) as PersistedUploadJobsState;
    if (!Array.isArray(data.jobIds) || !data.jobIds.length) return null;
    if (data.accountId && data.accountId !== accountId.value) return null;
    if (data.knowledgeBaseId && data.knowledgeBaseId !== knowledgeBaseId.value) return null;
    return data;
  } catch {
    return null;
  }
}

function clearPersistedUploadJobs() {
  try {
    window.localStorage.removeItem(uploadJobsStorageKey);
  } catch {
    // Ignore storage cleanup failures.
  }
}

function mergeJobsById(jobs: WebJob[]) {
  const merged = new Map<string, WebJob>();
  for (const job of jobs) {
    if (job?.job_id) merged.set(job.job_id, job);
  }
  return Array.from(merged.values());
}

function isUnfinishedJob(job: WebJob) {
  return job.state !== "done" && job.state !== "failed";
}

function updateProgressFromJobs(jobs: WebJob[]) {
  if (!jobs.length) return;
  const totalProgress = jobs.reduce((sum, item) => sum + (Number(item.progress) || 0), 0) / jobs.length;
  const finished = jobs.filter((item) => item.state === "done" || item.state === "failed").length;
  const running = jobs.find((item) => item.state === "running");
  const queued = jobs.find((item) => item.state === "queued");
  const current = running || queued || jobs[0];
  const prefix = jobs.length > 1 ? `队列 ${finished}/${jobs.length}` : "";
  const message = current?.message || "正在恢复 OCR 任务";
  setProgress(totalProgress, prefix ? `${prefix}，${message}` : message);
}

function numberParam(params: URLSearchParams, key: string): number | undefined {
  const value = Number(params.get(key));
  return Number.isFinite(value) && value > 0 ? value : undefined;
}

function boolParam(params: URLSearchParams, key: string): boolean | undefined {
  const value = params.get(key);
  if (value === "1" || value === "true") return true;
  if (value === "0" || value === "false") return false;
  return undefined;
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
  llmPanelMode.value = "chat";
  documentLlmConversations.value = [];
  llmInput.value = "";
  llmError.value = "";
  clearPersistedReaderState();
}

function switchView(view: ActiveView) {
  activeView.value = view;
  writeActiveViewToUrl(view);
  closeReaderMenu();
  closeSettings();
  if (view === "library") void loadLibraryDocuments();
  if (view === "chat") {
    void loadLibraryDocuments();
    void loadGlobalLlmConversations();
  }
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

function openLlmPanel() {
  isLlmPanelOpen.value = true;
  llmPanelMode.value = "chat";
  void scrollLlmMessagesToBottom();
}

function closeLlmPanel() {
  isLlmPanelOpen.value = false;
  closeReaderMenu();
  if (activeReaderResizeTarget.value === "right") stopReaderResize();
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
    defaultEngine.value = data.default_engine || defaultEngine.value;
    engine.value = data.default_engine || engine.value;
  } catch (error) {
    console.warn("Failed to load engines", error);
  }
}

async function loadLlmStatus() {
  try {
    llmStatus.value = await getLlmStatus();
    if (!llmStatus.value.web_search_enabled) {
      llmWebSearchEnabled.value = false;
    } else if (!didInitializeLlmWebSearchToggle) {
      llmWebSearchEnabled.value = true;
    }
    didInitializeLlmWebSearchToggle = true;
    llmError.value = llmStatus.value.configured ? "" : "请先在后端配置 LLM_API_BASE_URL、LLM_API_KEY 和 LLM_MODEL。";
  } catch (error) {
    llmStatus.value = null;
    llmWebSearchEnabled.value = false;
    llmError.value = `LLM 状态检查失败：${getErrorMessage(error)}`;
  }
}

async function startLlmConversationForCurrentDocument(initialTitle = "") {
  const documentId = currentReaderDocumentId.value;
  if (!markdown.value || !documentId || isLlmPreparing.value) return;
  isLlmPreparing.value = true;
  llmError.value = "";
  try {
    llmConversation.value = await createLlmConversation({
      accountId: activeJob.value?.account_id || accountId.value,
      knowledgeBaseId: activeJob.value?.knowledge_base_id || knowledgeBaseId.value,
      title: initialTitle || activeJob.value?.file_name || readerTitle.value,
      origin: "reader",
      systemPrompt: buildLlmSystemPrompt(),
      contextDocuments: [{ document_id: documentId }]
    });
    llmPanelMode.value = "chat";
    void loadDocumentLlmConversations();
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
  return [
    "你是一个严谨的文档阅读助手。",
    "优先依据提供的文档内容回答；如果文档内容不足以支持结论，要明确说明不确定。",
    "回答应简洁、结构清晰，可以引用页码、表格或原文要点。",
    `当前文档：${source}。${pages}`
  ].join("\n");
}

async function sendCurrentLlmMessage() {
  const content = llmInput.value.trim();
  if (!content || isLlmSending.value || isLlmPreparing.value) return;
  llmError.value = "";
  if (!llmConversation.value) {
    await startLlmConversationForCurrentDocument(conversationTitleFromPrompt(content));
  }
  if (!llmConversation.value) return;
  llmPanelMode.value = "chat";

  isLlmSending.value = true;
  llmInput.value = "";
  const localUserMessage = createLocalLlmMessage("user", content, { pending: true });
  const localAssistantMessage = createLocalLlmMessage("assistant", "正在思考...", { pending: true });
  llmConversation.value = {
    ...llmConversation.value,
    title: isDefaultConversationTitle(llmConversation.value.title) ? conversationTitleFromPrompt(content) : llmConversation.value.title,
    messages: [...(llmConversation.value.messages || []), localUserMessage, localAssistantMessage],
    message_count: (llmConversation.value.messages || []).length + 2
  };
  upsertDocumentLlmConversation(llmConversation.value);
  await scrollLlmMessagesToBottom();

  try {
    const data = await streamLlmMessage(llmConversation.value.conversation_id, content, {
      onDelta: (delta) => {
        appendLocalLlmMessageContent("document", localAssistantMessage.message_id, delta);
        void scrollLlmMessagesToBottom();
      }
    }, {
      webSearchEnabled: llmWebSearchEnabled.value
    });
    const mergedConversation = mergeServerConversation(data.conversation, llmConversation.value);
    llmConversation.value = mergedConversation;
    upsertDocumentLlmConversation(mergedConversation);
    await scrollLlmMessagesToBottom();
  } catch (error) {
    markLocalLlmMessage(localUserMessage.message_id, { pending: false, error: true });
    markLocalLlmMessage(localAssistantMessage.message_id, { pending: false, error: true });
    llmInput.value = content;
    llmError.value = `发送失败：${getErrorMessage(error)}`;
    void loadLlmStatus();
  } finally {
    isLlmSending.value = false;
    await scrollLlmMessagesToBottom();
  }
}

async function showDocumentLlmHistory() {
  llmPanelMode.value = "history";
  await loadDocumentLlmConversations();
}

async function loadDocumentLlmConversations() {
  const documentId = currentReaderDocumentId.value;
  if (!documentId) {
    documentLlmConversations.value = [];
    return;
  }
  isLlmHistoryLoading.value = true;
  try {
    const data = await listLlmConversations({
      accountId: activeJob.value?.account_id || accountId.value,
      knowledgeBaseId: activeJob.value?.knowledge_base_id || knowledgeBaseId.value,
      documentId,
      limit: 100
    });
    documentLlmConversations.value = (data.conversations || []).filter(hasConversationMessages);
  } catch (error) {
    llmError.value = `加载对话记录失败：${getErrorMessage(error)}`;
  } finally {
    isLlmHistoryLoading.value = false;
  }
}

async function openDocumentLlmConversation(conversationId: string) {
  llmError.value = "";
  try {
    llmConversation.value = await getLlmConversation(conversationId);
    llmPanelMode.value = "chat";
    await scrollLlmMessagesToBottom();
  } catch (error) {
    llmError.value = `打开对话失败：${getErrorMessage(error)}`;
  }
}

async function loadGlobalLlmConversations() {
  isGlobalLlmHistoryLoading.value = true;
  try {
    const data = await listLlmConversations({
      accountId: accountId.value,
      knowledgeBaseId: knowledgeBaseId.value,
      limit: 100
    });
    globalLlmConversations.value = (data.conversations || []).filter(hasConversationMessages);
  } catch (error) {
    globalLlmError.value = `加载对话记录失败：${getErrorMessage(error)}`;
  } finally {
    isGlobalLlmHistoryLoading.value = false;
  }
}

function startNewGlobalConversation() {
  globalLlmConversation.value = null;
  globalLlmInput.value = "";
  globalLlmError.value = "";
  selectedGlobalContextDocumentIds.value = [];
  globalContextQuery.value = "";
  isGlobalContextPickerOpen.value = false;
}

function toggleGlobalContextPicker() {
  if (!canEditGlobalContextDocuments.value) {
    isGlobalContextPickerOpen.value = false;
    return;
  }
  isGlobalContextPickerOpen.value = !isGlobalContextPickerOpen.value;
  if (isGlobalContextPickerOpen.value) void loadLibraryDocuments();
}

function toggleGlobalContextDocument(documentId: string) {
  if (!canEditGlobalContextDocuments.value) return;
  const selected = selectedGlobalContextDocumentIds.value;
  if (selected.includes(documentId)) {
    selectedGlobalContextDocumentIds.value = selected.filter((item) => item !== documentId);
    return;
  }
  if (selected.length >= 5) {
    globalLlmError.value = "最多只能选择 5 篇上下文文档。";
    return;
  }
  selectedGlobalContextDocumentIds.value = [...selected, documentId];
  globalLlmError.value = "";
}

async function openGlobalLlmConversation(conversationId: string) {
  globalLlmError.value = "";
  try {
    globalLlmConversation.value = await getLlmConversation(conversationId);
    selectedGlobalContextDocumentIds.value = globalLlmConversation.value.context_document_ids || [];
    isGlobalContextPickerOpen.value = false;
    await scrollGlobalLlmMessagesToBottom();
  } catch (error) {
    globalLlmError.value = `打开对话失败：${getErrorMessage(error)}`;
  }
}

async function ensureGlobalLlmConversation(firstMessage = "") {
  if (globalLlmConversation.value) return true;
  isGlobalLlmPreparing.value = true;
  globalLlmError.value = "";
  try {
    globalLlmConversation.value = await createLlmConversation({
      accountId: accountId.value,
      knowledgeBaseId: knowledgeBaseId.value,
      title: conversationTitleFromPrompt(firstMessage),
      origin: "global",
      systemPrompt: "你是一个严谨的知识库问答助手。回答应简洁、结构清晰；如果上下文不足，要明确说明不确定。",
      contextDocuments: selectedGlobalContextDocumentIds.value.map((documentId) => ({ document_id: documentId }))
    });
    return true;
  } catch (error) {
    globalLlmConversation.value = null;
    globalLlmError.value = `创建对话失败：${getErrorMessage(error)}`;
    return false;
  } finally {
    isGlobalLlmPreparing.value = false;
  }
}

async function sendGlobalLlmMessage() {
  const content = globalLlmInput.value.trim();
  if (!content || isGlobalLlmSending.value || isGlobalLlmPreparing.value) return;
  if (!(await ensureGlobalLlmConversation(content)) || !globalLlmConversation.value) return;

  isGlobalLlmSending.value = true;
  isGlobalContextPickerOpen.value = false;
  globalLlmInput.value = "";
  const localUserMessage = createLocalLlmMessage("user", content, { pending: true });
  const localAssistantMessage = createLocalLlmMessage("assistant", "正在思考...", { pending: true });
  globalLlmConversation.value = {
    ...globalLlmConversation.value,
    title: isDefaultConversationTitle(globalLlmConversation.value.title)
      ? conversationTitleFromPrompt(content)
      : globalLlmConversation.value.title,
    messages: [...(globalLlmConversation.value.messages || []), localUserMessage, localAssistantMessage],
    message_count: (globalLlmConversation.value.messages || []).length + 2
  };
  upsertGlobalLlmConversation(globalLlmConversation.value);
  await scrollGlobalLlmMessagesToBottom();
  try {
    const data = await streamLlmMessage(globalLlmConversation.value.conversation_id, content, {
      onDelta: (delta) => {
        appendLocalLlmMessageContent("global", localAssistantMessage.message_id, delta);
        void scrollGlobalLlmMessagesToBottom();
      }
    }, {
      webSearchEnabled: llmWebSearchEnabled.value,
      contextDocuments: canEditGlobalContextDocuments.value
        ? selectedGlobalContextDocumentIds.value.map((documentId) => ({ document_id: documentId }))
        : undefined
    });
    const mergedConversation = mergeServerConversation(data.conversation, globalLlmConversation.value);
    globalLlmConversation.value = mergedConversation;
    selectedGlobalContextDocumentIds.value = mergedConversation.context_document_ids || selectedGlobalContextDocumentIds.value;
    upsertGlobalLlmConversation(mergedConversation);
    await scrollGlobalLlmMessagesToBottom();
  } catch (error) {
    globalLlmConversation.value = {
      ...globalLlmConversation.value,
      messages: (globalLlmConversation.value.messages || []).map((message) =>
        message.message_id === localUserMessage.message_id || message.message_id === localAssistantMessage.message_id
          ? { ...message, pending: false, error: true }
          : message
      )
    };
    globalLlmInput.value = content;
    globalLlmError.value = `发送失败：${getErrorMessage(error)}`;
  } finally {
    isGlobalLlmSending.value = false;
    await scrollGlobalLlmMessagesToBottom();
  }
}

function onGlobalLlmInputKeydown(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  void sendGlobalLlmMessage();
}

async function scrollGlobalLlmMessagesToBottom() {
  await nextTick();
  const element = globalLlmMessageList.value;
  if (element) element.scrollTop = element.scrollHeight;
}

function conversationMeta(conversation: LlmConversation) {
  const documentCount = conversation.context_document_ids?.length || conversation.context_documents?.length || 0;
  const context = documentCount ? `${documentCount} 篇上下文` : "通用对话";
  const count = `${conversation.message_count || conversation.messages?.length || 0} 条消息`;
  return `${context} / ${count}`;
}

function hasConversationMessages(conversation: LlmConversation) {
  return (conversation.message_count || conversation.messages?.length || 0) > 0;
}

function isReaderOriginConversation(conversation: LlmConversation | null) {
  if (!conversation) return false;
  if (conversation.origin === "reader") return true;
  if (conversation.origin) return false;
  return Boolean(conversation.context_document_ids?.length || conversation.context_documents?.length);
}

function renderMessageMarkdown(content: string) {
  const html = marked.parse(content || "", { async: false }) as string;
  return DOMPurify.sanitize(html);
}

function conversationTitleFromPrompt(content: string, maxLength = 32) {
  const title = content.replace(/\s+/g, " ").trim();
  if (!title) return "新对话";
  return title.length <= maxLength ? title : `${title.slice(0, maxLength).trimEnd()}...`;
}

function isDefaultConversationTitle(title: string) {
  return !title || title === "New chat" || title === "新对话";
}

function upsertConversation(list: LlmConversation[], conversation: LlmConversation) {
  if (!hasConversationMessages(conversation)) return list;
  const hasPendingMessage = (conversation.messages || []).some((message) => message.pending);
  const updated = {
    ...conversation,
    updated_at: hasPendingMessage ? new Date().toISOString() : conversation.updated_at || new Date().toISOString()
  };
  return [updated, ...list.filter((item) => item.conversation_id !== conversation.conversation_id)].sort((left, right) =>
    String(right.updated_at || "").localeCompare(String(left.updated_at || ""))
  );
}

function upsertGlobalLlmConversation(conversation: LlmConversation) {
  globalLlmConversations.value = upsertConversation(globalLlmConversations.value, conversation);
}

function upsertDocumentLlmConversation(conversation: LlmConversation) {
  documentLlmConversations.value = upsertConversation(documentLlmConversations.value, conversation);
}

function mergeServerConversation(serverConversation: LlmConversation, localConversation: LlmConversation | null) {
  const serverMessages = serverConversation.messages || [];
  const localMessages = localConversation?.messages || [];
  if (!localMessages.length || serverMessages.length >= localMessages.length) return serverConversation;
  const completedLocalMessages = localMessages.map((message) => ({ ...message, pending: false, error: false }));
  return {
    ...serverConversation,
    messages: completedLocalMessages,
    message_count: Math.max(serverConversation.message_count || 0, completedLocalMessages.length)
  };
}

function appendLocalLlmMessageContent(scope: "document" | "global", messageId: string, delta: string) {
  if (!delta) return;
  const current = scope === "document" ? llmConversation.value : globalLlmConversation.value;
  if (!current?.messages) return;
  const messages = current.messages.map((message) => {
    if (message.message_id !== messageId) return message;
    const currentContent = message.content === "正在思考..." ? "" : message.content;
    return { ...message, content: `${currentContent}${delta}` };
  });
  const updated = { ...current, messages };
  if (scope === "document") {
    llmConversation.value = updated;
    return;
  }
  globalLlmConversation.value = updated;
}

function createLocalLlmMessage(
  role: "user" | "assistant",
  content: string,
  state: Pick<LlmMessage, "pending" | "error"> = {}
): LlmMessage {
  return {
    message_id: `local:${Date.now()}:${Math.random().toString(16).slice(2)}`,
    role,
    content,
    created_at: new Date().toISOString(),
    ...state
  };
}

function markLocalLlmMessage(messageId: string, state: Pick<LlmMessage, "pending" | "error">) {
  if (!llmConversation.value?.messages) return;
  llmConversation.value = {
    ...llmConversation.value,
    messages: llmConversation.value.messages.map((message) =>
      message.message_id === messageId ? { ...message, ...state } : message
    )
  };
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
  activeJobs.value = mergeJobsById([job, ...activeJobs.value.filter((existing) => !isLibraryJob(existing))]);
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
    clearPersistedUploadJobs();
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
  clearPersistedUploadJobs();
  logs.value = [];
  progress.value = 0;
  statusText.value = "等待上传";
  resetReader();
}

async function startUpload() {
  if (!selectedFiles.value.length || isBusy.value) return;
  clearPollTimer();
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
    persistActiveUploadJobs();
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
  const uploadJobs = activeJobs.value.filter((item) => !isLibraryJob(item));
  if (!uploadJobs.length) {
    isBusy.value = false;
    return;
  }

  try {
    const refreshedResults = await Promise.all(
      uploadJobs.map(async (item) => {
        try {
          return { job: await getJob(item.job_id), missing: false };
        } catch (error) {
          if (isMissingJobError(error)) return { job: item, missing: true };
          throw error;
        }
      })
    );
    const missingJobIds = new Set(refreshedResults.filter((item) => item.missing).map((item) => item.job.job_id));
    const refreshedUploadJobs = refreshedResults.filter((item) => !item.missing).map((item) => item.job);
    const refreshedById = new Map(refreshedUploadJobs.map((item) => [item.job_id, item]));
    const jobs = activeJobs.value
      .filter((item) => !missingJobIds.has(item.job_id))
      .map((item) => refreshedById.get(item.job_id) || item);
    activeJobs.value = jobs;
    persistActiveUploadJobs();
    const polledJobs = jobs.filter((item) => !isLibraryJob(item));
    if (!polledJobs.length) {
      isBusy.value = false;
      if (missingJobIds.size) statusText.value = "等待上传";
      return;
    }

    const totalProgress = polledJobs.reduce((sum, item) => sum + (Number(item.progress) || 0), 0) / polledJobs.length;
    const finished = polledJobs.filter((item) => item.state === "done" || item.state === "failed").length;
    const failed = polledJobs.filter((item) => item.state === "failed").length;
    const running = polledJobs.find((item) => item.state === "running");
    const queued = polledJobs.find((item) => item.state === "queued");
    const selected = activeJobId.value ? polledJobs.find((item) => item.job_id === activeJobId.value) : null;
    const current = running || queued || selected || polledJobs[0] || null;
    if (!selected && !isLibraryJob(activeJob.value)) {
      activeJobId.value = current?.job_id || null;
    }
    logs.value = current?.log_tail || [];
    const prefix = polledJobs.length > 1 ? `队列 ${finished}/${polledJobs.length}` : "";
    const message = current?.message || (failed ? "部分任务失败" : "处理完成");
    setProgress(totalProgress, prefix ? `${prefix}：${message}` : message);

    if (polledJobs.length === 1 && activeJob.value?.state === "done" && activeJob.value.markdown_url && !markdown.value) {
      await loadJobResult(activeJob.value);
    }

    if (activeView.value !== "upload" && activeJob.value?.state === "done" && activeJob.value.markdown_url && !markdown.value) {
      await loadJobResult(activeJob.value, { openReader: false });
    }

    if (finished === polledJobs.length) {
      const selectedDone = activeJob.value?.state === "done" && !isLibraryJob(activeJob.value) ? activeJob.value : null;
      const firstDone = polledJobs.find((item) => item.state === "done");
      if (!markdown.value && (selectedDone || firstDone)) await loadJobResult(selectedDone || firstDone);
      void loadLibraryDocuments();
      isBusy.value = false;
      clearPersistedUploadJobs();
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

async function loadJobResult(job: WebJob, options: { openReader?: boolean; page?: number; skipPersist?: boolean } = {}) {
  activeJobId.value = job.job_id;
  if (job.markdown_url) await loadMarkdown(job.markdown_url, job.pages_url, options);
  downloadUrl.value = job.download_url || "";
  llmConversation.value = null;
  llmPanelMode.value = "history";
  llmInput.value = "";
  llmError.value = "";
  void loadDocumentLlmConversations();
  if (!options.skipPersist) persistCurrentReaderState();
}

async function downloadCurrentMarkdown() {
  if (!downloadUrl.value) return;
  closeReaderMenu();
  const baseName = activeJob.value?.file_name || readerTitle.value || "document";
  const stem = baseName.replace(/\.[^.]+$/, "") || "document";
  try {
    await downloadTextFile(downloadUrl.value, `${stem}.md`);
  } catch (error) {
    logs.value = [getErrorMessage(error)];
  }
}

async function loadMarkdown(url: string, pagesUrl?: string | null, options: { openReader?: boolean; page?: number } = {}) {
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
  if (options.openReader !== false) activeView.value = "reader";
  if (options.page) setReaderPage(options.page);
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

function isMissingJobError(error: unknown) {
  return error instanceof ApiError && error.status === 404;
}
</script>

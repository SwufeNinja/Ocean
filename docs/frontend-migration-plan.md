# Frontend Migration Plan

## 背景

当前 Web UI 直接内嵌在 `src/ocean/web.py` 的 `INDEX_HTML` 字符串中，由 FastAPI 在 `/` 路由返回。Vue、marked、DOMPurify 通过 CDN 加载，因此修改前端后只需要重启 Python 服务，不需要 `npm run build`。

后续项目会继续扩展阅读器、分页、缩放、关键词检索和更多交互能力，建议尽早迁移为独立前端工程，避免单个 Python 文件继续膨胀。

## 目标

- 前端迁移为独立 `Vue 3 + Vite` 工程。
- 后端继续使用 FastAPI，保留现有 OCR、任务轮询、Markdown、分页和关键词提取 API。
- 开发环境前后端分开运行。
- 生产或本地交付时，前端通过 `npm run build` 生成静态文件，并由 FastAPI 托管。
- 迁移过程中尽量保持现有功能和 API 不变，降低风险。

## 技术选型

推荐使用 `Vue 3 + Vite + TypeScript`。

原因：

- 当前前端已经使用 Vue 语法，迁移成本低。
- Vite 启动快，适合本地快速迭代。
- TypeScript 有利于后续维护 API 数据结构。
- 不需要立刻重写为 React，避免不必要的框架迁移成本。

## 目标目录结构

```text
C:\ocean
  frontend/
    package.json
    index.html
    vite.config.ts
    tsconfig.json
    src/
      main.ts
      App.vue
      api/
        jobs.ts
      components/
        UploadPanel.vue
        ReaderPanel.vue
        KeywordExtractor.vue
        ProgressStatus.vue
      styles/
        theme.css

  src/
    ocean/
      web.py
      ...
```

## 保留的后端 API

迁移第一阶段不改 API，只改前端组织方式。

需要保留：

- `GET /`
- `GET /api/engines`
- `POST /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/markdown`
- `GET /api/jobs/{job_id}/pages`
- `GET /api/jobs/{job_id}/download`
- `POST /api/jobs/{job_id}/extract-keywords`

## 阶段计划

### 阶段 1：建立独立前端工程

新建 `frontend/`，初始化 Vite。

目标：

- 增加 `package.json`。
- 增加 Vite、Vue、TypeScript 配置。
- 安装前端依赖：
  - `vue`
  - `vite`
  - `typescript`
  - `@vitejs/plugin-vue`
  - `marked`
  - `dompurify`

建议脚本：

```json
{
  "scripts": {
    "dev": "vite --host 127.0.0.1 --port 5173",
    "build": "vite build",
    "preview": "vite preview"
  }
}
```

### 阶段 2：原样迁移当前页面

先把 `INDEX_HTML` 中的 Vue 应用迁移到 `frontend/src/App.vue`，不要一开始大规模拆组件。

目标：

- 先保证功能一致。
- 移除 CDN 依赖，改为 npm import。
- 保留现有 CSS 视觉风格。
- 保留当前状态管理方式，后续再拆分。

当前功能必须可用：

- PDF 上传。
- OCR 引擎选择。
- OCR 进度和日志。
- Markdown 阅读器。
- 页内滚动框。
- 页码跳转。
- 阅读器缩放。
- 查看源码。
- 下载 Markdown。
- 关键词提取。
- 关键词结果分页。
- 命中关键词和原文页码标签。

### 阶段 3：配置开发代理

在 `frontend/vite.config.ts` 中配置 API 代理。

示例：

```ts
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000"
    }
  }
});
```

开发时运行：

```powershell
# 后端
cd C:\ocean
.\.venv\Scripts\python.exe -m ocean.web --config .\config.yaml --output .\outputs --host 127.0.0.1 --port 8000

# 前端
cd C:\ocean\frontend
npm run dev
```

访问：

```text
http://127.0.0.1:5173/
```

### 阶段 4：抽出 API 调用层

新增 `frontend/src/api/jobs.ts`。

建议封装：

- `listEngines()`
- `createJob(file, engine)`
- `getJob(jobId)`
- `getMarkdown(url)`
- `getPages(url)`
- `extractKeywords(jobId, options)`

目标：

- 组件不直接拼接 API URL。
- API 数据类型集中维护。
- 后续换路由或加字段更容易。

### 阶段 5：拆分组件

在功能稳定后再拆组件。

建议拆分：

- `UploadPanel.vue`
  - 文件选择。
  - 拖拽上传。
  - OCR 引擎选择。
  - 开始解析按钮。

- `ProgressStatus.vue`
  - 进度条。
  - 状态文本。
  - 日志展示。

- `ReaderPanel.vue`
  - 阅读器标题。
  - 命中关键词和原文页码标签。
  - 缩放控件。
  - 查看源码。
  - 下载 Markdown。
  - 内部滚动框。
  - 页码跳转。

- `KeywordExtractor.vue`
  - 关键词输入框。
  - 匹配模式。
  - 提取粒度。
  - 上下文段数。
  - 正则、大小写、简繁、去重选项。
  - 提取按钮和状态。

### 阶段 6：FastAPI 托管构建产物

前端构建：

```powershell
cd C:\ocean\frontend
npm run build
```

构建输出：

```text
C:\ocean\frontend\dist
```

修改 `src/ocean/web.py`：

- 如果 `frontend/dist/index.html` 存在，`GET /` 返回构建后的 `index.html`。
- 挂载 `frontend/dist/assets` 到 `/assets`。
- 如果 `dist` 不存在，保留旧 `INDEX_HTML` fallback，避免开发初期首页不可用。

示意逻辑：

```python
dist_root = Path("frontend/dist").resolve()
if dist_root.exists():
    app.mount("/assets", StaticFiles(directory=dist_root / "assets"), name="assets")

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    index_path = dist_root / "index.html"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return INDEX_HTML
```

### 阶段 7：清理旧内嵌前端

新前端稳定后，再考虑清理 `INDEX_HTML`。

建议先保留 fallback 一段时间，确认：

- `npm run build` 稳定。
- FastAPI 能正确托管静态资源。
- 用户启动后端时，不需要单独启动前端也能使用页面。

## 验证清单

后端测试：

```powershell
cd C:\ocean
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

前端开发验证：

```powershell
cd C:\ocean\frontend
npm run dev
```

前端构建验证：

```powershell
cd C:\ocean\frontend
npm run build
```

页面功能验证：

- 上传 PDF。
- OCR 进度显示正常。
- OCR 完成后 Markdown 阅读器显示正常。
- OCR 原文分页正常。
- 阅读器内滚动正常。
- 页码输入跳转正常。
- 上一页、下一页正常。
- 缩放控件正常。
- 查看源码正常。
- 下载 Markdown 正常。
- 关键词为空时提示用户输入。
- 关键词段落提取正常。
- 关键词页面提取正常。
- 关键词结果分页正常。
- 标题右侧显示命中关键词。
- 标题右侧显示原文页码。
- 阅读器正文不显示关键词结果元信息。

## 风险与控制

### 风险 1：迁移时功能回归

控制方式：

- 第一阶段先原样迁移，不立即重构。
- API 不变。
- 保留旧 `INDEX_HTML` fallback。

### 风险 2：本机没有 Node.js

控制方式：

- 迁移前确认 `node` 和 `npm` 可用。
- 如果不可用，先安装 Node.js LTS。

检查命令：

```powershell
node --version
npm --version
```

### 风险 3：生产环境静态资源路径错误

控制方式：

- Vite 默认使用相对 `assets` 路径即可。
- FastAPI 挂载 `/assets`。
- 构建后必须通过 `http://127.0.0.1:8000/` 验证一次。

### 风险 4：跨域或代理问题

控制方式：

- 开发环境使用 Vite proxy。
- 生产环境前端由 FastAPI 同源托管，不需要 CORS。

## 推荐实施顺序

1. 安装或确认 Node.js LTS。
2. 新建 `frontend/` Vite 工程。
3. 将当前内嵌 Vue 页面迁移到 `App.vue`。
4. 配置 Vite `/api` 代理。
5. 跑通 `npm run dev`。
6. 跑通现有上传、OCR、阅读器、关键词提取流程。
7. 拆分组件。
8. 增加 FastAPI 静态托管 `frontend/dist`。
9. 跑通 `npm run build` 后通过后端访问页面。
10. 稳定后再移除或缩小旧 `INDEX_HTML` fallback。


# OCR Assistant Frontend

Standalone Vue 3 + Vite + TypeScript frontend for the OCR Assistant web workbench.

## Development

Run the FastAPI backend from the repository root:

```powershell
.\.venv\Scripts\python.exe -m ocean.web --config .\config.yaml --output .\outputs --host 127.0.0.1 --port 8010
```

Run the Vite dev server from this directory:

```powershell
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173/
```

The Vite dev server proxies `/api` requests to `http://127.0.0.1:8010`.

## Production Build

```powershell
npm run build
```

The build output is written to `frontend/dist`. When `frontend/dist/index.html`
exists, FastAPI serves it at `/` and mounts `frontend/dist/assets` at `/assets`.
If the build output does not exist, FastAPI returns a 503 response asking you to
run `npm run build`.

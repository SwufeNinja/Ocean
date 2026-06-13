# PaddleOCR official hosted API link

This project uses PaddleOCR through the official hosted async Job API. The existing OCR pipeline still handles
PDF discovery, local page splitting, result merging, and Markdown/JSON export.

## Configuration

Set the official job URL and token in `.env`:

```env
PADDLEOCR_API_BASE_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
PADDLEOCR_API_TOKEN=replace_me
```

Use PaddleOCR in `config.yaml`:

```yaml
ocr:
  engine: paddleocr
  api_base_url: ${PADDLEOCR_API_BASE_URL}
  api_token: ${PADDLEOCR_API_TOKEN}
  options:
    api_mode: official_hosted
    model: PaddleOCR-VL-1.6
    auth_scheme: bearer
    use_doc_orientation_classify: false
    use_doc_unwarping: false
    use_chart_recognition: false
```

## Request flow

`src/ocean/ocr/paddle.py` follows the official sample flow:

1. `POST {PADDLEOCR_API_BASE_URL}` with multipart form data:
   - `file`: the local PDF file
   - `model`: for example `PaddleOCR-VL-1.6`
   - `optionalPayload`: JSON string for options such as `useDocOrientationClassify`
2. Read `data.jobId`.
3. Poll `GET {PADDLEOCR_API_BASE_URL}/{jobId}` until `data.state == "done"`.
4. Download `data.resultUrl.jsonUrl`.
5. Parse JSONL lines and map `result.layoutParsingResults[].markdown.text` into `OcrDocument`.

## Option mapping

Options in YAML are mapped to the official `optionalPayload` camelCase fields:

- `use_doc_orientation_classify` -> `useDocOrientationClassify`
- `use_doc_unwarping` -> `useDocUnwarping`
- `use_chart_recognition` -> `useChartRecognition`
- `use_table_recognition` -> `useTableRecognition`
- `use_formula_recognition` -> `useFormulaRecognition`
- `use_seal_recognition` -> `useSealRecognition`
- `use_region_detection` -> `useRegionDetection`

If the API needs extra custom fields, put them under `api_extra_payload`.

## Run

```powershell
ocean ocr --input .\hzb2025full.pdf --output .\outputs_paddle --config .\config.yaml
```

Large PDFs are still split by `ocr.options.max_pages_per_file` before calling the API.

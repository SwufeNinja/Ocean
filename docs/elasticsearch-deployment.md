# Elasticsearch deployment notes

This project can use an existing Elasticsearch cluster. It does not need a
dedicated Elasticsearch instance as long as its index names are isolated.

## What separates this project from other projects

OCR Assistant writes to six indices derived from `elasticsearch.index_prefix`:

```text
<index_prefix>_documents_v1
<index_prefix>_pages_v1
<index_prefix>_chunks_v1
<index_prefix>_jobs_v1
<index_prefix>_llm_conversations_v1
<index_prefix>_llm_messages_v1
```

For example, with:

```yaml
elasticsearch:
  enabled: true
  hosts:
    - http://127.0.0.1:9200
  index_prefix: ocean_new_pc
```

OCR Assistant creates and uses:

```text
ocean_new_pc_documents_v1
ocean_new_pc_pages_v1
ocean_new_pc_chunks_v1
ocean_new_pc_jobs_v1
ocean_new_pc_llm_conversations_v1
ocean_new_pc_llm_messages_v1
```

Use a prefix that is unique on the target computer. Do not reuse another
project's prefix.

## Recommended setup on a new computer

1. Install and start the project as usual.
2. Copy `config.example.yaml` to `config.yaml`.
3. Point `elasticsearch.hosts` to the Elasticsearch service on that computer.
4. Set a unique `elasticsearch.index_prefix`.
5. Start OCR Assistant. On startup, it creates missing indices automatically.

Example for sharing an existing local Elasticsearch:

```yaml
elasticsearch:
  enabled: true
  hosts:
    - http://127.0.0.1:9200
  username: ""
  password: ""
  index_prefix: ocean_hzb
  analyzer: ik_max_word
  search_analyzer: ik_smart
  request_timeout_seconds: 30
  verify_certs: true
```

If the existing Elasticsearch has authentication enabled:

```yaml
elasticsearch:
  enabled: true
  hosts:
    - http://127.0.0.1:9200
  username: elastic
  password: ${ELASTIC_PASSWORD}
  index_prefix: ocean_hzb
```

Put the password in `.env`:

```env
ELASTIC_PASSWORD=replace_me
```

## Analyzer requirement

The default config uses the IK Chinese analyzers:

```yaml
analyzer: ik_max_word
search_analyzer: ik_smart
```

The target Elasticsearch must have the IK analysis plugin installed. If it does
not, index creation will fail with an analyzer-not-found error.

If you cannot install IK on the target computer, use the built-in analyzer:

```yaml
elasticsearch:
  analyzer: standard
  search_analyzer: standard
```

This is easier to deploy, but Chinese search quality will be weaker.

## How to check whether the prefix is free

PowerShell:

```powershell
curl.exe http://127.0.0.1:9200/_cat/indices/ocean_hzb*?v
```

If nothing is returned, the prefix is unused. If indices already exist, choose a
different prefix unless you intentionally want to reuse the same OCR Assistant data.

## Existing Elasticsearch already has other fields

That is fine. Elasticsearch mappings are per index, not global. Other projects'
fields do not affect OCR Assistant unless OCR Assistant writes into the same index names.

The important rule is:

```text
different project = different index_prefix
```

Do not manually create OCR Assistant data inside another project's index.

## OCR Assistant fields

OCR Assistant creates mappings in code when each index is first created. The main fields
are listed here so the deployment choice is visible without reading code.

### `<prefix>_documents_v1`

Stores one record per PDF/document.

```text
account_id, knowledge_base_id, document_id
file_name, file_ext, mime_type, file_size, file_sha256
status, source, source_path
ocr_engine, ocr_options_hash, pipeline_version, processing_fingerprint
page_count, language, title, tags
metadata, markdown, ocr_json
created_at, updated_at, processed_at
```

### `<prefix>_pages_v1`

Stores page-level OCR text.

```text
account_id, knowledge_base_id, document_id
page_id, page_number
text, markdown, blocks
created_at
```

### `<prefix>_chunks_v1`

Stores text chunks for search and later analysis.

```text
account_id, knowledge_base_id, document_id
chunk_id, chunk_index
page_start, page_end
text, token_count
chunk_strategy, chunk_version
embedding_model, created_at
```

### `<prefix>_jobs_v1`

Stores OCR job status.

```text
account_id, knowledge_base_id
job_id, document_id, file_name
type, state, progress, message, error
engine, reused
created_at, updated_at, finished_at
```

### `<prefix>_llm_conversations_v1`

Stores persisted LLM conversation metadata.

```text
conversation_id, account_id, knowledge_base_id
title, origin, context_mode, context_document_ids, context_documents
system_prompt, provider, model, temperature, max_tokens
message_count, status
metadata, created_at, updated_at, deleted_at
```

### `<prefix>_llm_messages_v1`

Stores LLM conversation messages.

```text
message_id, conversation_id
account_id, knowledge_base_id
role, content, sequence
created_at, metadata
```

## When to use a separate Elasticsearch container

Use the existing Elasticsearch if:

- it is Elasticsearch 8.x or otherwise compatible with the Python client;
- it has the required analyzer, or you are willing to use `standard`;
- you can safely create new indices with a unique prefix.

Use a separate container if:

- the existing Elasticsearch version or plugins are incompatible;
- another project manages cluster settings strictly;
- you want easy deletion and backup of OCR Assistant data only.

If another Elasticsearch already uses port `9200`, map the OCR Assistant container to a
different host port, for example `9201`, then configure:

```yaml
elasticsearch:
  hosts:
    - http://127.0.0.1:9201
  index_prefix: ocean_hzb
```

## Deleting only OCR Assistant data

Only delete indices with the chosen OCR Assistant prefix:

```powershell
curl.exe -X DELETE "http://127.0.0.1:9200/ocean_hzb_*"
```

Do not run broad delete commands such as deleting `*` or deleting another
project's prefix.

# Login Plan

## Current Decision

Do not add `ocean_accounts_v1` yet.

The login identity is mapped to the existing Elasticsearch business field:

```text
login user -> account_id -> OCR Assistant documents/pages/chunks/jobs
```

## Modes

`auth.enabled: true`

- Use static users from `config.yaml`.
- Define real users with hashed passwords before enabling this mode.
- Do not deploy shared default passwords.
- Business APIs require `Authorization: Bearer <token>`.

`auth.enabled: false`

- Enter local mode.
- It still requires login.
- Local credentials:
  - username: `local`
  - password: `local`
  - account_id: `local`
- Business APIs still require `Authorization: Bearer <token>`.

## Config

Safe local default:

```yaml
auth:
  enabled: false
  jwt_secret: ${OCEAN_JWT_SECRET}
  access_token_minutes: 720
  users: []
```

When `auth.enabled=true`, `.env` must define:

```env
OCEAN_JWT_SECRET=replace_with_random_secret
```

Generate a `sha256:` password hash:

```powershell
python -c "import hashlib; print('sha256:' + hashlib.sha256('your_password'.encode()).hexdigest())"
```

Example enabled config:

```yaml
auth:
  enabled: true
  jwt_secret: ${OCEAN_JWT_SECRET}
  access_token_minutes: 720
  users:
    - username: admin
      password_hash: "sha256:<replace_with_generated_hash>"
      account_id: admin
      role: admin
      display_name: Admin
```

## Backend Scope

Implemented endpoints:

```text
POST /api/auth/login
GET /api/auth/me
POST /api/auth/logout
```

Protected API groups:

```text
/api/documents...
/api/jobs...
/api/llm/conversations...
```

Rules:

- No token returns `401`.
- Invalid token returns `401`.
- Wrong account access returns `403`.
- Frontend-provided `account_id` is not trusted when authenticated.

## Frontend Scope

Implemented:

- Login screen before workspace access.
- Token saved in local storage.
- Fetch and XMLHttpRequest APIs send `Authorization`.
- Markdown download uses authenticated fetch instead of a plain link.
- Left sidebar settings icon opens a settings panel.
- Settings panel contains logout.

## Tests

Covered:

```text
auth.enabled=false requires local/local login
static admin login returns token
wrong password returns 401
missing token returns 401
users cannot access another account_id conversation
LLM routes work with local-mode token
```

Verification commands:

```powershell
.\.venv\Scripts\python.exe -m pytest
cd frontend
npm run build
```

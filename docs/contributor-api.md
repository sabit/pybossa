# Contributor client API contract

This contract describes the backend in this checkout for a native mobile or desktop client. Accounts are provisioned separately; the client provides local-account login and a built-in contribution UI for one configured project. Pair this guide with [contributor-api.openapi.yaml](contributor-api.openapi.yaml). The separate agent API covers owner workflows and is not needed here.

Contract version: **1.1.1**. Signup is not part of this client contract.

## Client quick start

1. Configure `BASE_URL` and `PROJECT_ID`, and obtain a provisioned account's email and password.
2. POST JSON credentials to `/api/auth/login`. Save `api_key` and `user.id` from the successful response; no separate key-fetch request is needed.
3. Send `Authorization: <api_key>` when requesting `/api/project/{PROJECT_ID}/newtask`.
4. Render the returned `info` in the built-in UI, then POST `{project_id, task_id, info}` to `/api/taskrun` using the same key.
5. On local logout, discard the stored key and current task. The server key remains valid.

Login and contribution requests require no cookies or CSRF tokens. The legacy profile check and password-recovery routes documented below are optional supporting flows, not prerequisites for login or key retrieval.

## Configuration and boundaries

Configure `BASE_URL` (for example, `https://pybossa.example.org`) and integer `PROJECT_ID` before distributing the client. All URLs below are relative to `BASE_URL`. Example identifiers and payloads are illustrative: project `42`, task `1001`, contributor `7`.

Accounts and API keys belong to the PYBOSSA installation, not to a project. Restricting the client to `PROJECT_ID` does not create backend project-scoped credentials or a contributor-only account role. Use each contributor's own credentials; the client needs no owner key, project secret, or external-identity JWT. Do not load or execute the task presenter. Task creation, imports, project administration, and answer editing/deletion are outside this contract.

Use HTTPS and store API keys in platform credential storage. If using legacy password recovery, retain its cookies in a cookie jar for that flow. Do not log passwords, cookies, API keys, or account response bodies. Native HTTP clients do not require browser CORS; an embedded browser/web app has different integration requirements.

## Login and API key retrieval

The client requires an existing local account with an email address and password. Account provisioning is outside this contract. Local API login is unavailable when LDAP or two-factor authentication is enabled; it returns `403 unsupported_auth_mode`. Email-confirmation settings alone do not disable login for existing accounts.

POST `/api/auth/login` with `Content-Type: application/json`. Standard charset parameters such as `application/json; charset=utf-8` are accepted by this endpoint. No form fetch, CSRF token, cookie jar, or existing API key is needed.

```http
POST /api/auth/login
Content-Type: application/json

{"email_addr":"contributor@example.org","password":"example-password"}
```

Both fields must be nonempty strings. Login always verifies these credentials; ambient cookies or Authorization headers do not replace password verification. Successful login returns HTTP 200:

Use `email_addr`, not the legacy website login field `email`. Values are used as supplied; the endpoint does not trim or normalize them. Additional JSON properties are ignored and do not change the account or its permissions.

```json
{
  "user": {
    "id": 7,
    "name": "example_contributor",
    "fullname": "Example Contributor",
    "email_addr": "contributor@example.org"
  },
  "api_key": "existing-user-api-key"
}
```

This response contains only the identity fields shown and the existing API key. The endpoint does not create an authenticated cookie session, redirect, or rotate the key. Responses carry `Cache-Control: no-store`. Store the contributor ID and API key in platform credential storage.

Use the key for subsequent API requests:

```http
Authorization: <CONTRIBUTOR_API_KEY>
Content-Type: application/json
```

The key is the entire header value: do not prefix it with `Bearer` or `Basic`. Existing APIs also accept `?api_key=...`, but prefer the header to avoid URL logging. Existing APIs still support cookie authentication; avoid mixing identities between cookies and API keys.

A failed login returns a JSON error with an appropriate HTTP status, rather than an HTTP-200 form response:

```json
{
  "error": {
    "code": "invalid_credentials",
    "message": "Invalid email or password."
  }
}
```

| HTTP status | Error code | Meaning |
| --- | --- | --- |
| 400 | `invalid_json` | Missing/malformed JSON or a body that is not an object. |
| 400 | `validation_failed` | Missing, empty, or non-string credentials; `error.fields` maps fields to message arrays. |
| 401 | `invalid_credentials` | Unknown account, incorrect password, or no local password. |
| 403 | `unsupported_auth_mode` | LDAP or two-factor authentication is enabled. |
| 415 | `unsupported_media_type` | Request does not have a JSON content type. |
| 429 | `rate_limited` | Configured endpoint/IP rate limit reached. |

The login error body never returns a key or echoes the supplied password. Rate limiting runs before request validation, so any login attempt can return 429. Rate-limit headers use the existing API convention described below and are also returned on ordinary login successes and failures when the limiter is available. Unexpected server/proxy failures may still need generic handling.

Existing contribution APIs have different authentication behavior: an invalid key may fall back to anonymous access or a session, rather than return 401. Verify identity after restoring a previously stored key using GET `/account/profile` with exactly `Content-Type: application/json` and the key, without unrelated cookies. An authenticated response has `user.id`; an unauthenticated response is HTTP 200 with `{"next":"/account/signin","status":"not_signed_in"}`. Inspect the identity of saved task runs too. Where authenticated contributions are required, the project operator should disable anonymous contributions.

## Local logout and password recovery

Client logout clears the local API key, any account-flow cookies, and the current task. It does not revoke the server API key. There is no new logout endpoint, refresh-token flow, or per-device key in this contract.

Existing password recovery is available through account routes. These routes still use the legacy cookie/CSRF flow: GET the form with exactly `Content-Type: application/json`, retain its cookies, and POST JSON with `X-CSRFToken: <form.csrf>` from the same cookie jar. Unlike the new login endpoint, these routes compare the content-type header literally. An Accept header alone is insufficient. Deployment HTTPS referrer enforcement may also apply.

For a forgotten password, GET `/account/forgot-password`, then POST `{"email_addr":"contributor@example.org"}`. HTTP 200 returns the form and a status/message: `success` means the email was queued, not delivered; an unknown address can return `error`.

The emailed link contains `/account/reset-password?key=...`. Preserve and URL-encode its opaque key. GET it for CSRF, then POST to the same URL with `{"new_password":"example-new-password","confirm":"example-new-password"}`. Success changes the password and signs in that HTTP client, returning a JSON `next` navigation hint. Missing/invalid/expired/already-used keys can return 403. A password reset does not rotate the API key. If the link opens in an external browser, log in through `/api/auth/login` afterward to retrieve the key in the native client.

Account recovery validation failures can return HTTP 200 with `form.errors`, `status`, and localized `flash` text. Inspect the body instead of treating 200 as success. Do not follow arbitrary navigation hints with credentials, or log account responses.

## Contribution flow

### 1. Request one task

```http
GET /api/project/42/newtask
Authorization: <CONTRIBUTOR_API_KEY>
Content-Type: application/json
```

HTTP 200 returns either a task object or `{}` when no task is available for that caller. A task response includes:

```json
{
  "id": 1001,
  "project_id": 42,
  "state": "ongoing",
  "info": {"asset_url":"https://assets.example.org/item.jpg"},
  "n_answers": 30
}
```

This is a partial response. Require a non-null task `id`, the configured `project_id`, and a payload your built-in UI understands before rendering. Some anonymous-access failures return HTTP 200 with a task-shaped response whose `info.error` is `This project does not allow anonymous contributors`; this is an error, not a renderable task.

Use the default single-task request. Optional `limit` is capped at 100, but output shape depends on the number actually returned: zero gives `{}`, one gives an object, and multiple gives an array. The client flow here does not prefetch batches. Fetching `/api/task` or `/api/task/{id}` does not establish the required request stamp.

Requesting a task creates a caller/task stamp valid for 3,600 seconds. It is not generally an exclusive reservation; a project's scheduler can impose a separate lock with a shorter configured lifetime. No generic lease-renewal API is provided. Avoid offline submission assumptions and concurrent requests from multiple client instances.

### 2. Render and submit

Render the project-specific `info` using the built-in UI, validate the answer locally, then submit using the same contributor identity:

```http
POST /api/taskrun
Authorization: <CONTRIBUTOR_API_KEY>
Content-Type: application/json

{"project_id":42,"task_id":1001,"info":{"label":"example-label"}}
```

Task-run POST is CSRF-exempt. Send only these three fields in this client. Do not set user identity, timestamps, or `id`; the backend derives the contributor and timestamps. `id`, `created`, and `finish_time` are explicitly reserved and rejected with 400. A successful submission returns HTTP **200**, with the stored task run, including:

```json
{
  "id": 501,
  "project_id": 42,
  "task_id": 1001,
  "user_id": 7,
  "info": {"label":"example-label"},
  "created": "2026-09-23T10:00:00",
  "finish_time": "2026-09-23T10:00:15"
}
```

Check returned identifiers and `user_id`, then record the submission ID before requesting the next task. Timestamps are UTC text and may lack an explicit timezone suffix. Successful submission means that contribution was saved, not that the task reached consensus or the whole project completed.

The backend checks the task/project relationship, request stamp, scheduler permission, and duplicate contribution rules. It does not validate the contents of `info` against a project schema; `info` is a nullable JSONB field and accepts arbitrary JSON. The client's requirement to send `info` is a usage rule, not an additional backend guarantee. This contract models it accordingly.

### 3. Progress and submission reconciliation

GET `/api/project/42/userprogress` with the contributor key returns `{"done":12,"total":100}`. `done` counts that caller's task runs; `total` is the project's task count. This is not a promise that `total - done` tasks are currently available. Progress does not validate the API key by itself because anonymous callers are supported.

After a submission timeout or lost response, do not blindly retry or assume the answer failed. Query GET `/api/taskrun?all=1&project_id=42&task_id=1001&user_id=7` with the same key and inspect the returned array for a matching saved run. `all=1` removes the API's default project-owner filter; it is necessary for contributors who do not own the project. Keep the explicit project, task, and contributor filters. This read does not reserve a task. Verify identity and answer before marking a pending submission complete. No idempotency key or exactly-once delivery guarantee exists. An empty result immediately after a timeout is inconclusive if the original request is still executing; preserve the answer for reconciliation.

The request stamp is removed during submission processing before authorization/save completes, so a failed POST can consume it. A duplicate or expired request commonly returns 403, including `You must request a task first!`. Re-enter the assignment flow after resolving a pending submission; the next assigned task may differ. Never attach a preserved answer to a different task ID. Concurrent retries are not a safe duplicate-prevention strategy.

## Project payload agreement

Before shipping, the project owner and client developer must fill in this table. No concrete project schema was supplied for this contract.

| Item | Placeholder to agree |
| --- | --- |
| Project | `PROJECT_ID` and deployment `BASE_URL` |
| Task input | Required/optional `task.info` fields, types, asset access, and examples |
| Answer | Required/optional `taskrun.info` fields, allowed values, and examples |
| Compatibility | How the client recognizes unsupported or changed payloads |
| Validation | Client checks and error UI for malformed task data or answers |

`asset_url` and `label` above are illustrative, not PYBOSSA-defined fields. OpenAPI uses unrestricted `TaskInfo` and `AnswerInfo` schemas to reflect the backend. Replace or specialize these in a project-specific client schema once agreed, without claiming server enforcement. The baseline flow is JSON-only; multipart media upload requires an extension to this client contract if the project needs it.

## Errors and deployment checklist

Existing contribution API errors generally use this envelope (the new login endpoint uses the error envelope above):

```json
{
  "status":"failed",
  "status_code":403,
  "target":"taskrun",
  "action":"POST",
  "exception_cls":"Forbidden",
  "exception_msg":"You must request a task first!"
}
```

| HTTP status | Client handling |
| --- | --- |
| 200 | Inspect the body; legacy account validation failure and some assignment errors use 200. |
| 400 | Invalid/reserved fields or CSRF failure; correct the request or refresh the account form. |
| 401 / 403 | Check identity, project policy, assignment/lock expiry, duplicates, or account-link validity. |
| 404 | Missing project/resource. |
| 405 | Unsupported HTTP method. |
| 415 | API JSON/type/model errors can use this status; it is not limited to media types. |
| 429 | Back off using `X-RateLimit-Reset` when present (Unix epoch seconds). |
| 500 / transport failure | Preserve pending answers and reconcile before retrying submissions. |

Account/CSRF errors can instead return `{"template":"400.html","code":400,"description":"..."}`. Other errors can be HTML (notably generic 403 handling or proxies). Check the response content type before JSON parsing; do not assume every failure has the API envelope. Rate-limited APIs expose `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` when available. Limits are configuration-dependent and normally grouped by endpoint and caller IP, so native users behind one network can share a limit.

Confirm these deployment choices with the operator:

- Accounts are provisioned with local passwords; LDAP and two-factor authentication are disabled for this login endpoint.
- `PROJECT_ID`, anonymous-contribution policy, scheduler/lock settings, API rate limits, and asset access are agreed.
- If password recovery is exposed in the client, email delivery, `ACCOUNT_LINK_EXPIRATION`, password-strength policy, and CSRF/cookie settings work with its HTTP stack.
- Recovery links have an agreed external-browser or native-link handoff; external browsers do not share native cookies.

Integration acceptance checks:

- Log in without cookies/CSRF, retrieve the existing key, and use it on an existing API.
- Exercise incorrect credentials, malformed JSON, invalid fields, unsupported modes, and throttling.
- Confirm repeated logins return the same key and do not create an authenticated session.
- If enabled in the client, exercise password recovery, including expired links and legacy form errors.
- Request a task, validate the project/payload, submit with the same identity, and verify stored run/progress.
- Handle `{}`, task-shaped errors, expired stamps/locks, and duplicate submissions without losing an answer.
- Simulate a lost submission response and reconcile via the filtered task-run read.
- Handle non-JSON failures and local logout; verify local credentials are cleared.

## Implementation references

The new login contract is implemented in `pybossa/api/login.py` and tested in `test/test_api/test_login_api.py`. The source of truth for legacy account behavior is `pybossa/view/account.py`, `pybossa/forms/forms.py`, `pybossa/util.py`, and `pybossa/core.py` for account/authentication flows; `pybossa/api/__init__.py`, `pybossa/api/task_run.py`, `pybossa/api/api_base.py`, `pybossa/auth/taskrun.py`, `pybossa/contributions_guard.py`, and `pybossa/sched.py` for contributions. Existing account JSON coverage is in `test/test_web.py`; submission coverage is in `test/test_api/test_taskrun_api.py`. This document does not imply those tests were run against your deployment.

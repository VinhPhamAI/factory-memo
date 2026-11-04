# Phase 3: Frontend

## Context links

- [Plan overview](plan.md), [phase 2 contract](phase-02-api.md)
- Auth client: `AuthClient.authorizedFetch` and `refresh` in `frontend/src/services/auth-api.ts`
- Hook to get the client: `useAuthClient` in `frontend/src/hooks/auth.tsx`
- Routes: `frontend/src/main.tsx`; navigation: `frontend/src/components/qa-shell.tsx`
- Dialog component: `frontend/src/components/ui/dialog.tsx`
- E2E example: `frontend/e2e/auth.spec.ts`

## Overview

- Priority: medium.
- Status: complete.
- Adds an API service and a new page `/files` in live mode (the mode that requires sign-in).

## Key insights

- `authorizedFetch` attaches the token but never retries. The file service retries once after a `401`.
- A plain `<a href>` cannot send the token, so downloads go through `fetch` and a blob URL.
- The mock demo mode has no backend. The page is added only to live-mode routes.

## Requirements

Functional:

- Upload one or more files; show a result or an error per file.
- Table of files: name, type, size, upload date; newest first.
- Download and delete buttons per row. Delete asks for confirmation in an in-app dialog.
- Empty state with a short upload prompt.
- A note that library files are not added to any knowledge base.

Error messages (Vietnamese, shown next to the action):

| Cause | Trigger |
|---|---|
| File too large (max 100 MiB) | Browser pre-check or `413` |
| Unsupported or mismatched file type | Browser pre-check or `415` |
| File no longer exists | `404` |
| Session expired | `401` after a failed refresh |
| Cannot reach the server | Network error |
| Server unavailable | `503` |

Non-functional:

- Browser checks only warn early. The server still validates everything.
- Never use `window.confirm` or `alert`.

## Architecture

```
FilesPage (pages/files.tsx)
  └─ file-api.ts: listFiles / uploadFile / downloadFile / deleteFile
        └─ AuthClient.authorizedFetch
              on 401 -> client.refresh() -> retry once
              refresh failed -> client signs out -> RequireAuth shows sign-in
```

Retry sketch:

```ts
async function send(client: AuthClient, url: string, init: RequestInit = {}): Promise<Response> {
  let response = await client.authorizedFetch(url, init);
  if (response.status === 401 && (await client.refresh()))
    response = await client.authorizedFetch(url, init);
  return response;
}
```

Download sketch:

```ts
const blob = await response.blob();
const url = URL.createObjectURL(blob);
const link = Object.assign(document.createElement("a"), { href: url, download: file.filename });
link.click();
URL.revokeObjectURL(url);
```

## Related code files

| Action | Path |
|---|---|
| Create | `frontend/src/services/file-api.ts`, `frontend/src/services/file-api.test.ts` |
| Create | `frontend/src/pages/files.tsx`, `frontend/src/pages/files.test.tsx` |
| Create | `frontend/e2e/files.spec.ts` |
| Modify | `frontend/src/main.tsx` (lazy live-mode route `/files`) |
| Modify | `frontend/src/components/qa-shell.tsx` (nav link "Thư viện file") |

## Implementation steps

1. Write `file-api.test.ts`: success per function, each error code mapped to its message, `401` → refresh → retry once, refresh failure.
2. Implement `file-api.ts`.
3. Write `files.test.tsx`: empty state, list rendering, upload error shown, delete asks for confirmation then refreshes the list, download error shown.
4. Implement `pages/files.tsx` with the existing UI components (`button`, `dialog`).
5. Add the route and the nav link.
6. Write `e2e/files.spec.ts`: sign in, upload a PDF, see it listed, download it, delete it, and upload a `.txt` to see the type error.

## Todo list

- [x] `file-api.test.ts` written and failing
- [x] `file-api.ts` implemented
- [x] `files.test.tsx` written and failing
- [x] `pages/files.tsx` implemented
- [x] Route and nav link added
- [x] `e2e/files.spec.ts` passes (`PLAYWRIGHT_SUITE=files`, 1/1, against a real backend)
- [x] `npm run build` and `npm run lint` pass

## Success criteria

- Upload, download and delete work from the page.
- Every failed action shows its message, and no action fails silently.
- Existing pages and their tests are unchanged.

## Risk assessment

| Risk | Mitigation |
|---|---|
| Large downloads use a lot of browser memory | Accepted at the current scale; signed links later if needed |
| Two refreshes at once | `AuthClient.refresh` already joins a refresh in flight |

## Security considerations

- The token stays in memory inside `AuthClient`. It is never put in a URL.
- The blob URL is revoked right after the download starts.

## Next steps

- Phase 4 documents the page and runs the full checks.

## Deviations (as built)

- Delete that returns `404` (file already gone) removes the row from the list and shows a notice.
- The download blob URL is revoked after 60 seconds, not right after the click.
- Upload shows new messages for `400` and `411` responses.
- The Playwright selector is `PLAYWRIGHT_SUITE=files`, set in `frontend/playwright.config.ts`.
- Follow-ups, non-blocking (from code review):
  - S4: the Refresh button stays enabled during an upload, so it can hide a just-uploaded file until the next refresh.
  - S6: no screen-reader text while an upload is in progress.

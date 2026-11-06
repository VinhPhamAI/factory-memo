# Code review: Factory Memo UI refinement (uncommitted frontend diff)

## Scope

- Files: 34 modified under `frontend/`, plus the untracked `frontend/public/factory-memo.svg` and `frontend/src/components/library-file-dialog.tsx`. `backend/worker.py` and `plans/` are excluded.
- LOC: +2308 / -704 (about 1350 of the added lines are CSS).
- Plan: `plans/261106-1757-factory-memo-ui-refinement/` (plan.md and phases 01–04).
- Checks run: `git diff --check` is clean. tsc, eslint, vitest and Playwright were not rerun; the caller had already run them.

## Overall assessment

No critical or high-severity defects were found. Routes, roles, the admin gate (`KnowledgeAdminPage`), API calls, citation handling, stream handling, inquiry actions and draft/approval rules are unchanged. Status and mode keys are unchanged in `STAGES`, `MODES`, `MODE_OPTIONS`, `STATUS_LABELS`, the `<option value>` attributes and `data-status`. Only the display strings changed. The raw `stage` and `mode` values are now shown only in the technical-details disclosure.

The preview dialog's Blob URL handling is correct. The diagnosis focus handling has two real gaps (M1, M2).

## Critical

None.

## High

None.

## Medium

### M1. The diagnosis overlay loses focus when the user clicks a non-focusable area, so the trap and the composer guard stop working

- **Where:** `frontend/src/components/diagnosis/machine-diagnosis-panel.tsx:207-221` (`onKeyDown` on the `<aside>`, which has no `tabIndex`), and the guard in `frontend/src/pages/conversations.tsx:318-320`.
- **Scenario:** the panel is open as a modal at 1100px or narrower, and a stream is still running. The user drags the 3D canvas, clicks a 3D station label, or clicks the metric text.
  1. None of these targets is focusable, and the `<aside>` is not focusable either, so focus moves to `document.body`.
  2. Tab no longer passes through the panel's `onKeyDown`. Focus escapes to the topbar and sidebar under the overlay, even though `aria-modal="true"` tells assistive technology that this content is inert.
  3. When the stream ends, `document.activeElement` is `body`. `closest('[aria-modal]')` therefore returns null, and `composer.current.focus()` moves focus to the composer hidden under the overlay. At 860px or narrower the overlay covers the whole screen, so keyboard input goes into a control the user cannot see.

  Rotating the scene while the answer streams is the main use of the panel, so this case is likely.
- **Fix:**
  - Add `tabIndex={-1}` to the `<aside>` when `modal` is true. A click on a non-focusable descendant then focuses the panel instead of `body`.
  - In `onKeyDown`, handle `document.activeElement === panel.current`: Shift+Tab goes to the last focusable element, and Tab goes to the first.
  - In `conversations.tsx`, decide based on whether a modal overlay is open, not on where focus is. For example: `if (!document.querySelector('[aria-modal="true"]')) composer.current?.focus();`. Passing a `diagnosisCoversChat` flag down is a cleaner alternative.

### M2. The mock demo (`ask.tsx`) still moves focus out of the now-modal overlay

- **Where:** `frontend/src/pages/ask.tsx:270`, which runs `composer.current?.focus()` in `finally` with no guard. Since `variant="overlay"`, `modal` is always true (`machine-diagnosis-panel.tsx:168`).
- **Scenario:**
  1. `diagnosis.trigger(question)` runs on submit (`ask.tsx:184`) and opens the overlay.
  2. The new effect (`machine-diagnosis-panel.tsx:177-187`) moves focus to the close button.
  3. When the mock stream ends, `ask.tsx` puts focus back on the composer, outside the `aria-modal` dialog.

  Keyboard and screen-reader users end up behind a dialog that claims to be modal. `conversations.tsx` received this guard; `ask.tsx` did not.
- **Fix:** apply the same guard as in M1 to `ask.tsx:270`. Better, put it in one small helper that both pages use, so the two integration points cannot drift apart.
- **Related:** the two Playwright mock tests that fail with `VITE_MACHINE_DIAGNOSIS_VIZ=true` are probably caused by the overlay geometry, not by this diff. The `.dx-panel.overlay` position and size did not change, and `workspace.spec.ts` never closes the panel. This was not checked against HEAD (see the questions at the end).

## Low

### L1. Focus is lost when the overlay closes after being opened from the reopen button

- **Where:** `machine-diagnosis-panel.tsx:178-186` and `:190-196`.
- **Scenario:**
  1. The `dx-reopen` button belongs to the same component and unmounts when the panel opens. When the effect runs, `opener` is therefore `document.body`.
  2. On close, `body.focus()` does nothing. Focus is not restored to the reopen button, which has just been rendered again.
- **Fix:** if `opener` is `body` or null, focus `[data-testid=diagnosis-reopen]` after closing (for example in a `requestAnimationFrame`, or from a ref on the reopen button).

### L2. Widening the window past 1100px while the panel is open moves focus out of the panel

- **Where:** `machine-diagnosis-panel.tsx:187`. The effect depends on `modal`, so its cleanup runs when the window crosses the 1100px breakpoint.
- **Scenario:** the cleanup focuses the opener (the composer) while the user is working inside the now-docked panel.
- **Fix:** restore focus only when `visible` becomes false. Keep `opener` in a ref and focus it in the close path, not in the cleanup for a `modal` change.

### L3. The preview tests do not cover the claims about cancellation and revocation

- **Where:** `frontend/src/pages/files.test.tsx` (the new `library file preview` block).
- **Gaps:**
  - No test asserts that `URL.revokeObjectURL` is called on close.
  - No test asserts that a late response after closing is ignored.
  - The test assigns `URL.createObjectURL` and `URL.revokeObjectURL` globally with `vi.fn` and never restores them, so the mocks carry over into later tests in the file.
- **Fix:** in the existing small-image test, close the dialog and assert `expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview")`. Add one test with a deferred `/content` response that resolves after Escape and assert that `createObjectURL` is never called. Use `vi.spyOn(URL, "createObjectURL")` together with `restoreAllMocks`.

The code itself is correct. I traced the following paths in `library-file-dialog.tsx:111-148`:
- The abort check runs before `createObjectURL`.
- The cleanup aborts, revokes and calls `setPreview(null)`.
- `preview.for === id` blocks results for a different file.
- `{file && …}` stops a revoked URL from rendering during the close animation.
- `useAuthClient` returns a stable context value, so the effect does not loop.

### L4. The admin list no longer shows the KB ID, so collections with the same title look identical

- **Where:** `frontend/src/pages/knowledge-admin.tsx:178-181`. The list shows `displayName` and the description, but no longer `item.name`.
- **Scenario:** two KBs with the same title and no description look identical in the list. The admin has to select each one and open the technical details to tell them apart.
- **Fix (optional):** show `item.name` as a muted suffix only when `displayName` is duplicated. Otherwise, accept this as an intended part of the plan.

### L5. The phone history drawer stays open after the window widens past 860px

- **Where:** `frontend/src/components/qa-shell.tsx`.
- **Scenario:** the drawer is open and the window or device is resized above 860px. The drawer stays open next to the sidebar. This is cosmetic.
- **Fix:** close the drawer on a `(min-width: 861px)` change.

## Verified as not issues

- **Hooks order:**
  - The new `useMediaQuery` and `useEffect` in the diagnosis panel run before both early returns.
  - `QaShell` calls `useLocation()` unconditionally.
  - `AccountPanel` returns null after its hooks have run.
- **Hidden tab stops:**
  - The collapsed sidebar uses `hidden`, with an explicit `.app-sidebar[hidden]{display:none}` rule.
  - `.sidebar-toggle` and the desktop aside are `display:none` at 860px or narrower.
  - `.app-menu-button` and the topbar brand are `display:none` on desktop unless the sidebar is collapsed.
  - The running-state toolbar and content use `inert`, and `focusableIn` skips inert regions.
- **Duplicate IDs and landmarks:**
  - Two `nav[aria-label="Chức năng"]` elements exist, but CSS shows only one at a time.
  - `new-chat`, `sign-out` and `signed-in-user` appear twice when the phone drawer is open (the CSS-hidden aside plus the drawer). The old `Sidebar` was rendered twice in the same way, so this is not a regression. The e2e tests scope these lookups to the drawer.
  - `mock nav-ask` is still provided by `shell.tsx` at line 150.
- **320px overflow:** I found no fixed-width element wider than the viewport. Checked:
  - The phone tab grid uses `minmax(0,1fr)` and `overflow-wrap:anywhere`.
  - The library grid has 1 column at 360px or narrower.
  - The dialog width is `calc(100% - 16px)`.
  - The KB admin layout is one column at 850px or narrower.
  - The inquiry rows use `minmax(0,1fr) auto`.
  - The diagnosis panel uses `width:100vw` on `position:fixed`, which does not add to document scroll width.
- **Contracts:**
  - `fetchFileContent` uses the same `/v1/files/{id}/content` path through `send`, and `openFile` and `downloadFile` now share it.
  - Backend `files.py:140` returns the stored `content_type`, so `blob.type.startsWith("image/")` is reliable.
  - `.km-advanced` wraps the extraction-mode select. The select's value and the extract action are unchanged, and the summary shows the current mode.
- **Plan follow-ups:**
  - Phase 01, steps 2–4: implemented.
  - Phase 02, step 2: implemented. Abort, stale-result guard, revoke and focus restoration are all present; the tests are thin (L3).
  - Phase 03: implemented.
  - Phase 04, step 3: partly implemented. Focus behaviour after a pointer click (M1) and in the mock integration point (M2) is incomplete.

## Recommended actions

1. Fix M1: make the modal `<aside>` focusable with `tabIndex={-1}`, handle Tab when the panel itself has focus, and base the composer refocus on whether an overlay is open.
2. Fix M2: apply the same guard to `ask.tsx:270` through a shared helper.
3. Optional: L1 and L2 (focus restoration edge cases) and L3 (assert revoke and stale-response handling).

## Metrics

- Type coverage: no `any` or `@ts-ignore` added.
- Lint: clean, according to the caller.
- Tests: the caller reported 193 passing. New tests cover the preview, shortcuts, panel focus and the shell toggle. Blob revocation and stale-response handling are not covered.

## Unresolved questions

- Do the two mock Playwright failures with `VITE_MACHINE_DIAGNOSIS_VIZ=true` also happen at HEAD? I could not run the old tree, but the overlay geometry did not change.
- Should the mock `variant="overlay"` panel be modal at 1280px and wider, where it covers only the right 640px of the chat? Phase 04 asks for modal behaviour only for "an overlay that obscures chat". The current code makes it modal at every width.

Status: DONE_WITH_CONCERNS
Summary: No contract, status-value or Blob-URL regressions were found. The diagnosis focus trap fails after the user clicks a non-focusable area of the panel (M1), and `ask.tsx` still moves focus out of the now-modal overlay (M2).

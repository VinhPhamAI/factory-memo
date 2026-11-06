# Phase 01. Theme, navigation and chat

**Done · 5h · Prerequisite for phases 02–04**

## Affected files

- Modify `frontend/src/index.css`, `frontend/src/components/brand.tsx` and `frontend/index.html` for the app theme, brand mark and favicon. Create `frontend/public/factory-memo.svg` as the shared asset.
- Modify `frontend/src/components/qa-shell.tsx`, `frontend/src/components/conversation-list.tsx` and `frontend/src/pages/conversations.tsx` for navigation, history and composer layout.
- Use shared theme styles in `frontend/src/components/sign-in.tsx` and `frontend/src/components/shell.tsx`. Edit those components only where existing markup needs a styling hook. Keep mock route content and capture UI behavior.
- Update `frontend/e2e/qa.spec.ts` and `frontend/src/pages/conversations.test.tsx` where new interactions or visible labels warrant changes. Retain stable selectors.

## Steps

1. [ ] Reuse existing theme variables and UI primitives. Define semantic surface, text, border, focus, action and document colors where needed. Use the prototype SVG in the shell and favicon. Use subtle surface and border differences to separate regions.
2. [ ] Keep desktop history in the compact sidebar and place the collapse toggle in a header that remains visible when collapsed. Keep collapse state inside `QaShell`; preserve the existing authenticated `ConversationsProvider` and mounted `Outlet`. Keep desktop and mobile state independent. The collapsed sidebar must leave no hidden tab stops.
3. [ ] Give phones a compact section nav and an accessible drawer for history and account actions. Reuse the existing Radix Sheet and conversation list. Use one navigation pattern at a time and keep the existing admin-only route gate. Preserve new chat, history selection, route highlighting, rename/delete where available, and logout/error feedback.
4. [ ] Place the existing composer once in the DOM. Center it in a new chat under **Trò chuyện mới** with **Nhập câu hỏi hoặc mã lỗi…**. Keep it at the bottom once a thread has content. Place **Tra mã lỗi** and **Tìm tài liệu** below the empty composer. The first focuses the composer and may prefill an editable prompt only if it is empty. The second links to the actual library. Preserve send, stop, dictation, loading, errors, history, tool cards, citations and inquiry drafting. Keep Enter/Shift+Enter behavior and guard against submission during IME composition.

## Completion checks

- Desktop at 1280 and 1440. Collapse and reopen with mouse and keyboard. The current route, unsent text and streaming response survive the toggle. The reopened history identifies the active chat. Test loading/error/empty history and long titles.
- Phone at 320 and 390 plus a real keyboard-opening check. Open and dismiss history, switch chat, start a new chat and sign out. Focus returns to the drawer trigger on dismissal. The composer and send/stop controls remain reachable with the keyboard open. No page-wide horizontal scrolling or hidden navigation stops.
- With controlled SSE, final text replaces provisional text once. Reload saved history, stop a stream, show an unconfirmed response without resending, edit dictation before sending, follow a citation and open the chat-generated inquiry draft. Compare employee and admin navigation. Verify sign-in and mock shell still render.


# Phase 04. Diagnosis theme and regression verification

**Done · 5h · Integrate after phases 01–03**

## Affected files

- Modify `frontend/src/components/diagnosis/diagnosis.css` and `frontend/src/components/diagnosis/machine-diagnosis-panel.tsx` for the panel, in-chat card, mock labels, controls and overlays. In `frontend/src/components/diagnosis/production-line-scene.tsx`, scope edits to decorative palette values. Keep `frontend/src/pages/conversations.tsx` and `frontend/src/pages/ask.tsx` as the two integration points. Avoid layout changes to the mock workflow beyond those required to keep the overlay usable.
- Use `frontend/src/components/diagnosis/machine-diagnosis-panel.test.tsx`, `frontend/src/services/machine-diagnosis.test.ts`, the existing Playwright suites and a manual browser matrix. Add a focused browser check if manual testing exposes a layout regression worth retaining.

## Steps

1. [ ] Theme viewer chrome, labels and running state with white, blue and muted green. Use cool blue-gray for neutral scene surfaces and restrained accents on non-status parts. Keep red fault, green healthy and blue selection distinguishable by label or outline as well as color. Translate primary labels into Vietnamese and keep **Dữ liệu mô phỏng** visible.
2. [ ] Preserve `VITE_MACHINE_DIAGNOSIS_VIZ`, four keyword-driven scenarios, timing, per-turn reopen and thread reset. Keep the lazy import, geometry, station IDs, hit targets, label picking, camera and OrbitControls behavior, renderer lifecycle, ResizeObserver and disposal. Preserve keyboard station controls, WebGL fallback and reduced-motion handling. Keep the existing data, metrics, checks and mock-only analysis behavior.
3. [ ] Check live docked and mock overlay variants. Confirm the existing live dock-to-overlay transition around 1100px still fits when the sidebar opens or closes. Where chat and panel cannot both fit, use the existing overlay path and keep the chat mounted. For an overlay that obscures chat, move focus into the panel, keep keyboard navigation within it, support Escape, and restore focus to the opener on close. Keep the docked variant nonmodal. Ensure hidden checklist controls cannot receive focus while the running state covers them. Check layering against dialogs, canvas gestures and scroll outside the canvas.
4. [ ] During implementation run `npm run build`, `npm run lint`, `npm run test` and `git diff --check`. Run the controlled API-mode QA suite and mock workspace suite, then the real-backend files, auth and inquiries suites when a disposable environment is available. Playwright starts Vite. Use a development test session with the diagnosis flag enabled for the real WebGL checks. Report environmental skips and avoid paid model calls. Record baseline vs new regressions and inspect the final diff for behavior changes.

## Completion checks

- Desktop. At 1280 and 1440 and on either side of the current sidebar and diagnosis breakpoints (860/861 and 1100/1101), open the viewer with the sidebar expanded and collapsed. Confirm readable chat/composer, orbit, wheel zoom, click/keyboard selection, focus fault, reset and auto-rotate. Check each fault scenario and verify the correct station and metrics.
- Phone. At 320 and 390 and in landscape, drag, pinch, tap a station and use the station buttons. Scroll the surrounding panel from outside the canvas. Close, reopen and switch threads. Verify access to the composer afterward, overlay stacking, touch targets, mock labels and no viewport overflow.
- Run with flag disabled, WebGL unavailable, and reduced motion enabled. In the unavailable case confirm fallback and that chat, station controls and checklist remain usable. Exercise repeated opens, closes and route changes with real WebGL. Confirm one canvas per open viewer, responsive resizing and no renderer errors in the console. Unit tests with a mocked scene supplement this check.
- Smoke test authenticated login/logout, live chat and inquiry drafting, citations, voice input, file upload and processing status, source review, inquiry evidence, and the mock recording-to-answer workflow after the shared styles land.

import { useCallback, useEffect, useRef, useState } from "react";
import { MicError, MicRecorder } from "@/services/mic-recorder";
import { ConversationError } from "@/services/conversations-api";

/** Recordings stop on their own a few seconds before the backend limit (120 s). */
export const DICTATION_MAX_MS = 115_000;

export type DictationState = "idle" | "recording" | "transcribing";

/**
 * Record one clip from the microphone and turn it into text.
 *
 * `onText` receives the transcript together with the `target` passed to
 * `start`, so the caller can drop a result that arrives after the user moved
 * to another conversation.
 */
export function useDictation(
  transcribe: (wav: Blob, signal: AbortSignal) => Promise<string>,
  onText: (text: string, target: string | undefined) => void,
) {
  const [state, setState] = useState<DictationState>("idle");
  const [error, setError] = useState("");
  const recorder = useRef<MicRecorder | null>(null);
  const target = useRef<string | undefined>(undefined);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const abort = useRef<AbortController | null>(null);
  // True from start() until the transcript is handled or cancelled.
  const busy = useRef(false);
  // Incremented by cancel(), so a microphone still opening is discarded.
  const generation = useRef(0);

  useEffect(
    () => () => {
      clearTimeout(timer.current);
      abort.current?.abort();
      void recorder.current?.stop();
    },
    [],
  );

  async function stop() {
    const active = recorder.current;
    if (!active) return;
    recorder.current = null;
    clearTimeout(timer.current);
    setState("transcribing");
    const controller = new AbortController();
    abort.current = controller;
    try {
      await active.stop();
      const text = await transcribe(active.toWav(), controller.signal);
      if (!controller.signal.aborted) {
        if (text.trim()) onText(text.trim(), target.current);
        else setError("Không nghe rõ lời nói. Hãy ghi lại.");
      }
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(
          cause instanceof ConversationError
            ? cause.message
            : "Không nhận dạng được giọng nói. Hãy thử lại.",
        );
    } finally {
      if (abort.current === controller) {
        abort.current = null;
        busy.current = false;
        setState("idle");
      }
    }
  }

  async function start(forTarget: string | undefined) {
    // The ref, not state, guards the window while the microphone is opening.
    if (busy.current) return;
    busy.current = true;
    const attempt = ++generation.current;
    setError("");
    target.current = forTarget;
    let opened: MicRecorder;
    try {
      opened = await MicRecorder.open(() => undefined);
    } catch (cause) {
      if (attempt === generation.current) {
        busy.current = false;
        setError(
          cause instanceof MicError ? cause.message : "Không mở được micro.",
        );
      }
      return;
    }
    if (attempt !== generation.current) {
      // Cancelled while opening: release the microphone immediately.
      void opened.stop();
      return;
    }
    recorder.current = opened;
    setState("recording");
    timer.current = setTimeout(() => void stop(), DICTATION_MAX_MS);
  }

  /** Drop the recording or pending transcript, e.g. when switching chats. */
  const cancel = useCallback(() => {
    generation.current += 1;
    busy.current = false;
    clearTimeout(timer.current);
    abort.current?.abort();
    abort.current = null;
    const active = recorder.current;
    recorder.current = null;
    void active?.stop();
    setState("idle");
  }, []);

  return { state, error, clearError: () => setError(""), start, stop, cancel };
}

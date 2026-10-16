import { useCallback, useEffect, useRef, useState } from "react";
import {
  assertMicAvailable,
  micErrorFrom,
  MicRecorder,
} from "@/services/mic-recorder";
import { recordSocketUrl, uploadRecording } from "@/services/voice-api";

export interface LiveTurn {
  /** Same T01, T02 … numbering the server gives the saved transcript. */
  id: string;
  /** Null until diarization has labelled this stretch of audio. */
  speaker: string | null;
  text: string;
  start: number | null;
  end: number | null;
}

export interface LiveTranscript {
  turns: LiveTurn[];
  /** Words of the utterance in progress; its speaker is known only once final. */
  partial: string;
}

export type ServerEvent =
  | { type: "ready"; audio_file: string }
  | { type: "partial"; text: string }
  /** Every turn so far; speakers may be relabelled as more audio is diarized. */
  | { type: "turns"; turns: LiveTurn[] }
  | { type: "recognition_lost" }
  | { type: "saving" }
  | { type: "saved"; conversation_id: string }
  | {
      type: "error";
      code: string;
      message: string;
      audio_file?: string | null;
    };

export const emptyTranscript: LiveTranscript = { turns: [], partial: "" };

/** The server sends whole turn lists, so the live view matches the saved ids. */
export function applyTranscriptEvent(
  state: LiveTranscript,
  event: ServerEvent,
): LiveTranscript {
  if (event.type === "partial")
    return { ...state, partial: state.partial + event.text };
  if (event.type !== "turns") return state;
  // Later lists only add text, so a partial ends once a list grows.
  const grew =
    event.turns.map((turn) => turn.text).join(" ").length >
    state.turns.map((turn) => turn.text).join(" ").length;
  return { turns: event.turns, partial: grew ? "" : state.partial };
}

const SERVER_ERRORS: Record<string, string> = {
  speech_unavailable:
    "Dịch vụ nhận dạng giọng nói chưa sẵn sàng. Kiểm tra speech-api rồi thử lại.",
  kb: "Knowledge base này không hỗ trợ ghi âm.",
  start: "Thông tin bắt đầu ghi âm không hợp lệ.",
  empty: "Không nhận ra lời nói nào trong bản ghi.",
  server: "Máy chủ không lưu được cuộc hội thoại.",
};

export function serverErrorMessage(event: {
  code: string;
  audio_file?: string | null;
}) {
  const message = SERVER_ERRORS[event.code] ?? "Máy chủ báo lỗi khi ghi âm.";
  return event.audio_file
    ? `${message} File âm thanh vẫn được giữ: ${event.audio_file}.`
    : message;
}

export type RecordingPhase =
  "idle" | "starting" | "recording" | "saving" | "failed";

export interface StartOptions {
  kb: string;
  name: string;
  clean: boolean;
}

interface Session {
  options: StartOptions;
  mic: MicRecorder;
  socket: WebSocket | null;
  sent: number;
  ready: boolean;
  recordedAt: Date;
  saved?: (id: string) => void;
  failed?: (message: string, canResend?: boolean) => void;
}

const SAVE_TIMEOUT_MS = 180_000;

/** The browser's copy of a recording whose save failed. */
export interface RecordingBackup {
  options: StartOptions;
  recordedAt: Date;
  audio: Blob;
  /** False when the server already judged the audio (e.g. no speech in it). */
  canResend: boolean;
}

class DefinitiveError extends Error {}

/**
 * Records the microphone and streams it to `/v1/voice/{kb}/record`.
 *
 * Audio captured before the socket is ready, or after it drops, is kept in
 * the browser; if the socket is gone at stop the whole WAV is uploaded, so
 * nothing said between Start and Stop is lost.
 */
export function useVoiceRecording(onSaved: (conversationId: string) => void) {
  const [phase, setPhase] = useState<RecordingPhase>("idle");
  const [transcript, setTranscript] = useState(emptyTranscript);
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0);
  const [online, setOnline] = useState(true);
  const [recognitionLost, setRecognitionLost] = useState(false);
  const [error, setError] = useState("");
  const [savingNote, setSavingNote] = useState("");
  const [backup, setBackup] = useState<RecordingBackup | null>(null);
  const session = useRef<Session | null>(null);
  const wakeLock = useRef<WakeLockSentinel | null>(null);
  const savedCallback = useRef(onSaved);
  useEffect(() => {
    savedCallback.current = onSaved;
  }, [onSaved]);

  const releaseWakeLock = () => {
    void wakeLock.current?.release().catch(() => undefined);
    wakeLock.current = null;
  };

  const sendPending = (current: Session) => {
    const socket = current.socket;
    if (!current.ready || !socket || socket.readyState !== WebSocket.OPEN)
      return;
    while (current.sent < current.mic.chunks.length)
      socket.send(current.mic.chunks[current.sent++]);
  };

  const teardown = useCallback(async () => {
    const current = session.current;
    session.current = null;
    releaseWakeLock();
    if (!current) return;
    if (current.socket && current.socket.readyState <= WebSocket.OPEN)
      current.socket.close();
    await current.mic.stop().catch(() => undefined);
  }, []);

  useEffect(() => () => void teardown(), [teardown]);

  useEffect(() => {
    if (phase !== "recording") return;
    const timer = setInterval(() => {
      const current = session.current;
      if (current)
        setSeconds(
          Math.floor(current.mic.sampleCount / current.mic.sampleRate),
        );
    }, 250);
    return () => clearInterval(timer);
  }, [phase]);

  const start = useCallback(async (options: StartOptions) => {
    if (session.current) return;
    setPhase("starting");
    setError("");
    setTranscript(emptyTranscript);
    setSeconds(0);
    setOnline(true);
    setRecognitionLost(false);
    setSavingNote("");
    setBackup(null);
    let mic: MicRecorder | undefined;
    try {
      assertMicAvailable();
      mic = await MicRecorder.open(({ level: value }) => {
        setLevel(value);
        if (session.current) sendPending(session.current);
      });
      const current: Session = {
        options,
        mic,
        socket: null,
        sent: 0,
        ready: false,
        recordedAt: new Date(),
      };
      session.current = current;
      await connectSocket(current);
      sendPending(current);
      setPhase("recording");
      wakeLock.current =
        (await navigator.wakeLock?.request("screen").catch(() => null)) ?? null;
    } catch (reason) {
      session.current = null;
      await mic?.stop().catch(() => undefined);
      setError(
        reason instanceof Error && !(reason instanceof DOMException)
          ? reason.message
          : micErrorFrom(reason).message,
      );
      setPhase("failed");
      setLevel(0);
    }
  }, []);

  function connectSocket(current: Session) {
    return new Promise<void>((resolve, reject) => {
      const socket = new WebSocket(recordSocketUrl(current.options.kb));
      socket.binaryType = "arraybuffer";
      current.socket = socket;
      let opened = false;
      const timer = setTimeout(() => {
        reject(
          new Error("Máy chủ không phản hồi. Kiểm tra kết nối rồi thử lại."),
        );
        socket.close();
      }, 15_000);
      socket.onopen = () =>
        socket.send(
          JSON.stringify({
            type: "start",
            name: current.options.name,
            clean: current.options.clean,
            sample_rate: current.mic.sampleRate,
          }),
        );
      socket.onmessage = (message: MessageEvent<string>) => {
        let event: ServerEvent;
        try {
          event = JSON.parse(message.data) as ServerEvent;
        } catch {
          return;
        }
        if (event.type === "ready") {
          opened = true;
          current.ready = true;
          clearTimeout(timer);
          resolve();
        } else if (event.type === "partial" || event.type === "turns")
          setTranscript((state) => applyTranscriptEvent(state, event));
        else if (event.type === "recognition_lost") setRecognitionLost(true);
        else if (event.type === "saving")
          setSavingNote("Đang chờ nhận dạng câu cuối…");
        else if (event.type === "saved") current.saved?.(event.conversation_id);
        else if (event.type === "error") {
          const text = serverErrorMessage(event);
          if (!opened) {
            clearTimeout(timer);
            reject(new Error(text));
          } else current.failed?.(text, event.code !== "empty");
        }
      };
      socket.onclose = () => {
        clearTimeout(timer);
        if (!opened) {
          reject(
            new Error(
              "Không kết nối được máy chủ ghi âm. Kiểm tra backend rồi thử lại.",
            ),
          );
          return;
        }
        current.ready = false;
        setOnline(false);
        current.failed?.(
          "Mất kết nối trước khi máy chủ xác nhận đã lưu. File âm thanh trên máy chủ (nếu có) vẫn được giữ.",
        );
      };
    });
  }

  const stop = useCallback(async () => {
    const current = session.current;
    if (!current) return;
    setPhase("saving");
    setLevel(0);
    releaseWakeLock();
    await current.mic.stop();
    setSeconds(Math.floor(current.mic.sampleCount / current.mic.sampleRate));
    const socket = current.socket;
    try {
      let conversationId: string;
      if (current.ready && socket?.readyState === WebSocket.OPEN) {
        sendPending(current);
        conversationId = await new Promise<string>((resolve, reject) => {
          const timer = setTimeout(
            () =>
              reject(
                new Error(
                  "Máy chủ lưu quá lâu. Mở danh sách hội thoại để kiểm tra lại.",
                ),
              ),
            SAVE_TIMEOUT_MS,
          );
          current.saved = (id) => {
            clearTimeout(timer);
            resolve(id);
          };
          current.failed = (message, canResend = true) => {
            clearTimeout(timer);
            reject(
              canResend ? new Error(message) : new DefinitiveError(message),
            );
          };
          socket.send(JSON.stringify({ type: "stop" }));
        });
      } else {
        // The live connection dropped: send the copy kept in the browser.
        setSavingNote("Đang gửi bản ghi từ trình duyệt và nhận dạng lại…");
        socket?.close();
        conversationId = await uploadRecording(current.options.kb, {
          name: current.options.name,
          clean: current.options.clean,
          recordedAt: current.recordedAt,
          audio: current.mic.toWav(),
        });
      }
      session.current = null;
      socket?.close();
      savedCallback.current(conversationId);
    } catch (reason) {
      session.current = null;
      socket?.close();
      setBackup({
        options: current.options,
        recordedAt: current.recordedAt,
        audio: current.mic.toWav(),
        canResend: !(reason instanceof DefinitiveError),
      });
      setError(reason instanceof Error ? reason.message : String(reason));
      setPhase("failed");
    }
  }, []);

  const resend = useCallback(async () => {
    if (!backup) return;
    setPhase("saving");
    setError("");
    setSavingNote("Đang gửi bản ghi từ trình duyệt và nhận dạng lại…");
    try {
      const id = await uploadRecording(backup.options.kb, {
        name: backup.options.name,
        clean: backup.options.clean,
        recordedAt: backup.recordedAt,
        audio: backup.audio,
      });
      setBackup(null);
      savedCallback.current(id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setPhase("failed");
    }
  }, [backup]);

  const reset = useCallback(() => {
    void teardown();
    setPhase("idle");
    setError("");
    setBackup(null);
    setTranscript(emptyTranscript);
    setSeconds(0);
  }, [teardown]);

  return {
    phase,
    transcript,
    seconds,
    level,
    online,
    recognitionLost,
    error,
    savingNote,
    backup,
    start,
    stop,
    resend,
    reset,
  };
}

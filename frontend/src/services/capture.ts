import type { Source, TranscriptTurn } from "@/types";
import {
  ax167,
  conversationTurns,
  SOURCE_IDS,
  type SpeakerId,
} from "@/fixtures/ax167";

export const MAX_FILE_SIZE = 20 * 1024 * 1024;
export const ACCEPTED_FILES = ".pdf,.txt,.md,.docx";
export function validateFile(file: File): string | undefined {
  if (!/\.(pdf|txt|md|docx)$/i.test(file.name))
    return "Chọn tệp PDF, TXT, MD hoặc DOCX.";
  if (file.size > MAX_FILE_SIZE)
    return "Tệp vượt quá 20 MB. Hãy chọn tệp nhỏ hơn.";
  if (!file.size) return "Tệp trống. Hãy chọn tệp có nội dung.";
}

export async function prepareUpload(file: File): Promise<Source> {
  const error = validateFile(file);
  if (error) throw new Error(error);
  const isText = /\.(txt|md)$/i.test(file.name);
  return {
    id: crypto.randomUUID(),
    title: file.name.replace(/\.[^.]+$/, ""),
    kind: "document",
    status: "draft",
    origin: "upload",
    createdAt: new Date().toISOString(),
    machines: [],
    summary: "",
    file,
    fileName: file.name,
    fileType: /\.pdf$/i.test(file.name) ? "application/pdf" : file.type,
    fileSize: file.size,
    text: isText ? await file.text() : undefined,
  };
}

// ---- Simulated speech-to-text with speaker labels -------------------------
// The recording is a pure function of a tick counter. One tick is one word at
// speed 1; the page advances the tick with an interval scaled by `?speed=`.
export const TICK_MS = 55;
const LEAD_TICKS = 10;
const GAP_TICKS = 9;

interface Segment {
  turn: TranscriptTurn;
  words: string[];
  start: number;
}

const segments: Segment[] = (() => {
  let tick = LEAD_TICKS;
  return conversationTurns.map((turn) => {
    const words = turn.text.split(" ");
    const segment = {
      turn: { ...turn, seconds: Math.floor((tick * TICK_MS) / 1000) },
      words,
      start: tick,
    };
    tick += words.length + GAP_TICKS;
    return segment;
  });
})();

export const TOTAL_TICKS = (() => {
  const last = segments.at(-1)!;
  return last.start + last.words.length;
})();

/** T01–T14 with the same timestamps the live recording shows. */
export const recordedTurns: TranscriptTurn[] = segments.map((s) => s.turn);

export interface LiveTurn extends TranscriptTurn {
  streaming: boolean;
}
export interface RecordingView {
  turns: LiveTurn[];
  completed: TranscriptTurn[];
  active?: SpeakerId;
  complete: boolean;
  seconds: number;
}

export function recordingView(tick: number): RecordingView {
  const turns: LiveTurn[] = [];
  let active: SpeakerId | undefined;
  for (const segment of segments) {
    if (tick < segment.start) break;
    const shown = Math.min(segment.words.length, tick - segment.start + 1);
    const streaming = shown < segment.words.length;
    if (streaming) active = segment.turn.speakerId as SpeakerId;
    turns.push({
      ...segment.turn,
      text: segment.words.slice(0, shown).join(" "),
      streaming,
    });
  }
  return {
    turns,
    completed: turns
      .filter((turn) => !turn.streaming)
      .map((turn) => ({
        id: turn.id,
        code: turn.code,
        speakerId: turn.speakerId,
        speaker: turn.speaker,
        role: turn.role,
        text: turn.text,
        seconds: turn.seconds,
      })),
    active,
    complete: tick >= TOTAL_TICKS,
    seconds: Math.floor((Math.min(tick, TOTAL_TICKS) * TICK_MS) / 1000),
  };
}

export function recordingSource(tick: number): Source {
  const view = recordingView(tick);
  return {
    id: SOURCE_IDS.conversation,
    title: "Ghi âm trao đổi · MR-JE-AX167 tiếng mài trục Z",
    kind: "conversation",
    origin: "recording",
    status: "draft",
    createdAt: new Date().toISOString(),
    machines: [ax167],
    summary: view.complete
      ? "Junior technician and senior expert discuss a grinding noise on the Z-axis servo motor of MR-JE-AX167 (Line 11, machining cell B)."
      : "",
    turns: view.completed,
  };
}

export function timestamp(seconds: number) {
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

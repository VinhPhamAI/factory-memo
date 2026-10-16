/**
 * Microphone capture as 16-bit PCM mono, the format the speech service takes.
 *
 * An AudioWorklet converts samples off the main thread and posts ~80 ms
 * chunks. Every chunk is also kept in memory, so the whole recording can be
 * sent as a WAV file if the live connection drops.
 */

export const TARGET_SAMPLE_RATE = 16_000;
const CHUNK_MS = 80;

const WORKLET = `
class PcmCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.size = options.processorOptions.chunkSize;
    this.buffer = new Int16Array(this.size);
    this.length = 0;
    this.energy = 0;
    this.port.onmessage = (event) => {
      if (event.data === "flush") {
        this.flush();
        this.port.postMessage({ type: "flushed" });
      }
    };
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      const sample = Math.max(-1, Math.min(1, channel[i]));
      this.buffer[this.length++] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      this.energy += sample * sample;
      if (this.length === this.size) this.flush();
    }
    return true;
  }
  flush() {
    if (!this.length) return;
    const pcm = this.buffer.slice(0, this.length);
    const rms = Math.sqrt(this.energy / this.length);
    this.length = 0;
    this.energy = 0;
    this.port.postMessage({ type: "pcm", pcm: pcm.buffer, rms }, [pcm.buffer]);
  }
}
registerProcessor("pcm-capture", PcmCapture);
`;

export type MicErrorKind =
  "insecure" | "unsupported" | "denied" | "not-found" | "busy" | "unknown";

export class MicError extends Error {
  constructor(
    readonly kind: MicErrorKind,
    message: string,
  ) {
    super(message);
  }
}

export function micErrorFrom(error: unknown): MicError {
  if (error instanceof MicError) return error;
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError")
    return new MicError(
      "denied",
      "Trình duyệt chưa cho phép dùng micro. Bấm biểu tượng ổ khóa trên thanh địa chỉ, cho phép Micro rồi thử lại.",
    );
  if (name === "NotFoundError" || name === "OverconstrainedError")
    return new MicError(
      "not-found",
      "Không tìm thấy micro. Cắm micro hoặc tai nghe rồi thử lại.",
    );
  if (name === "NotReadableError" || name === "AbortError")
    return new MicError(
      "busy",
      "Micro đang được ứng dụng khác sử dụng. Đóng ứng dụng đó rồi thử lại.",
    );
  return new MicError("unknown", "Không mở được micro. Hãy thử lại.");
}

/** Throws a MicError when this page can never use the microphone. */
export function assertMicAvailable() {
  if (!window.isSecureContext)
    throw new MicError(
      "insecure",
      "Micro chỉ hoạt động khi trang mở qua HTTPS hoặc localhost. Hãy mở ứng dụng bằng địa chỉ HTTPS.",
    );
  if (!navigator.mediaDevices?.getUserMedia || !("AudioWorkletNode" in window))
    throw new MicError(
      "unsupported",
      "Trình duyệt này không hỗ trợ ghi âm. Hãy dùng Chrome, Edge, Firefox hoặc Safari bản mới.",
    );
}

export interface MicChunk {
  pcm: Int16Array<ArrayBuffer>;
  /** Root-mean-square level of the chunk, 0–1. */
  level: number;
}

export class MicRecorder {
  readonly chunks: Int16Array<ArrayBuffer>[] = [];
  private flushed?: () => void;

  private constructor(
    private readonly stream: MediaStream,
    private readonly context: AudioContext,
    private readonly source: MediaStreamAudioSourceNode,
    private readonly node: AudioWorkletNode,
  ) {}

  get sampleRate() {
    return this.context.sampleRate;
  }

  get sampleCount() {
    return this.chunks.reduce((total, chunk) => total + chunk.length, 0);
  }

  static async open(onChunk: (chunk: MicChunk) => void): Promise<MicRecorder> {
    assertMicAvailable();
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (error) {
      throw micErrorFrom(error);
    }
    try {
      const { context, source } = await connect(stream);
      const url = URL.createObjectURL(
        new Blob([WORKLET], { type: "text/javascript" }),
      );
      try {
        await context.audioWorklet.addModule(url);
      } finally {
        URL.revokeObjectURL(url);
      }
      const node = new AudioWorkletNode(context, "pcm-capture", {
        numberOfInputs: 1,
        numberOfOutputs: 0,
        channelCount: 1,
        processorOptions: {
          chunkSize: Math.round((context.sampleRate * CHUNK_MS) / 1000),
        },
      });
      const recorder = new MicRecorder(stream, context, source, node);
      node.port.onmessage = (event: MessageEvent) => {
        const data = event.data as
          { type: "pcm"; pcm: ArrayBuffer; rms: number } | { type: "flushed" };
        if (data.type === "flushed") {
          recorder.flushed?.();
          return;
        }
        const pcm = new Int16Array(data.pcm);
        recorder.chunks.push(pcm);
        onChunk({ pcm, level: Math.min(1, data.rms * 4) });
      };
      source.connect(node);
      return recorder;
    } catch (error) {
      stream.getTracks().forEach((track) => track.stop());
      throw error instanceof MicError
        ? error
        : new MicError(
            "unknown",
            "Không khởi động được bộ ghi âm. Hãy thử lại.",
          );
    }
  }

  /** Delivers the last partial chunk, then releases the microphone. */
  async stop() {
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, 600);
      this.flushed = () => {
        clearTimeout(timer);
        resolve();
      };
      this.node.port.postMessage("flush");
    });
    this.source.disconnect();
    this.node.port.onmessage = null;
    this.stream.getTracks().forEach((track) => track.stop());
    await this.context.close().catch(() => undefined);
  }

  /** The whole recording so far as a WAV file. */
  toWav(): Blob {
    return encodeWav(this.chunks, this.sampleRate);
  }
}

async function connect(stream: MediaStream) {
  // Recording at 16 kHz directly avoids resampling; some browsers cannot
  // connect a microphone to a context whose rate differs from the device's.
  try {
    const context = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
    try {
      return { context, source: context.createMediaStreamSource(stream) };
    } catch (error) {
      await context.close();
      throw error;
    }
  } catch {
    const context = new AudioContext();
    return { context, source: context.createMediaStreamSource(stream) };
  }
}

export function encodeWav(
  chunks: Int16Array<ArrayBuffer>[],
  sampleRate: number,
): Blob {
  const dataBytes = chunks.reduce(
    (total, chunk) => total + chunk.byteLength,
    0,
  );
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  const text = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++)
      view.setUint8(offset + i, value.charCodeAt(i));
  };
  text(0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, dataBytes, true);
  return new Blob([header, ...chunks], { type: "audio/wav" });
}

import { useEffect, useRef, useState } from "react";
import { useBlocker, useLocation, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  AudioLines,
  CircleCheck,
  FileText,
  HardHat,
  Mic,
  Pause,
  Play,
  Square,
} from "lucide-react";
import { useWorkspace } from "@/hooks/workspace";
import {
  recordingSource,
  recordingView,
  TICK_MS,
  TOTAL_TICKS,
  timestamp,
} from "@/services/capture";
import { scaled } from "@/services/timing";
import { speakers, type SpeakerId } from "@/fixtures/ax167";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/utils/cn";

type Phase = "idle" | "recording" | "paused" | "done";

function Waveform({ active }: { active: boolean }) {
  return (
    <span className={cn("waveform", active && "active")} aria-hidden="true">
      {Array.from({ length: 28 }, (_, i) => (
        <span key={i} style={{ animationDelay: `${(i * 97) % 700}ms` }} />
      ))}
    </span>
  );
}

function SpeakerPanel({
  id,
  active,
  live,
}: {
  id: SpeakerId;
  active: boolean;
  live: boolean;
}) {
  const speaker = speakers[id];
  return (
    <div
      className={cn("speaker-panel", active && "active", id)}
      data-testid={`speaker-${id}`}
      data-active={active}
    >
      <span className="speaker-panel-avatar">
        <HardHat size={26} strokeWidth={1.6} />
        <span>{speaker.initials}</span>
      </span>
      <span className="speaker-panel-text">
        <strong>
          {speaker.label} · {speaker.name}
        </strong>
        <span>{speaker.role}</span>
        <em>{active ? "Đang nói…" : live ? "Đang nghe" : "Sẵn sàng"}</em>
      </span>
      <Waveform active={active} />
    </div>
  );
}

export function RecordPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const workspace = useWorkspace();
  const [phase, setPhase] = useState<Phase>(() =>
    location.state?.autoStart ? "recording" : "idle",
  );
  const [tick, setTick] = useState(0);
  const [saving, setSaving] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const view = recordingView(tick);
  const live = phase === "recording" || phase === "paused";
  const blocker = useBlocker(live);

  useEffect(() => {
    if (phase !== "recording") return;
    const timer = setInterval(
      () => setTick((value) => Math.min(value + 1, TOTAL_TICKS)),
      scaled(TICK_MS),
    );
    return () => clearInterval(timer);
  }, [phase]);
  useEffect(() => {
    if (!live) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [live]);
  const lastText = view.turns.at(-1)?.text;
  useEffect(() => {
    if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [lastText, view.turns.length]);

  async function finish() {
    setSaving(true);
    try {
      await workspace.saveSource(recordingSource(tick));
      setPhase("done");
    } finally {
      setSaving(false);
    }
  }

  const active = phase === "recording" ? view.active : undefined;
  return (
    <>
      <main className="record-page">
        <header className="record-topline">
          <div className="record-topline-left">
            <span
              className={cn("rec-indicator", phase)}
              data-testid="record-state"
              data-state={phase}
            >
              <span className="rec-dot" />
              {phase === "done"
                ? "ĐÃ LƯU"
                : phase === "paused"
                  ? "TẠM DỪNG"
                  : "REC"}
            </span>
            <span className="record-clock" data-testid="record-timer">
              {timestamp(view.seconds)}
            </span>
            <span className="machine-chip">MR-JE-AX167 · Line 11</span>
          </div>
          <div className="record-topline-right">
            <span className="stt-label">
              STT + nhận diện người nói (mô phỏng)
            </span>
            {live && (
              <>
                <Button
                  variant="outline"
                  data-testid="record-pause"
                  onClick={() =>
                    setPhase(phase === "paused" ? "recording" : "paused")
                  }
                >
                  {phase === "paused" ? (
                    <Play size={15} />
                  ) : (
                    <Pause size={15} />
                  )}
                  {phase === "paused" ? "Tiếp tục" : "Tạm dừng"}
                </Button>
                <Button
                  data-testid="record-finish"
                  className={cn(view.complete && "pulse-cta")}
                  disabled={!view.completed.length || saving}
                  onClick={() => void finish()}
                >
                  <Square size={13} fill="currentColor" />
                  Kết thúc ghi âm
                </Button>
              </>
            )}
          </div>
        </header>
        <div className="page-title">
          <span className="eyebrow">BƯỚC 1 · INPUT GIỌNG NÓI</span>
          <h1>Ghi âm trao đổi giữa chuyên gia và kỹ thuật viên</h1>
        </div>
        <div className="speaker-row">
          <SpeakerPanel
            id="junior_01"
            active={active === "junior_01"}
            live={live}
          />
          <SpeakerPanel
            id="expert_01"
            active={active === "expert_01"}
            live={live}
          />
        </div>
        {phase === "idle" ? (
          <div className="record-idle tech-hero">
            <span className="record-idle-icon">
              <Mic size={36} strokeWidth={1.5} />
            </span>
            <h2>Sẵn sàng ghi âm tại Line 11 · Machining cell B</h2>
            <p>
              Factory Memo chuyển giọng nói thành văn bản và tách lời từng người
              nói.
              <br />
              Bản demo phát lại cuộc trao đổi thật của case MR-JE-AX167, không
              dùng micro.
            </p>
            <Button
              size="lg"
              data-testid="record-start"
              onClick={() => setPhase("recording")}
            >
              <Mic size={18} />
              Bắt đầu ghi âm
            </Button>
          </div>
        ) : (
          <section className="live-transcript" data-testid="live-transcript">
            <div className="live-transcript-header">
              <span>
                <AudioLines size={18} />
                Bản ghi trực tiếp
              </span>
              <span data-testid="record-turn-count">
                {view.completed.length}/14 lượt
              </span>
            </div>
            <div className="live-transcript-scroll" ref={scroll}>
              {!view.turns.length && (
                <div className="listening-placeholder">
                  <AudioLines size={22} />
                  <p>Đang lắng nghe…</p>
                </div>
              )}
              {view.turns.map((turn) => (
                <div
                  key={turn.id}
                  className={cn(
                    "live-turn",
                    turn.speakerId === "expert_01" && "expert",
                    turn.streaming && "streaming",
                  )}
                  data-testid={`turn-${turn.code}`}
                  data-streaming={turn.streaming}
                >
                  <span className="turn-chip">{turn.code}</span>
                  <div className="live-turn-body">
                    <div className="turn-meta">
                      <strong>
                        {turn.speaker} · {turn.speakerId}
                      </strong>
                      <span>{timestamp(turn.seconds)}</span>
                    </div>
                    <p>
                      {turn.text}
                      {turn.streaming && phase === "recording" && (
                        <span className="stream-cursor" />
                      )}
                    </p>
                  </div>
                </div>
              ))}
            </div>
            {phase === "done" ? (
              <div className="record-done" data-testid="record-done">
                <span className="record-done-icon">
                  <CircleCheck size={22} />
                </span>
                <div>
                  <strong>Đã lưu bản ghi vào thư viện</strong>
                  <span>
                    {view.completed.length} lượt · 2 người nói ·{" "}
                    {timestamp(view.seconds)} ·{" "}
                    <span className="status-badge draft">Nháp</span>
                  </span>
                </div>
                <Button
                  variant="outline"
                  onClick={() => void navigate("/sources")}
                >
                  <FileText size={15} />
                  Thêm tài liệu
                </Button>
                <Button
                  data-testid="record-analyze-cta"
                  onClick={() => void navigate("/analyze")}
                >
                  Phân tích bằng AI
                  <ArrowRight size={16} />
                </Button>
              </div>
            ) : (
              <div className="record-controls">
                <span>
                  {view.complete ? (
                    <>
                      <CircleCheck size={16} />
                      Đã nhận đủ 14 lượt nói. Bấm “Kết thúc ghi âm” để lưu.
                    </>
                  ) : (
                    <>
                      <AudioLines size={16} />
                      Transcript cập nhật theo từng lượt nói
                    </>
                  )}
                </span>
              </div>
            )}
          </section>
        )}
      </main>
      <Dialog
        open={blocker.state === "blocked"}
        onOpenChange={(open) => {
          if (!open && blocker.state === "blocked") blocker.reset();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Bản ghi âm vẫn đang mở</DialogTitle>
            <DialogDescription>
              Kết thúc ghi âm để lưu vào thư viện trước khi rời trang.
            </DialogDescription>
          </DialogHeader>
          <div className="leave-actions">
            <Button
              variant="outline"
              onClick={() => {
                if (blocker.state === "blocked") blocker.reset();
              }}
            >
              Tiếp tục ghi
            </Button>
            <Button
              disabled={!view.completed.length}
              onClick={() => {
                if (blocker.state === "blocked") blocker.reset();
                void finish();
              }}
            >
              Kết thúc và lưu
            </Button>
            <Button
              variant="ghost"
              className="destructive-text"
              onClick={() => {
                if (blocker.state === "blocked") blocker.proceed();
              }}
            >
              Bỏ bản ghi và rời trang
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

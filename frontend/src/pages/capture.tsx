import { useEffect, useRef, useState } from "react";
import { Link, useBlocker, useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  ChevronRight,
  Download,
  Loader2,
  Mic,
  RotateCcw,
  Square,
  WifiOff,
} from "lucide-react";
import type { KnowledgeBase } from "@/types";
import { useVoiceRecording, type LiveTurn } from "@/hooks/voice-recording";
import {
  listCaptureKnowledgeBases,
  listConversations,
  type VoiceConversationSummary,
} from "@/services/voice-api";
import {
  clock,
  dateTime,
  defaultConversationName,
} from "@/services/voice-display";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { LevelMeter, SpeakerBadge } from "@/components/voice/speaker-badge";
import { cn } from "@/utils/cn";

const KB_KEY = "voice-capture:kb";
const CLEAN_KEY = "voice-capture:clean";

function readSetting(key: string) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function writeSetting(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Settings are a convenience; private windows may refuse storage.
  }
}

export function CapturePage() {
  const navigate = useNavigate();
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [kbState, setKbState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [kbReload, setKbReload] = useState(0);
  const [kb, setKb] = useState(() => readSetting(KB_KEY) ?? "");
  const [name, setName] = useState(() => defaultConversationName());
  const [clean, setClean] = useState(() => readSetting(CLEAN_KEY) !== "false");
  // Set just before navigating to the saved conversation, which the blocker allows.
  const saved = useRef(false);
  const recording = useVoiceRecording((id) => {
    saved.current = true;
    void navigate(`/capture/${encodeURIComponent(kb)}/${id}`);
  });
  const live = recording.phase === "recording" || recording.phase === "saving";
  const blocker = useBlocker(() => live && !saved.current);

  useEffect(() => {
    const abort = new AbortController();
    setKbState("loading");
    listCaptureKnowledgeBases(abort.signal)
      .then((items) => {
        setKnowledgeBases(items);
        setKb((current) =>
          items.some((item) => item.name === current)
            ? current
            : (items[0]?.name ?? ""),
        );
        setKbState("ready");
      })
      .catch(() => {
        if (!abort.signal.aborted) setKbState("error");
      });
    return () => abort.abort();
  }, [kbReload]);

  useEffect(() => {
    if (live) saved.current = false;
  }, [live]);

  useEffect(() => {
    if (!live) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [live]);

  const trimmedName = name.trim();
  const canStart =
    kbState === "ready" &&
    !!kb &&
    !!trimmedName &&
    recording.phase !== "starting";

  function start() {
    if (!canStart) return;
    writeSetting(KB_KEY, kb);
    writeSetting(CLEAN_KEY, String(clean));
    void recording.start({ kb, name: trimmedName, clean });
  }

  if (recording.phase === "recording" || recording.phase === "saving")
    return (
      <>
        <RecordingView
          name={trimmedName}
          recording={recording}
          onStop={() => void recording.stop()}
        />
        <LeaveDialog
          open={blocker.state === "blocked"}
          saving={recording.phase === "saving"}
          onStay={() => blocker.state === "blocked" && blocker.reset()}
          onStop={() => {
            if (blocker.state === "blocked") blocker.reset();
            void recording.stop();
          }}
          onLeave={() => blocker.state === "blocked" && blocker.proceed()}
        />
      </>
    );

  return (
    <main className="vc-page">
      <div className="vc-start">
        <header className="vc-heading">
          <h1>Ghi âm hội thoại</h1>
          <p>
            Ghi lại trao đổi tại hiện trường. Khi bạn bấm <strong>Dừng</strong>,
            bản ghi được lưu và AI trích xuất tri thức để bạn duyệt.
          </p>
        </header>

        <form
          className="vc-start-card"
          onSubmit={(event) => {
            event.preventDefault();
            start();
          }}
        >
          <label className="vc-field">
            <span>Tên cuộc hội thoại</span>
            <input
              value={name}
              maxLength={120}
              onChange={(event) => setName(event.target.value)}
              placeholder="VD: Lỗi rung trục Z máy AX167"
              data-testid="capture-name"
            />
            <small>Tên này cũng được dùng để đặt tên file âm thanh.</small>
          </label>

          <label className="vc-field">
            <span>Lưu vào knowledge base</span>
            {kbState === "error" ? (
              <span className="vc-inline-error" role="alert">
                Không tải được danh sách knowledge base.{" "}
                <button
                  type="button"
                  className="link-button"
                  onClick={() => setKbReload((value) => value + 1)}
                >
                  Thử lại
                </button>
              </span>
            ) : (
              <select
                value={kb}
                disabled={kbState !== "ready" || !knowledgeBases.length}
                onChange={(event) => setKb(event.target.value)}
                data-testid="capture-kb"
              >
                {kbState === "loading" && <option>Đang tải…</option>}
                {knowledgeBases.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name}
                  </option>
                ))}
              </select>
            )}
          </label>

          <label className="vc-toggle">
            <input
              type="checkbox"
              role="switch"
              checked={clean}
              onChange={(event) => setClean(event.target.checked)}
              data-testid="capture-clean"
            />
            <span className="vc-toggle-track" aria-hidden="true" />
            <span className="vc-toggle-text">
              <strong>Làm sạch transcript bằng AI</strong>
              <small>
                Bỏ từ đệm, câu lặp để dễ đọc. Bản gốc vẫn được giữ và là căn cứ
                để trích xuất tri thức.
              </small>
            </span>
          </label>

          {recording.phase === "failed" && (
            <FailureNotice recording={recording} />
          )}

          <Button
            type="submit"
            size="lg"
            className="vc-start-button"
            disabled={!canStart}
            data-testid="capture-start"
          >
            {recording.phase === "starting" ? (
              <>
                <Loader2 className="animate-spin" />
                Đang mở micro…
              </>
            ) : (
              <>
                <Mic />
                Bắt đầu ghi âm
              </>
            )}
          </Button>
        </form>

        {kb && kbState === "ready" && <RecentConversations kb={kb} />}
      </div>
    </main>
  );
}

type Recording = ReturnType<typeof useVoiceRecording>;

function FailureNotice({ recording }: { recording: Recording }) {
  const backup = recording.backup;
  const download = () => {
    if (!backup) return;
    const url = URL.createObjectURL(backup.audio);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${backup.options.name}.wav`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <div className="vc-notice error" role="alert" data-testid="capture-error">
      <AlertTriangle size={18} />
      <div>
        <p>{recording.error}</p>
        {backup && (
          <div className="vc-notice-actions">
            {backup.canResend && (
              <Button size="sm" onClick={() => void recording.resend()}>
                <RotateCcw />
                Gửi lại bản ghi
              </Button>
            )}
            <Button size="sm" variant="outline" onClick={download}>
              <Download />
              Tải file âm thanh
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

function RecordingView({
  name,
  recording,
  onStop,
}: {
  name: string;
  recording: Recording;
  onStop(): void;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const { turns, partial } = recording.transcript;
  const saving = recording.phase === "saving";
  const voices = [...new Set(turns.map((turn) => turn.speaker))].filter(
    (label): label is string => !!label,
  );

  useEffect(() => {
    const element = scroll.current;
    if (element && follow.current) element.scrollTop = element.scrollHeight;
  }, [turns, partial]);

  return (
    <main className="vc-page vc-live" data-testid="capture-live">
      <header className="vc-live-bar">
        <div className="vc-live-status">
          <span
            className={cn("vc-rec", saving && "saving")}
            data-testid="capture-state"
            data-state={recording.phase}
          >
            <span className="vc-rec-dot" />
            {saving ? "Đang lưu" : "Đang ghi"}
          </span>
          <span className="vc-clock" data-testid="capture-clock">
            {clock(recording.seconds)}
          </span>
          <LevelMeter level={recording.level} active={!saving} />
        </div>
        <div className="vc-live-title">
          <strong title={name}>{name}</strong>
          <span>
            {voices.length
              ? `${voices.length} người nói · ${turns.length} lượt`
              : "Chưa nhận ra lời nói"}
          </span>
        </div>
        <Button
          size="lg"
          variant="destructive"
          className="vc-stop"
          disabled={saving}
          onClick={onStop}
          data-testid="capture-stop"
        >
          {saving ? (
            <Loader2 className="animate-spin" />
          ) : (
            <Square fill="currentColor" />
          )}
          {saving ? "Đang lưu…" : "Dừng & lưu"}
        </Button>
      </header>

      {(!recording.online || recording.recognitionLost) && !saving && (
        <div className="vc-notice warn" role="status">
          <WifiOff size={18} />
          <p>
            {!recording.online
              ? "Mất kết nối máy chủ. Micro vẫn đang ghi trên thiết bị này; bản ghi đầy đủ sẽ được gửi khi bạn bấm Dừng."
              : "Tạm mất nhận dạng trực tiếp. Âm thanh vẫn được ghi đầy đủ; transcript sẽ được tạo lại từ file khi bạn bấm Dừng."}
          </p>
        </div>
      )}

      <section className="vc-live-transcript" aria-label="Transcript trực tiếp">
        <div
          className="vc-live-scroll"
          ref={scroll}
          onScroll={(event) => {
            const element = event.currentTarget;
            follow.current =
              element.scrollHeight - element.scrollTop - element.clientHeight <
              80;
          }}
        >
          {!turns.length && !partial && (
            <div className="vc-listening">
              <Mic size={22} />
              <p>Đang nghe… Hãy bắt đầu nói bình thường.</p>
              <small>
                Người nói được gán chữ cái A, B, C… theo giọng. Bạn có thể đặt
                tên sau khi dừng.
              </small>
            </div>
          )}
          {turns.map((turn) => (
            <LiveTurnRow key={turn.id} turn={turn} />
          ))}
          {partial && (
            <div className="vc-turn partial" aria-hidden="true">
              <span className="vc-turn-id" />
              <span className="vc-speaker compact" data-slot="pending">
                <span className="vc-speaker-letter">…</span>
              </span>
              <p>
                {partial}
                <span className="stream-cursor" />
              </p>
            </div>
          )}
        </div>
        <footer className="vc-live-foot" aria-live="polite">
          {saving
            ? recording.savingNote || "Đang lưu bản ghi…"
            : "Transcript hiển thị sau mỗi câu nói. Bấm Dừng khi cuộc trao đổi kết thúc."}
        </footer>
      </section>
    </main>
  );
}

function LiveTurnRow({ turn }: { turn: LiveTurn }) {
  return (
    <div className="vc-turn" data-testid={`live-turn-${turn.id}`}>
      <span className="vc-turn-id">{turn.id}</span>
      <SpeakerBadge label={turn.speaker} compact />
      <div className="vc-turn-body">
        <span className="vc-turn-meta">
          Người nói {turn.speaker ?? "?"} · {clock(turn.start)}
        </span>
        <p>{turn.text}</p>
      </div>
    </div>
  );
}

function LeaveDialog({
  open,
  saving,
  onStay,
  onStop,
  onLeave,
}: {
  open: boolean;
  saving: boolean;
  onStay(): void;
  onStop(): void;
  onLeave(): void;
}) {
  return (
    <Dialog open={open} onOpenChange={(value) => !value && onStay()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {saving ? "Bản ghi đang được lưu" : "Bạn đang ghi âm"}
          </DialogTitle>
          <DialogDescription>
            {saving
              ? "Chờ vài giây để bản ghi được lưu xong trước khi rời trang."
              : "Dừng và lưu để không mất cuộc hội thoại. Nếu rời trang bây giờ, file âm thanh vẫn nằm trên máy chủ nhưng không được đưa vào knowledge base."}
          </DialogDescription>
        </DialogHeader>
        <div className="leave-actions">
          <Button variant="outline" onClick={onStay}>
            {saving ? "Ở lại" : "Tiếp tục ghi"}
          </Button>
          {!saving && <Button onClick={onStop}>Dừng và lưu</Button>}
          <Button
            variant="ghost"
            className="destructive-text"
            onClick={onLeave}
          >
            Rời trang
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

const JOB_LABELS: Record<string, string> = {
  queued: "Đang chờ trích xuất",
  running: "Đang trích xuất",
  failed: "Trích xuất lỗi",
  interrupted: "Bị gián đoạn",
};

function RecentConversations({ kb }: { kb: string }) {
  const [items, setItems] = useState<VoiceConversationSummary[]>();
  const [error, setError] = useState("");

  useEffect(() => {
    const abort = new AbortController();
    setItems(undefined);
    setError("");
    listConversations(kb, abort.signal)
      .then(setItems)
      .catch((reason: unknown) => {
        if (!abort.signal.aborted)
          setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => abort.abort();
  }, [kb]);

  return (
    <section className="vc-recent" aria-labelledby="vc-recent-title">
      <h2 id="vc-recent-title">Hội thoại đã ghi</h2>
      {error && <p className="vc-inline-error">{error}</p>}
      {!items && !error && <p className="vc-muted">Đang tải…</p>}
      {items && !items.length && (
        <p className="vc-muted">
          Chưa có cuộc hội thoại nào trong <strong>{kb}</strong>.
        </p>
      )}
      {!!items?.length && (
        <ul>
          {items.map((item) => {
            const pending = item.knowledge.draft ?? 0;
            const reviewed =
              (item.knowledge.approved ?? 0) + (item.knowledge.deprecated ?? 0);
            const job = item.ingestion.status ?? "";
            return (
              <li key={item.id}>
                <Link
                  to={`/capture/${encodeURIComponent(kb)}/${item.id}`}
                  data-testid="recent-conversation"
                >
                  <span className="vc-recent-name">{item.name}</span>
                  <span className="vc-recent-meta">
                    {dateTime(item.recorded_at)} · {clock(item.duration_s)} ·{" "}
                    {item.speakers} người nói
                  </span>
                  <span className="vc-recent-state">
                    {JOB_LABELS[job] ? (
                      <span className={cn("vc-chip", job)}>
                        {JOB_LABELS[job]}
                      </span>
                    ) : pending ? (
                      <span className="vc-chip draft">{pending} chờ duyệt</span>
                    ) : reviewed ? (
                      <span className="vc-chip approved">Đã duyệt xong</span>
                    ) : (
                      <span className="vc-chip">Không có tri thức mới</span>
                    )}
                    <ChevronRight size={16} />
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

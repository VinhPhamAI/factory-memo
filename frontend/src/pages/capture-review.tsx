import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  Circle,
  Loader2,
  Pencil,
  Play,
  RotateCcw,
  Sparkles,
  Undo2,
  X,
  XCircle,
} from "lucide-react";
import {
  audioUrl,
  getConversation,
  renameSpeakers,
  retryExtraction,
  reviewRecord,
  startCleaning,
  type ReviewStatus,
  type Speaker,
  type VoiceConversation,
  type VoiceRecord,
  type VoiceTurn,
} from "@/services/voice-api";
import {
  CERTAINTY_LABELS,
  claimText,
  clock,
  dateTime,
  findQuote,
  pipelineSteps,
  recordEvidence,
  recordScope,
  recordSpeaker,
  recordSteps,
  speakerName,
  stageText,
  STATUS_LABELS,
  turnsBySpeaker,
  TYPE_LABELS,
  type Evidence,
} from "@/services/voice-display";
import { Button } from "@/components/ui/button";
import { SpeakerBadge } from "@/components/voice/speaker-badge";
import { cn } from "@/utils/cn";

const POLL_MS = 1500;
const ACTIVE = new Set(["queued", "running"]);

interface Focus {
  turnId: string;
  quote?: string;
  /** Changes on every click, so the same quote can be shown again. */
  nonce: number;
}

export function CaptureReviewPage() {
  const { kb = "", conversationId = "" } = useParams();
  const [conversation, setConversation] = useState<VoiceConversation>();
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const [focus, setFocus] = useState<Focus>();
  const [mobileTab, setMobileTab] = useState<"transcript" | "knowledge">(
    "knowledge",
  );
  const audio = useRef<HTMLAudioElement>(null);

  const active =
    !!conversation &&
    (ACTIVE.has(conversation.ingestion.status ?? "") ||
      ACTIVE.has(conversation.cleaning.status ?? ""));

  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const next = await getConversation(kb, conversationId, abort.signal);
        setConversation(next);
        setLoadError("");
        if (
          ACTIVE.has(next.ingestion.status ?? "") ||
          ACTIVE.has(next.cleaning.status ?? "")
        )
          timer = setTimeout(() => void load(), POLL_MS);
      } catch (reason) {
        if (abort.signal.aborted) return;
        setLoadError(reason instanceof Error ? reason.message : String(reason));
        // Keep polling through brief network failures while jobs run.
        timer = setTimeout(() => void load(), POLL_MS * 3);
      }
    };
    void load();
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [kb, conversationId, reload]);

  const refresh = useCallback(() => setReload((value) => value + 1), []);

  const playFrom = (seconds: number | null) => {
    const element = audio.current;
    if (!element || seconds == null) return;
    element.currentTime = Math.max(0, seconds - 0.3);
    void element.play().catch(() => undefined);
  };

  const showEvidence = (evidence: Evidence) => {
    if (!evidence.turnId) return;
    setFocus({
      turnId: evidence.turnId,
      quote: evidence.quote,
      nonce: Date.now(),
    });
    setMobileTab("transcript");
  };

  const replaceRecord = (record: VoiceRecord) =>
    setConversation(
      (current) =>
        current && {
          ...current,
          records: current.records.map((item) =>
            item.id === record.id ? record : item,
          ),
        },
    );

  if (!conversation)
    return (
      <main className="vc-page vc-review-loading">
        {loadError ? (
          <div className="vc-notice error" role="alert">
            <AlertTriangle size={18} />
            <div>
              <p>{loadError}</p>
              <div className="vc-notice-actions">
                <Button size="sm" variant="outline" onClick={refresh}>
                  Thử lại
                </Button>
                <Button size="sm" variant="ghost" asChild>
                  <Link to="/capture">Về trang ghi âm</Link>
                </Button>
              </div>
            </div>
          </div>
        ) : (
          <p className="vc-muted">
            <Loader2 className="animate-spin" size={16} /> Đang tải cuộc hội
            thoại…
          </p>
        )}
      </main>
    );

  const counts = countStatuses(conversation.records);

  return (
    <main className="vc-page vc-review" data-testid="capture-review">
      <header className="vc-review-head">
        <Link to="/capture" className="vc-back">
          <ArrowLeft size={16} />
          Ghi âm
        </Link>
        <div className="vc-review-title">
          <h1>{conversation.name}</h1>
          <p>
            {dateTime(conversation.recorded_at ?? conversation.created_at)} ·{" "}
            {conversation.source === "text"
              ? "Văn bản"
              : clock(conversation.audio.duration_s)}{" "}
            · {conversation.speakers.length} người nói ·{" "}
            {conversation.turns.length} lượt · KB{" "}
            <strong>{conversation.kb}</strong>
          </p>
        </div>
        {conversation.source === "text" ? null : conversation.audio
            .available ? (
          <audio
            ref={audio}
            className="vc-audio"
            controls
            preload="metadata"
            src={audioUrl(conversation.kb, conversation.id)}
            data-testid="review-audio"
          />
        ) : (
          <span className="vc-muted">Không tìm thấy file âm thanh</span>
        )}
      </header>

      <Pipeline
        conversation={conversation}
        pending={counts.draft}
        onRetry={async () => {
          await retryExtraction(conversation.kb, conversation.id);
          refresh();
        }}
      />
      {loadError && active && (
        <p className="vc-inline-error" role="status">
          Mất kết nối, đang thử cập nhật lại… ({loadError})
        </p>
      )}

      <div className="vc-mobile-tabs" role="tablist">
        <button
          role="tab"
          aria-selected={mobileTab === "transcript"}
          onClick={() => setMobileTab("transcript")}
        >
          Bản ghi
        </button>
        <button
          role="tab"
          aria-selected={mobileTab === "knowledge"}
          onClick={() => setMobileTab("knowledge")}
        >
          Tri thức
          {conversation.records.length
            ? ` (${conversation.records.length})`
            : ""}
        </button>
      </div>

      <div className="vc-columns" data-tab={mobileTab}>
        <TranscriptPanel
          conversation={conversation}
          focus={focus}
          onPlay={playFrom}
          onRenamed={setConversation}
          onCleaningStarted={refresh}
        />
        <KnowledgePanel
          conversation={conversation}
          counts={counts}
          onEvidence={showEvidence}
          onRecord={replaceRecord}
        />
      </div>
    </main>
  );
}

function countStatuses(records: VoiceRecord[]) {
  const counts: Record<ReviewStatus, number> = {
    draft: 0,
    approved: 0,
    deprecated: 0,
  };
  for (const record of records) counts[record.status] += 1;
  return counts;
}

function elapsedSince(iso: string | undefined, now: number) {
  if (!iso) return "";
  const seconds = Math.max(
    0,
    Math.round((now - new Date(iso).getTime()) / 1000),
  );
  return clock(seconds);
}

function Pipeline({
  conversation,
  pending,
  onRetry,
}: {
  conversation: VoiceConversation;
  pending: number;
  onRetry(): Promise<void>;
}) {
  const ingestion = conversation.ingestion;
  const steps = pipelineSteps(ingestion);
  const running = ACTIVE.has(ingestion.status ?? "");
  const [now, setNow] = useState(() => Date.now());
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState("");

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

  const failed =
    ingestion.status === "failed" || ingestion.status === "interrupted";
  return (
    <section
      className={cn("vc-pipeline", failed && "failed")}
      aria-label="Tiến trình xử lý"
      data-testid="pipeline"
      data-status={ingestion.status}
    >
      <ol>
        {steps.map((step) => (
          <li key={step.key} className={step.state}>
            {step.state === "done" ? (
              <CheckCircle2 size={18} />
            ) : step.state === "active" ? (
              <Loader2 size={18} className="animate-spin" />
            ) : step.state === "failed" ? (
              <XCircle size={18} />
            ) : (
              <Circle size={18} />
            )}
            <span>{step.label}</span>
          </li>
        ))}
        <li className={ingestion.status === "done" ? "done" : "waiting"}>
          {ingestion.status === "done" ? (
            <CheckCircle2 size={18} />
          ) : (
            <Circle size={18} />
          )}
          <span>
            {ingestion.status !== "done"
              ? "Chờ duyệt"
              : !conversation.records.length
                ? "Không có mục cần duyệt"
                : pending
                  ? `${pending} mục chờ bạn duyệt`
                  : "Đã duyệt xong"}
          </span>
        </li>
      </ol>
      <div className="vc-pipeline-status" aria-live="polite">
        <span>{stageText(ingestion)}</span>
        {running && ingestion.started_at && (
          <span className="vc-muted">
            {elapsedSince(ingestion.started_at, now)}
          </span>
        )}
        {failed && (
          <>
            {ingestion.error && (
              <code className="vc-error-detail" title={ingestion.error}>
                {ingestion.error}
              </code>
            )}
            <Button
              size="sm"
              disabled={retrying}
              onClick={() => {
                setRetrying(true);
                setRetryError("");
                onRetry()
                  .catch((reason: unknown) =>
                    setRetryError(
                      reason instanceof Error ? reason.message : String(reason),
                    ),
                  )
                  .finally(() => setRetrying(false));
              }}
              data-testid="retry-extraction"
            >
              <RotateCcw />
              Trích xuất lại
            </Button>
            {retryError && (
              <span className="vc-inline-error">{retryError}</span>
            )}
          </>
        )}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ transcript

function TranscriptPanel({
  conversation,
  focus,
  onPlay,
  onRenamed,
  onCleaningStarted,
}: {
  conversation: VoiceConversation;
  focus?: Focus;
  onPlay(seconds: number | null): void;
  onRenamed(conversation: VoiceConversation): void;
  onCleaningStarted(): void;
}) {
  const cleaning = conversation.cleaning;
  const cleaned = cleaning.turns ?? {};
  const hasCleaned = Object.keys(cleaned).length > 0;
  const [view, setView] = useState<"clean" | "raw">(
    hasCleaned ? "clean" : "raw",
  );
  const [startError, setStartError] = useState("");
  const turnRefs = useRef(new Map<string, HTMLElement>());

  // Switch to the readable version once the first cleaned turns arrive.
  const sawCleaned = useRef(hasCleaned);
  useEffect(() => {
    if (hasCleaned && !sawCleaned.current) {
      sawCleaned.current = true;
      setView("clean");
    }
  }, [hasCleaned]);

  useEffect(() => {
    if (!focus) return;
    turnRefs.current
      .get(focus.turnId)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focus]);

  // Typed text has no recognition noise: no cleaning, no audio.
  const typed = conversation.source === "text";
  const cleaningRunning = ACTIVE.has(cleaning.status ?? "");
  const done = Object.keys(cleaned).length;
  const counts = turnsBySpeaker(conversation.turns);

  return (
    <section
      className="vc-panel vc-transcript"
      aria-labelledby="vc-transcript-title"
    >
      <header className="vc-panel-head">
        <div>
          <h2 id="vc-transcript-title">
            {typed ? "Nội dung hội thoại" : "Bản ghi hội thoại"}
          </h2>
          <p className="vc-muted">
            {typed
              ? "Văn bản nhập tay, lưu nguyên văn."
              : conversation.asr.recognition === "file"
                ? "Nhận dạng lại từ file âm thanh do mất kết nối trực tiếp."
                : "Nhận dạng giọng nói tự động, có thể sai một số từ."}
          </p>
        </div>
        {!typed && (
          <div
            className="vc-segmented"
            role="group"
            aria-label="Phiên bản transcript"
          >
            <button
              aria-pressed={view === "clean"}
              disabled={!hasCleaned}
              onClick={() => setView("clean")}
              data-testid="view-clean"
            >
              Dễ đọc
            </button>
            <button
              aria-pressed={view === "raw"}
              onClick={() => setView("raw")}
              data-testid="view-raw"
            >
              Bản gốc
            </button>
          </div>
        )}
      </header>

      {!typed && (
        <div className="vc-cleaning-line" aria-live="polite">
          {cleaningRunning ? (
            <>
              <Loader2 size={14} className="animate-spin" />
              Đang làm sạch {done}/{cleaning.total ?? conversation.turns.length}{" "}
              lượt…
            </>
          ) : cleaning.status === "done" ? (
            <>
              <Sparkles size={14} />
              Bản dễ đọc do AI làm sạch, chỉ để đọc. Trích xuất tri thức dùng
              bản gốc.
            </>
          ) : cleaning.status === "failed" ||
            cleaning.status === "interrupted" ? (
            <>
              <AlertTriangle size={14} />
              {cleaning.status === "failed"
                ? "Làm sạch transcript bị lỗi."
                : "Làm sạch transcript bị gián đoạn."}
              <CleanButton
                conversation={conversation}
                label="Thử lại"
                onStarted={onCleaningStarted}
                onError={setStartError}
              />
            </>
          ) : (
            <>
              Đang xem bản gốc. Làm sạch đã tắt khi ghi.
              <CleanButton
                conversation={conversation}
                label="Làm sạch bằng AI"
                onStarted={onCleaningStarted}
                onError={setStartError}
              />
            </>
          )}
          {startError && <span className="vc-inline-error">{startError}</span>}
        </div>
      )}

      <SpeakerNames
        conversation={conversation}
        counts={counts}
        onRenamed={onRenamed}
      />

      <ol className="vc-turns">
        {conversation.turns.map((turn) => {
          const focused = focus?.turnId === turn.id;
          return (
            <li
              key={turn.id}
              ref={(element) => {
                if (element) turnRefs.current.set(turn.id, element);
                else turnRefs.current.delete(turn.id);
              }}
              className={cn("vc-turn", focused && "focused")}
              data-testid={`turn-${turn.id}`}
            >
              <span className="vc-turn-id">{turn.id}</span>
              <SpeakerBadge
                label={turn.speaker}
                speakers={conversation.speakers}
                compact
              />
              <div className="vc-turn-body">
                <span className="vc-turn-meta">
                  {speakerName(turn.speaker, conversation.speakers)}
                  {turn.start != null && (
                    <button
                      type="button"
                      className="vc-play"
                      onClick={() => onPlay(turn.start)}
                      disabled={!conversation.audio.available}
                      aria-label={`Nghe lượt ${turn.id} từ ${clock(turn.start)}`}
                    >
                      <Play size={11} fill="currentColor" />
                      {clock(turn.start)}
                    </button>
                  )}
                </span>
                <TurnText
                  turn={turn}
                  view={view}
                  cleaned={cleaned[turn.id]}
                  quote={focused ? focus?.quote : undefined}
                />
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function CleanButton({
  conversation,
  label,
  onStarted,
  onError,
}: {
  conversation: VoiceConversation;
  label: string;
  onStarted(): void;
  onError(message: string): void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className="link-button"
      disabled={busy}
      onClick={() => {
        setBusy(true);
        onError("");
        startCleaning(conversation.kb, conversation.id)
          .then(onStarted)
          .catch((reason: unknown) =>
            onError(reason instanceof Error ? reason.message : String(reason)),
          )
          .finally(() => setBusy(false));
      }}
      data-testid="start-cleaning"
    >
      {label}
    </button>
  );
}

function TurnText({
  turn,
  view,
  cleaned,
  quote,
}: {
  turn: VoiceTurn;
  view: "clean" | "raw";
  cleaned?: { text: string; kept_raw?: string };
  quote?: string;
}) {
  // Evidence quotes come from the raw transcript, so a cited turn shows its raw text.
  if (quote !== undefined) {
    const range = findQuote(turn.text, quote);
    return (
      <p className="vc-turn-text">
        {view === "clean" && <span className="vc-raw-tag">Bản gốc</span>}
        {range ? (
          <>
            {turn.text.slice(0, range[0])}
            <mark>{turn.text.slice(range[0], range[1])}</mark>
            {turn.text.slice(range[1])}
          </>
        ) : (
          turn.text
        )}
      </p>
    );
  }
  if (view === "clean" && cleaned) {
    if (!cleaned.text)
      return (
        <p className="vc-turn-text noise">(chỉ có tiếng ồn hoặc từ đệm)</p>
      );
    return (
      <p className="vc-turn-text">
        {cleaned.text}
        {cleaned.kept_raw && (
          <span
            className="vc-raw-tag"
            title="Bản làm sạch làm mất số liệu nên giữ nguyên bản gốc"
          >
            Giữ bản gốc
          </span>
        )}
      </p>
    );
  }
  return (
    <p className={cn("vc-turn-text", view === "clean" && "pending")}>
      {turn.text}
    </p>
  );
}

function SpeakerNames({
  conversation,
  counts,
  onRenamed,
}: {
  conversation: VoiceConversation;
  counts: Map<string, number>;
  onRenamed(conversation: VoiceConversation): void;
}) {
  const [editing, setEditing] = useState<string>();
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  if (!conversation.speakers.length) return null;

  const save = async (label: string) => {
    setSaving(true);
    setError("");
    try {
      onRenamed(
        await renameSpeakers(conversation.kb, conversation.id, {
          [label]: draft.trim() || null,
        }),
      );
      setEditing(undefined);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="vc-speakers">
      <span className="vc-speakers-label">Người nói</span>
      <ul>
        {conversation.speakers.map((speaker: Speaker) => (
          <li key={speaker.label}>
            {editing === speaker.label ? (
              <form
                className="vc-speaker-edit"
                onSubmit={(event) => {
                  event.preventDefault();
                  void save(speaker.label);
                }}
              >
                <SpeakerBadge label={speaker.label} compact />
                <input
                  autoFocus
                  value={draft}
                  maxLength={60}
                  placeholder={`Tên người nói ${speaker.label}`}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setEditing(undefined);
                  }}
                  aria-label={`Tên người nói ${speaker.label}`}
                  data-testid={`speaker-input-${speaker.label}`}
                />
                <Button
                  type="submit"
                  size="icon-sm"
                  disabled={saving}
                  aria-label="Lưu tên"
                >
                  <Check />
                </Button>
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  onClick={() => setEditing(undefined)}
                  aria-label="Hủy"
                >
                  <X />
                </Button>
              </form>
            ) : (
              <button
                type="button"
                className="vc-speaker-chip"
                onClick={() => {
                  setEditing(speaker.label);
                  setDraft(speaker.name ?? "");
                }}
                data-testid={`speaker-${speaker.label}`}
              >
                <SpeakerBadge
                  label={speaker.label}
                  speakers={conversation.speakers}
                />
                <span className="vc-muted">
                  {counts.get(speaker.label) ?? 0} lượt
                </span>
                <Pencil size={13} aria-label="Đặt tên" />
              </button>
            )}
          </li>
        ))}
      </ul>
      {error && <p className="vc-inline-error">{error}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ knowledge

type Filter = "all" | ReviewStatus;

function KnowledgePanel({
  conversation,
  counts,
  onEvidence,
  onRecord,
}: {
  conversation: VoiceConversation;
  counts: Record<ReviewStatus, number>;
  onEvidence(evidence: Evidence): void;
  onRecord(record: VoiceRecord): void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const ingestion = conversation.ingestion;
  const records = useMemo(
    () =>
      conversation.records.filter(
        (record) => filter === "all" || record.status === filter,
      ),
    [conversation.records, filter],
  );
  const merged = ingestion.merged ?? [];
  const running = ACTIVE.has(ingestion.status ?? "");
  const tabs: [Filter, string, number][] = [
    ["all", "Tất cả", conversation.records.length],
    ["draft", "Chờ duyệt", counts.draft],
    ["approved", "Đã duyệt", counts.approved],
    ["deprecated", "Đã loại", counts.deprecated],
  ];

  return (
    <section
      className="vc-panel vc-knowledge"
      aria-labelledby="vc-knowledge-title"
    >
      <header className="vc-panel-head">
        <div>
          <h2 id="vc-knowledge-title">Tri thức trích xuất</h2>
          <p className="vc-muted">
            Đối chiếu từng mục với bản ghi rồi duyệt hoặc loại. Mục chờ duyệt
            vẫn có thể được dùng khi hỏi đáp; mục đã loại thì không.
          </p>
        </div>
      </header>

      {running && (
        <div className="vc-extracting" data-testid="extracting">
          {[0, 1, 2].map((index) => (
            <div key={index} className="vc-skeleton" />
          ))}
          <p>{stageText(ingestion)}</p>
        </div>
      )}

      {!running &&
        ingestion.status === "done" &&
        !conversation.records.length && (
          <div className="vc-empty">
            <p>
              <strong>Không có tri thức mới.</strong>
            </p>
            <p className="vc-muted">
              {merged.length
                ? "Các nội dung trong hội thoại trùng với tri thức đã có trong knowledge base (xem bên dưới)."
                : "AI không tìm thấy kinh nghiệm, quy trình hay sự việc nào đủ rõ để ghi lại từ cuộc hội thoại này."}
            </p>
          </div>
        )}

      {(ingestion.status === "failed" || ingestion.status === "interrupted") &&
        !conversation.records.length && (
          <div className="vc-empty">
            <p className="vc-muted">
              Chưa có tri thức nào. Bấm “Trích xuất lại” ở thanh tiến trình phía
              trên. Bản ghi đã được lưu an toàn.
            </p>
          </div>
        )}

      {!!conversation.records.length && (
        <div
          className="vc-filter"
          role="tablist"
          aria-label="Lọc theo trạng thái"
        >
          {tabs.map(([key, label, count]) => (
            <button
              key={key}
              role="tab"
              aria-selected={filter === key}
              onClick={() => setFilter(key)}
              data-testid={`filter-${key}`}
            >
              {label}
              <span>{count}</span>
            </button>
          ))}
        </div>
      )}

      <ul className="vc-records">
        {records.map((record) => (
          <RecordCard
            key={record.id}
            kb={conversation.kb}
            record={record}
            speakers={conversation.speakers}
            onEvidence={onEvidence}
            onChange={onRecord}
          />
        ))}
        {!!conversation.records.length && !records.length && (
          <li className="vc-muted vc-filter-empty">
            Không có mục nào ở trạng thái này.
          </li>
        )}
      </ul>

      {!!merged.length && (
        <section className="vc-merged">
          <h3>Trùng với tri thức đã có ({merged.length})</h3>
          <p className="vc-muted">
            Cuộc hội thoại này được ghi nhận là bằng chứng bổ sung cho các mục
            sau, thay vì tạo mục mới.
          </p>
          <ul>
            {merged.map((lesson, index) => {
              const target = conversation.merged_into.find(
                (record) => record.id === lesson.target,
              );
              return (
                <li key={`${lesson.subject}-${index}`}>
                  <p>{lesson.claim}</p>
                  {target && (
                    <span className="vc-muted">
                      Đã có: “{target.subject}” · {STATUS_LABELS[target.status]}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </section>
  );
}

function RecordCard({
  kb,
  record,
  speakers,
  onEvidence,
  onChange,
}: {
  kb: string;
  record: VoiceRecord;
  speakers: Speaker[];
  onEvidence(evidence: Evidence): void;
  onChange(record: VoiceRecord): void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const evidence = recordEvidence(record);
  const steps = recordSteps(record);
  const scope = recordScope(record);
  const speaker = recordSpeaker(record);
  const certainty =
    typeof record.context.certainty === "string"
      ? record.context.certainty
      : "";
  const verify = record.verify;
  const replaced = !!record.superseded_by;

  const decide = async (
    decision: "approve" | "reject" | "reset",
    text = "",
  ) => {
    setBusy(true);
    setError("");
    try {
      onChange(await reviewRecord(kb, record.id, decision, text));
      setRejecting(false);
      setNote("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li
      className={cn("vc-record", record.status)}
      data-testid="knowledge-record"
      data-status={record.status}
    >
      <div className="vc-record-top">
        <span className="vc-type">
          {TYPE_LABELS[record.type] ?? record.type}
        </span>
        <span className="vc-record-subject">{record.subject}</span>
        <span className={cn("vc-chip", record.status)}>
          {replaced ? "Đã được thay thế" : STATUS_LABELS[record.status]}
        </span>
      </div>

      <p className="vc-claim">{claimText(record)}</p>

      {!!steps.length && (
        <ol className="vc-steps">
          {steps.map((step, index) => (
            <li key={index}>{step}</li>
          ))}
        </ol>
      )}

      <dl className="vc-facts">
        {certainty && (
          <div>
            <dt>Mức chắc chắn</dt>
            <dd className={cn(certainty !== "stated" && "caution")}>
              {CERTAINTY_LABELS[certainty] ?? certainty}
            </dd>
          </div>
        )}
        {speaker && (
          <div>
            <dt>Người nói</dt>
            <dd>{speakerName(speaker, speakers)}</dd>
          </div>
        )}
        {scope.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>

      {!!evidence.length && (
        <div className="vc-evidence">
          <span className="vc-evidence-label">Bằng chứng trong bản ghi</span>
          {evidence.map((item, index) => (
            <button
              key={index}
              type="button"
              className="vc-quote"
              disabled={!item.turnId}
              onClick={() => onEvidence(item)}
              data-testid="evidence-quote"
            >
              {item.turnId && <span className="vc-turn-id">{item.turnId}</span>}
              <q>{item.quote}</q>
            </button>
          ))}
        </div>
      )}

      {verify && (
        <p className={cn("vc-verify", verify.passed === false && "issue")}>
          {verify.error ? (
            <>
              <AlertTriangle size={14} />
              Không kiểm tra được trích dẫn.
            </>
          ) : verify.passed === false ? (
            <>
              <AlertTriangle size={14} />
              Cần kiểm tra: {(verify.issues ?? []).join("; ") || verify.verdict}
            </>
          ) : (
            <>
              <Check size={14} />
              Trích dẫn khớp với bản ghi gốc
            </>
          )}
        </p>
      )}

      {!replaced && (
        <div className="vc-record-actions">
          {record.status === "draft" && !rejecting && (
            <>
              <Button
                size="sm"
                disabled={busy}
                onClick={() => void decide("approve")}
                data-testid="approve-record"
              >
                <Check />
                Duyệt
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => setRejecting(true)}
                data-testid="reject-record"
              >
                <X />
                Loại bỏ
              </Button>
            </>
          )}
          {rejecting && (
            <form
              className="vc-reject"
              onSubmit={(event) => {
                event.preventDefault();
                void decide("reject", note);
              }}
            >
              <input
                autoFocus
                value={note}
                maxLength={1000}
                placeholder="Lý do loại (không bắt buộc), VD: AI hiểu sai ý"
                onChange={(event) => setNote(event.target.value)}
                aria-label="Lý do loại bỏ"
              />
              <Button
                size="sm"
                variant="destructive"
                type="submit"
                disabled={busy}
              >
                Xác nhận loại
              </Button>
              <Button
                size="sm"
                variant="ghost"
                type="button"
                onClick={() => setRejecting(false)}
              >
                Hủy
              </Button>
            </form>
          )}
          {record.status !== "draft" && (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => void decide("reset")}
              data-testid="reset-record"
            >
              <Undo2 />
              Hoàn tác
            </Button>
          )}
          {busy && <Loader2 size={16} className="animate-spin vc-muted" />}
        </div>
      )}
      {error && <p className="vc-inline-error">{error}</p>}
    </li>
  );
}

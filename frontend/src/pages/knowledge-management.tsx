import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Download,
  FileText,
  FolderOpen,
  Loader2,
  Mic,
  RefreshCw,
  Undo2,
  X,
} from "lucide-react";
import type { KnowledgeBase } from "@/types";
import { Button } from "@/components/ui/button";
import {
  KnowledgePanel,
  TranscriptPanel,
} from "@/components/knowledge/source-review";
import { listKnowledgeBases } from "@/services/knowledge-api";
import {
  audioUrl,
  type VoiceConversation,
  type VoiceRecord,
} from "@/services/voice-api";
import {
  dateTime,
  findQuote,
  STATUS_LABELS,
  type Evidence,
} from "@/services/voice-display";
import {
  convertManagedSource,
  extractManagedSource,
  getManagedSource,
  listManagedSources,
  originalDocumentUrl,
  reviewManagedChunk,
  sourceLink,
  type ExtractionMode,
  type ManagedChunk,
  type ManagedSource,
  type ManagedSourceDetail,
  type SourceKind,
} from "@/services/knowledge-management";
import { cn } from "@/utils/cn";

const STAGES = {
  raw: "Raw · Dữ liệu gốc",
  converted: "Converted · Đã có text",
  extracted: "Extracted · Đã trích xuất",
};
const REVIEWS = {
  none: "Chưa có kết quả cần duyệt",
  pending: "Chờ duyệt",
  reviewed: "Đã duyệt xong",
  rejected: "Đã loại",
};
const MODES = { knowledge: "Knowledge items", documents: "Document chunks" };
const isRunning = (source: ManagedSource) =>
  ["queued", "running"].includes(source.processing.status ?? "");
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

function SourceStatus({ source }: { source: ManagedSource }) {
  return (
    <div className="km-source-status">
      <span className={cn("km-stage", source.stage)}>
        {STAGES[source.stage]}
      </span>
      {isRunning(source) ? (
        <span className="vc-muted">
          <Loader2 size={14} className="animate-spin" /> Đang trích xuất
        </span>
      ) : ["failed", "interrupted", "unavailable"].includes(
          source.processing.status ?? "",
        ) ? (
        <span className="vc-inline-error">Xử lý lỗi / bị gián đoạn</span>
      ) : null}
      {source.stage === "extracted" && (
        <span
          className={cn(
            "vc-chip",
            source.review === "pending"
              ? "draft"
              : source.review === "rejected"
                ? "deprecated"
                : "approved",
          )}
        >
          {REVIEWS[source.review]}
        </span>
      )}
    </div>
  );
}

export function KnowledgeManagementPage() {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [kb, setKb] = useState("");
  const [sources, setSources] = useState<ManagedSource[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    listKnowledgeBases(abort.signal)
      .then((items) => {
        setKbs(items);
        let saved = "";
        try {
          saved = localStorage.getItem("voice-capture:kb") ?? "";
        } catch {
          /* optional setting */
        }
        setKb(
          (current) =>
            current ||
            (items.some((item) => item.name === saved)
              ? saved
              : (items[0]?.name ?? "")),
        );
        if (!items.length) setLoading(false);
      })
      .catch((reason) => {
        if (!abort.signal.aborted) {
          setError(errorText(reason));
          setLoading(false);
        }
      });
    return () => abort.abort();
  }, [reload]);

  useEffect(() => {
    if (!kb) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setSources([]);
    setNext(null);
    setLoading(true);
    setError("");
    const load = async () => {
      try {
        const inventory = await listManagedSources(kb, abort.signal);
        if (abort.signal.aborted) return;
        setSources(inventory.sources);
        setWarnings(inventory.warnings);
        setNext(inventory.next_offset);
        setLoading(false);
        if (inventory.sources.some(isRunning))
          timer = setTimeout(() => void load(), 2000);
      } catch (reason) {
        if (!abort.signal.aborted) {
          setError(errorText(reason));
          setLoading(false);
        }
      }
    };
    void load();
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [kb, reload]);

  async function loadMore() {
    if (next === null || loading) return;
    setLoading(true);
    try {
      const inventory = await listManagedSources(kb, undefined, next);
      setSources((current) => [
        ...new Map(
          [...current, ...inventory.sources].map((item) => [
            `${item.kind}:${item.id}`,
            item,
          ]),
        ).values(),
      ]);
      setNext(inventory.next_offset);
      setWarnings(inventory.warnings);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setLoading(false);
    }
  }

  const visible = sources.filter(
    (source) =>
      (filter === "all" ||
        source.stage === filter ||
        (filter === "pending" && source.review === "pending")) &&
      source.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );

  return (
    <main className="vc-page km-page" data-testid="knowledge-management">
      <header className="km-heading">
        <div>
          <h1>Quản lý tri thức</h1>
        </div>
        <Button
          variant="outline"
          onClick={() => setReload((value) => value + 1)}
          disabled={loading}
        >
          <RefreshCw /> Làm mới
        </Button>
      </header>
      <div className="km-toolbar">
        <label className="vc-field">
          <span>Knowledge base</span>
          <select
            value={kb}
            onChange={(event) => setKb(event.target.value)}
            disabled={!kbs.length}
            data-testid="management-kb"
          >
            {!kbs.length && <option value="">Chưa có knowledge base</option>}
            {kbs.map((item) => (
              <option key={item.name} value={item.name}>
                {item.title || item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="vc-field">
          <span>Tìm nguồn dữ liệu</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Tên file hoặc bản ghi…"
          />
        </label>
      </div>
      <div
        className="vc-filter km-filters"
        role="tablist"
        aria-label="Lọc nguồn dữ liệu"
      >
        {[
          ["all", "Tất cả"],
          ["raw", "Raw"],
          ["converted", "Converted"],
          ["extracted", "Extracted"],
          ["pending", "Chờ duyệt"],
        ].map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={filter === key}
            onClick={() => setFilter(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {error && (
        <p className="vc-notice error" role="alert">
          {error}
        </p>
      )}
      {warnings.map((warning) => (
        <p className="vc-notice warn" key={warning} role="status">
          {warning}
        </p>
      ))}
      {loading && !sources.length ? (
        <p className="vc-muted">
          <Loader2 className="animate-spin" size={16} /> Đang tải dữ liệu…
        </p>
      ) : !visible.length ? (
        <div className="km-empty">
          <FolderOpen size={32} />
          <h2>
            {sources.length
              ? "Không có nguồn phù hợp bộ lọc"
              : "Chưa có nguồn dữ liệu"}
          </h2>
          <Link to="/capture">Thêm dữ liệu</Link>
        </div>
      ) : (
        <ul className="km-source-list">
          {visible.map((source) => (
            <li key={`${source.kind}:${source.id}`}>
              <div className="km-source-icon">
                {source.source_kind === "voice_recording" ? (
                  <Mic />
                ) : (
                  <FileText />
                )}
              </div>
              <div className="km-source-main">
                <Link to={sourceLink(source)} className="km-source-name">
                  {source.name}
                </Link>
                <p className="vc-muted">
                  {source.content_type} ·{" "}
                  {source.created_at
                    ? dateTime(source.created_at)
                    : "Không có thời gian nguồn"}
                  {source.mode ? ` · ${MODES[source.mode]}` : ""}
                </p>
                <SourceStatus source={source} />
                {source.conversion_note && (
                  <p className="vc-muted">{source.conversion_note}</p>
                )}
                {source.review === "pending" && (
                  <small>{source.counts.draft ?? 0} mục chờ duyệt</small>
                )}
              </div>
              <Button variant="outline" size="sm" asChild>
                <Link to={sourceLink(source)}>
                  {source.review === "pending"
                    ? "Duyệt kết quả"
                    : "Xem / xử lý"}
                  <ArrowRight />
                </Link>
              </Button>
            </li>
          ))}
        </ul>
      )}
      {next !== null && (
        <Button
          variant="outline"
          disabled={loading}
          onClick={() => void loadMore()}
        >
          Tải thêm nguồn dữ liệu
        </Button>
      )}
      <p className="vc-muted km-footnote">
        Trạng thái xử lý và trạng thái duyệt được lưu riêng. “Extracted” chưa có
        nghĩa là đã được chuyên gia duyệt.
      </p>
    </main>
  );
}

export function LegacyCaptureReviewRedirect() {
  const { kb = "", conversationId = "" } = useParams();
  return (
    <Navigate
      replace
      to={sourceLink({ kb, kind: "conversation", id: conversationId })}
    />
  );
}

export function KnowledgeSourcePage() {
  const { kb = "", kind = "", sourceId = "" } = useParams();
  const validKind = ["upload", "conversation", "document"].includes(kind);
  const [source, setSource] = useState<ManagedSourceDetail>();
  const [mode, setMode] = useState<ExtractionMode>("knowledge");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [quote, setQuote] = useState("");
  const [focus, setFocus] = useState<{
    turnId: string;
    quote?: string;
    nonce: number;
  }>();
  const [mobileTab, setMobileTab] = useState("knowledge");
  const audio = useRef<HTMLAudioElement>(null);
  const preview = useRef<HTMLPreElement>(null);
  const refresh = useCallback(() => setReload((value) => value + 1), []);

  useEffect(() => {
    if (!validKind) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setSource(undefined);
    setError("");
    const load = async () => {
      try {
        const detail = await getManagedSource(
          kb,
          kind as SourceKind,
          sourceId,
          abort.signal,
        );
        if (abort.signal.aborted) return;
        setSource(detail);
        if (detail.mode) setMode(detail.mode);
        setError("");
        if (
          isRunning(detail) ||
          ["queued", "running"].includes(
            detail.conversation?.cleaning.status ?? "",
          )
        )
          timer = setTimeout(() => void load(), 1500);
      } catch (reason) {
        if (!abort.signal.aborted) {
          setError(errorText(reason));
          timer = setTimeout(() => void load(), 5000);
        }
      }
    };
    void load();
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [kb, kind, sourceId, validKind, reload]);

  async function action(work: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work();
      refresh();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  function updateRecord(record: VoiceRecord) {
    setSource((current) => {
      if (!current) return current;
      const records = current.records.map((item) =>
        item.id === record.id ? record : item,
      );
      const items = [...records, ...current.chunks];
      const review = items.some((item) => item.status === "draft")
        ? ("pending" as const)
        : items.every((item) => item.status === "deprecated")
          ? ("rejected" as const)
          : ("reviewed" as const);
      return {
        ...current,
        records,
        review,
        conversation: current.conversation && {
          ...current.conversation,
          records: current.conversation.records.map((item) =>
            item.id === record.id ? record : item,
          ),
        },
      };
    });
  }

  function showEvidence(evidence: Evidence) {
    if (evidence.turnId)
      setFocus({
        turnId: evidence.turnId,
        quote: evidence.quote,
        nonce: Date.now(),
      });
    setQuote(evidence.quote);
    setMobileTab("transcript");
    preview.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  if (!validKind) return <Navigate to="/knowledge" replace />;
  if (!source)
    return (
      <main className="vc-page km-page">
        <Link to="/knowledge" className="vc-back">
          <ArrowLeft size={16} /> Quản lý tri thức
        </Link>
        {error ? (
          <p className="vc-notice error" role="alert">
            {error}
          </p>
        ) : (
          <p className="vc-muted">Đang tải nguồn dữ liệu…</p>
        )}
      </main>
    );

  const running = isRunning(source);
  const counts = { draft: 0, approved: 0, deprecated: 0 };
  for (const item of [...source.records, ...source.chunks])
    counts[item.status]++;
  const conversation: VoiceConversation = source.conversation
    ? {
        ...source.conversation,
        ingestion: source.processing,
      }
    : {
        id: source.evidence_id ?? source.id,
        kb,
        source: "text",
        name: source.name,
        recorded_at: null,
        created_at: source.created_at ?? "",
        status: source.stage === "extracted" ? "processed" : "pending",
        audio: { available: false, file: null, duration_s: null },
        asr: {},
        speakers: [],
        turns: [],
        cleaning: { status: "off" },
        ingestion: source.processing,
        records: source.records,
        merged_into: [],
      };
  const match = source.text && quote ? findQuote(source.text, quote) : null;

  return (
    <main className="vc-page km-detail" data-testid="knowledge-source">
      <Link to="/knowledge" className="vc-back">
        <ArrowLeft size={16} /> Quản lý tri thức
      </Link>
      <header className="km-heading">
        <div>
          <h1>{source.name}</h1>
          <p>
            KB {kb} · {source.content_type}
          </p>
        </div>
        <div className="km-actions">
          {source.kind === "upload" && (
            <Button variant="outline" asChild>
              <a href={originalDocumentUrl(source)} download>
                <Download /> File gốc
              </a>
            </Button>
          )}
          <Button variant="outline" onClick={refresh} disabled={busy}>
            <RefreshCw /> Làm mới
          </Button>
        </div>
      </header>
      <ol className="km-lifecycle" aria-label="Tiến trình nguồn dữ liệu">
        {Object.entries(STAGES).map(([key, label], index) => (
          <li
            key={key}
            className={
              index <= ["raw", "converted", "extracted"].indexOf(source.stage)
                ? "complete"
                : ""
            }
          >
            <span>{index + 1}</span>
            {label}
          </li>
        ))}
      </ol>
      <section className="km-processing" aria-label="Xử lý nguồn dữ liệu">
        {source.stage === "raw" && !source.can_extract_raw ? (
          <>
            <p>
              {source.can_convert
                ? "Chuyển file text thành bản văn bản để xem và trích xuất. File gốc được giữ nguyên."
                : source.conversion_note}
            </p>
            {source.can_convert && (
              <Button
                disabled={busy}
                onClick={() =>
                  void action(() => convertManagedSource(kb, source.id))
                }
                data-testid="convert-source"
              >
                {busy && <Loader2 className="animate-spin" />}Chuyển thành text
              </Button>
            )}
          </>
        ) : source.stage !== "extracted" ? (
          <>
            {source.can_extract_raw && (
              <p>
                File TXT đã là văn bản: có thể trích xuất trực tiếp, không cần
                bước Convert.
              </p>
            )}
            <label className="vc-field">
              <span>Mode trích xuất cho nguồn này</span>
              <select
                value={mode}
                disabled={busy || running}
                onChange={(event) =>
                  setMode(event.target.value as ExtractionMode)
                }
                data-testid="extraction-mode"
              >
                <option value="knowledge">
                  Knowledge items · Trích xuất tri thức bằng AI
                </option>
                <option value="documents">
                  Document chunks · Chia đoạn văn bản
                </option>
              </select>
            </label>
            <p className="vc-muted">
              Mode được chọn riêng cho từng nguồn. Hỏi đáp vẫn dùng cấu hình
              truy xuất của knowledge base; không phải mọi mode đều được tìm
              kiếm trong mọi KB.
            </p>
            <Button
              disabled={busy || running}
              onClick={() =>
                void action(() =>
                  extractManagedSource(kb, source.kind, source.id, mode),
                )
              }
              data-testid="extract-source"
            >
              {(busy || running) && <Loader2 className="animate-spin" />}
              {running ? "Đang trích xuất…" : "Trích xuất"}
            </Button>
          </>
        ) : (
          <p>
            <strong>
              {counts.draft
                ? `${counts.draft} mục chờ duyệt`
                : REVIEWS[source.review]}
            </strong>{" "}
            · {MODES[source.mode ?? "knowledge"]}. Đối chiếu với bằng chứng
            trước khi duyệt; mỗi quyết định được lưu lại.
          </p>
        )}
        {source.processing.error && (
          <p className="vc-inline-error">{source.processing.error}</p>
        )}
        {source.processing.status === "interrupted" && (
          <p className="vc-inline-error">
            Lần xử lý trước bị gián đoạn. Có thể thử trích xuất lại.
          </p>
        )}
      </section>
      {error && (
        <p className="vc-notice error" role="alert">
          {error}
        </p>
      )}
      {source.conversation?.audio.available && (
        <audio
          ref={audio}
          className="vc-audio"
          controls
          src={audioUrl(kb, source.conversation.id)}
        />
      )}
      {source.stage !== "raw" && (
        <>
          <div className="vc-mobile-tabs" role="tablist">
            <button
              role="tab"
              aria-selected={mobileTab === "transcript"}
              onClick={() => setMobileTab("transcript")}
            >
              Text / Bằng chứng
            </button>
            <button
              role="tab"
              aria-selected={mobileTab === "knowledge"}
              onClick={() => setMobileTab("knowledge")}
            >
              Kết quả / Duyệt
            </button>
          </div>
          <div className="vc-columns" data-tab={mobileTab}>
            {source.conversation ? (
              <TranscriptPanel
                conversation={source.conversation}
                focus={focus}
                onPlay={(seconds) => {
                  if (audio.current && seconds !== null) {
                    audio.current.currentTime = Math.max(0, seconds - 0.3);
                    void audio.current.play().catch(() => undefined);
                  }
                }}
                onRenamed={(updated) =>
                  setSource(
                    (current) =>
                      current && { ...current, conversation: updated },
                  )
                }
                onCleaningStarted={refresh}
              />
            ) : (
              <section className="vc-panel vc-transcript">
                <header className="vc-panel-head">
                  <div>
                    <h2>Văn bản nguồn</h2>
                    <p className="vc-muted">
                      Bản text dùng để trích xuất. File gốc được giữ riêng.
                    </p>
                  </div>
                </header>
                <pre className="km-text" ref={preview}>
                  {source.text === null ? (
                    source.conversion_note
                  ) : match ? (
                    <>
                      {source.text.slice(0, match[0])}
                      <mark>{source.text.slice(match[0], match[1])}</mark>
                      {source.text.slice(match[1])}
                    </>
                  ) : (
                    source.text
                  )}
                </pre>
                {source.text_truncated && (
                  <p className="vc-muted">
                    Đang xem 100.000 ký tự đầu. Trích xuất document chunks dùng
                    toàn bộ văn bản.
                  </p>
                )}
              </section>
            )}
            {source.mode === "documents" ? (
              <section className="vc-panel vc-knowledge">
                <header className="vc-panel-head">
                  <div>
                    <h2>Document chunks</h2>
                    <p className="vc-muted">
                      Mỗi đoạn giữ ID nguồn và lịch sử duyệt. Đoạn bị loại được
                      giữ để truy vết và không xuất hiện trong tìm kiếm.
                    </p>
                  </div>
                </header>
                {!source.chunks.length && (
                  <p className="vc-muted">Chưa có chunks.</p>
                )}
                <ul className="vc-records">
                  {source.chunks.map((chunk) => (
                    <ChunkCard
                      key={chunk.id}
                      source={source}
                      chunk={chunk}
                      onChanged={setSource}
                    />
                  ))}
                </ul>
              </section>
            ) : (
              <KnowledgePanel
                conversation={conversation}
                counts={counts}
                onEvidence={showEvidence}
                onRecord={updateRecord}
              />
            )}
          </div>
        </>
      )}
    </main>
  );
}

function ChunkCard({
  source,
  chunk,
  onChanged,
}: {
  source: ManagedSourceDetail;
  chunk: ManagedChunk;
  onChanged(source: ManagedSourceDetail): void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  async function decide(decision: "approve" | "reject" | "reset") {
    setBusy(true);
    setError("");
    try {
      onChanged(await reviewManagedChunk(source, chunk.id, decision, note));
      setRejecting(false);
      setNote("");
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <li
      className={cn("vc-record", chunk.status)}
      data-testid="document-chunk"
      data-status={chunk.status}
    >
      <div className="vc-record-top">
        <strong>Đoạn {chunk.index + 1}</strong>
        <span className={cn("vc-chip", chunk.status)}>
          {STATUS_LABELS[chunk.status]}
        </span>
      </div>
      <small className="vc-muted">
        [D:{chunk.doc_id}] · {chunk.id}
      </small>
      <p className="km-chunk-text">{chunk.text}</p>
      <div className="vc-record-actions">
        {chunk.status === "draft" && !rejecting ? (
          <>
            <Button
              size="sm"
              disabled={busy}
              onClick={() => void decide("approve")}
            >
              <Check /> Duyệt
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => setRejecting(true)}
            >
              <X /> Loại bỏ
            </Button>
          </>
        ) : chunk.status !== "draft" ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => void decide("reset")}
          >
            <Undo2 /> Hoàn tác
          </Button>
        ) : null}
        {rejecting && (
          <form
            className="vc-reject"
            onSubmit={(event) => {
              event.preventDefault();
              void decide("reject");
            }}
          >
            <input
              aria-label="Lý do loại bỏ"
              value={note}
              maxLength={1000}
              placeholder="Lý do loại (không bắt buộc)"
              onChange={(event) => setNote(event.target.value)}
            />
            <Button size="sm" type="submit" disabled={busy}>
              Xác nhận loại
            </Button>
            <Button
              size="sm"
              type="button"
              variant="ghost"
              onClick={() => setRejecting(false)}
            >
              Hủy
            </Button>
          </form>
        )}
      </div>
      {error && (
        <p className="vc-inline-error" role="alert">
          {error}
        </p>
      )}
      {!!chunk.history.length && (
        <details className="vc-muted">
          <summary>Lịch sử duyệt ({chunk.history.length})</summary>
          <ul>
            {chunk.history.map((event, index) => (
              <li key={index}>
                {dateTime(event.at)} · {event.decision}
                {event.note ? ` · ${event.note}` : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

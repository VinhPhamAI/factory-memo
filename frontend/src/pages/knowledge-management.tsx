import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, useLocation, useParams, useSearchParams } from "react-router-dom";
import { Tabs } from "radix-ui";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Database,
  Download,
  Files,
  FileText,
  FolderOpen,
  GitCompareArrows,
  Loader2,
  Mic,
  Plus,
  RefreshCw,
  Undo2,
  X,
} from "lucide-react";
import type { KnowledgeBase, LocatedReference } from "@/types";
import { Button } from "@/components/ui/button";
import { CreateKnowledgeBaseDialog } from "@/components/knowledge/create-kb-dialog";
import { KnowledgeBaseSettings } from "@/components/knowledge/knowledge-base-settings";
import { DocumentUpload } from "@/components/voice/document-upload";
import { useAuth } from "@/hooks/auth";
import { LocatedEvidencePreview } from "@/components/source-preview";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  KnowledgePanel,
  TranscriptPanel,
} from "@/components/knowledge/source-review";
import {
  ConflictJobNote,
  ConflictReviewPanel,
  conflictRunning,
} from "@/components/knowledge/conflict-review";
import { listKnowledgeBases } from "@/services/knowledge-api";
import {
  fetchAudio,
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
  DuplicateContentError,
  extractManagedSource,
  fetchOriginalDocument,
  fetchOriginalPdfPage,
  getManagedSource,
  listManagedSources,
  NearDuplicateError,
  downloadOriginalDocument,
  reviewManagedChunk,
  removeManagedSource,
  sourceLink,
  withJob,
  type DuplicateOf,
  type ExtractionMode,
  type ManagedChunk,
  type ManagedSource,
  type ManagedSourceDetail,
  type OverlapMatch,
  type ProcessingState,
  type SourceJob,
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
const MODE_OPTIONS = {
  knowledge: "Knowledge items · Trích xuất tri thức bằng AI",
  documents: "Document chunks · Chia đoạn văn bản",
};
const PROGRESS = {
  convert: ["Đang chờ chuyển đổi", "Đang chuyển đổi"],
  extract: ["Đang chờ trích xuất", "Đang trích xuất"],
  conflict: ["Chờ kiểm tra mâu thuẫn", "Đang kiểm tra mâu thuẫn"],
};
const isRunning = (source: ManagedSource) =>
  ["queued", "running"].includes(source.processing.status ?? "");
const canExtract = (source: ManagedSource) =>
  source.kind !== "document" &&
  (source.stage === "converted" ||
    (source.stage === "raw" && source.can_extract_raw));
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
const key = (source: ManagedSource) => `${source.kind}:${source.id}`;

/** "Đang chuyển đổi → sau đó trích xuất" while a job is queued or running. */
function progressLabel(processing: ProcessingState) {
  const job = processing.job;
  const [queued, running] = PROGRESS[job?.stage ?? "extract"];
  const label = processing.status === "queued" ? queued : running;
  return job?.stage === "convert" && job.next_stages.includes("extract")
    ? `${label} → sau đó trích xuất`
    : label;
}

function failureLabel(processing: ProcessingState) {
  if (processing.status === "interrupted") return "Bị gián đoạn";
  if (processing.status === "unavailable") return "Không đọc được trạng thái";
  return processing.job?.stage === "convert"
    ? "Chuyển đổi lỗi"
    : "Trích xuất lỗi";
}

/** Retry and failure details of the latest job. */
function JobNote({ processing }: { processing: ProcessingState }) {
  const job = processing.job;
  if (job?.status === "queued" && job.retry_at)
    return (
      <p className="vc-muted" role="status">
        Lần thử {job.attempts}/{job.max_attempts} lỗi
        {job.error ? `: ${job.error}` : ""}. Tự thử lại lúc{" "}
        {dateTime(job.retry_at)}.
      </p>
    );
  if (processing.status === "failed" && processing.error)
    return (
      <p className="vc-inline-error">
        {job
          ? `${failureLabel(processing)} (lần thử ${job.attempts}/${job.max_attempts}): `
          : ""}
        {processing.error}
      </p>
    );
  return null;
}

/** The earlier source that already holds the extraction of identical content. */
function DuplicateNote({
  kb,
  duplicate,
}: {
  kb: string;
  duplicate: DuplicateOf;
}) {
  const link = (
    <Link
      to={sourceLink({ kb, kind: duplicate.kind, id: duplicate.source_id })}
    >
      «{duplicate.name}»
    </Link>
  );
  return (
    <p className="vc-notice warn" role="status" data-testid="duplicate-note">
      Nội dung trùng với {link}{" "}
      {duplicate.status === "running"
        ? "đang được trích xuất"
        : `đã trích xuất lúc ${dateTime(duplicate.extracted_at)} (${MODES[duplicate.mode]})`}
      {duplicate.same_logic === false
        ? " bằng logic trích xuất cũ. Trích xuất lại có xác nhận chưa được hỗ trợ."
        : ". Không trích xuất lặp; xem và duyệt kết quả ở nguồn đó."}
    </p>
  );
}

/** Labels after the earlier source's name: «ca-dem.txt» Chứa nội dung này. */
const OVERLAP_LEVELS = {
  near_duplicate: "Gần như trùng",
  contained: "Chứa nội dung này",
  similar: "Trùng một phần",
};
/** "<this source>: nằm trong «ca-dem.txt»" in the bulk result. */
const OVERLAP_RELATIONS = {
  near_duplicate: "gần như trùng với",
  contained: "nằm trong",
  similar: "trùng một phần với",
};
const percent = (value: number) => `${Math.round(value * 100)}%`;

/** Scores of one match: Jaccard, and how much of each text the other holds. */
function overlapScores(match: OverlapMatch) {
  return `Jaccard ${percent(match.jaccard)} · ${percent(match.containment)} nội dung này nằm trong nguồn đó · nguồn đó có ${percent(match.coverage)} nằm ở đây`;
}

function OverlapList({ kb, matches }: { kb: string; matches: OverlapMatch[] }) {
  return (
    <ul data-testid="overlap-matches">
      {matches.map((match) => (
        <li key={match.run_id}>
          <Link to={sourceLink({ kb, kind: match.kind, id: match.source_id })}>
            «{match.name}»
          </Link>{" "}
          <strong>{OVERLAP_LEVELS[match.level]}</strong>
          {match.status === "running" ? " (đang trích xuất)" : ""}:{" "}
          <span className="vc-muted">{overlapScores(match)}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Extracted sources whose text overlaps this one. A near-duplicate or
 * contained match waits for the reviewer, who may still extract: the result
 * is a separate draft, nothing is merged or overwritten. Similarity is only
 * a warning; it never decides on its own.
 */
function OverlapNote({
  kb,
  processing,
  busy,
  onConfirm,
}: {
  kb: string;
  processing: ProcessingState;
  busy: boolean;
  onConfirm(): void;
}) {
  const [open, setOpen] = useState(false);
  const matches = processing.overlaps ?? [];
  if (processing.status === "near_duplicate" && matches.length)
    return (
      <div className="vc-notice warn" role="status" data-testid="overlap-note">
        <p>
          Chưa trích xuất: nội dung gần giống nguồn đã trích xuất
          {processing.mode ? ` (${MODES[processing.mode]})` : ""}. Kiểm tra
          nguồn đó trước để tránh tri thức lặp.
        </p>
        <OverlapList kb={kb} matches={matches} />
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => setOpen(true)}
          data-testid="confirm-overlap"
        >
          Vẫn trích xuất
        </Button>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Vẫn trích xuất nguồn này?</DialogTitle>
              <DialogDescription>
                Kết quả là bản nháp riêng của nguồn này; nguồn cũ và tri thức đã
                trích xuất không bị gộp hay ghi đè. Bản nháp vẫn cần duyệt.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(false)}>
                Hủy
              </Button>
              <Button
                disabled={busy}
                onClick={() => {
                  setOpen(false);
                  onConfirm();
                }}
                data-testid="confirm-overlap-submit"
              >
                Vẫn trích xuất
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    );
  // Overlaps recorded by the finished extraction: information only.
  if (processing.status === "done" && matches.length)
    return (
      <div className="vc-muted" role="status" data-testid="overlap-info">
        <p>
          {processing.overlap_confirmed
            ? "Đã trích xuất theo xác nhận dù nội dung gần giống:"
            : "Nội dung có phần giống nguồn đã trích xuất (chỉ để tham khảo):"}
        </p>
        <OverlapList kb={kb} matches={matches} />
      </div>
    );
  return null;
}

function SourceStatus({ source }: { source: ManagedSource }) {
  return (
    <div className="km-source-status">
      <span className={cn("km-stage", source.stage)}>
        {STAGES[source.stage]}
      </span>
      {source.processing.status === "duplicate" && (
        <span
          className="vc-chip draft"
          title={
            source.processing.duplicate_of
              ? `Trùng với «${source.processing.duplicate_of.name}»`
              : undefined
          }
          data-testid="duplicate-chip"
        >
          Trùng nội dung
        </span>
      )}
      {source.processing.status === "near_duplicate" && (
        <span
          className="vc-chip draft"
          title="Chờ xác nhận trước khi trích xuất"
          data-testid="near-duplicate-chip"
        >
          Gần giống · chờ xác nhận
        </span>
      )}
      {isRunning(source) ? (
        <span className="vc-muted" data-testid="source-progress">
          <Loader2 size={14} className="animate-spin" />{" "}
          {progressLabel(source.processing)}
        </span>
      ) : ["failed", "interrupted", "unavailable"].includes(
          source.processing.status ?? "",
        ) ? (
        <span className="vc-inline-error">
          {failureLabel(source.processing)}
        </span>
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

/** A source the bulk Convert or Extract can queue now. */
const isSelectable = (source: ManagedSource) =>
  !isRunning(source) && (source.can_convert || canExtract(source));

interface BulkResult {
  action: "convert" | "extract";
  queued: number;
  skipped: number;
  failures: { name: string; error: string }[];
  /** Refused: identical content was already extracted from another source. */
  duplicates: { key: string; name: string; duplicateOf: DuplicateOf }[];
  /** Waiting: nearly the same as an extracted source; confirm per source. */
  nearDuplicates: { source: ManagedSource; matches: OverlapMatch[] }[];
}

/** Bulk Convert / Extract over the checked sources; both only queue jobs. */
function BulkActions({
  sources,
  selectable,
  defaultMode,
  allSelected,
  onToggleAll,
  onQueued,
}: {
  sources: ManagedSource[];
  selectable: number;
  defaultMode: ExtractionMode;
  allSelected: boolean;
  onToggleAll(): void;
  onQueued(sources: ManagedSource[], result: BulkResult): void;
}) {
  const [mode, setMode] = useState<ExtractionMode>(defaultMode);
  const [busy, setBusy] = useState<BulkResult["action"]>();
  const convertible = sources.filter((source) => source.can_convert);
  const extractable = sources.filter(canExtract);

  async function queue(
    action: BulkResult["action"],
    targets: ManagedSource[],
    work: (source: ManagedSource) => Promise<{ job: SourceJob }>,
  ) {
    setBusy(action);
    const results = await Promise.allSettled(targets.map(work));
    const queued: ManagedSource[] = [];
    const failures: BulkResult["failures"] = [];
    const duplicates: BulkResult["duplicates"] = [];
    const nearDuplicates: BulkResult["nearDuplicates"] = [];
    results.forEach((result, index) => {
      if (result.status === "fulfilled")
        queued.push(withJob(targets[index], result.value.job));
      else if (result.reason instanceof DuplicateContentError)
        duplicates.push({
          key: key(targets[index]),
          name: targets[index].name,
          duplicateOf: result.reason.duplicateOf,
        });
      else if (result.reason instanceof NearDuplicateError)
        nearDuplicates.push({
          source: targets[index],
          matches: result.reason.matches,
        });
      else
        failures.push({
          name: targets[index].name,
          error: errorText(result.reason),
        });
    });
    setBusy(undefined);
    onQueued(queued, {
      action,
      queued: queued.length,
      skipped: sources.length - targets.length,
      failures,
      duplicates,
      nearDuplicates,
    });
  }

  return (
    <div className="km-bulk" data-testid="bulk-actions">
      <label className="km-bulk-all">
        <input
          type="checkbox"
          checked={allSelected}
          disabled={!selectable || !!busy}
          onChange={onToggleAll}
          aria-label="Chọn tất cả nguồn có thể xử lý"
          data-testid="select-all-sources"
        />
        {sources.length ? `Đã chọn ${sources.length}` : "Chọn tất cả"}
      </label>
      <div className="km-bulk-actions">
        <Button
          size="sm"
          variant="outline"
          disabled={!convertible.length || !!busy}
          title="Chuyển các file đã chọn thành text"
          onClick={() =>
            void queue("convert", convertible, (source) =>
              convertManagedSource(source.kb, source.id),
            )
          }
          data-testid="bulk-convert"
        >
          {busy === "convert" && <Loader2 className="animate-spin" />}
          Convert{convertible.length ? ` (${convertible.length})` : ""}
        </Button>
        <select
          aria-label="Mode trích xuất cho các mục đã chọn"
          value={mode}
          disabled={!!busy}
          onChange={(event) => setMode(event.target.value as ExtractionMode)}
          data-testid="bulk-extraction-mode"
        >
          {Object.entries(MODE_OPTIONS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <Button
          size="sm"
          disabled={!extractable.length || !!busy}
          title="Raw được chuyển đổi trước rồi trích xuất"
          onClick={() =>
            void queue("extract", extractable, (source) =>
              extractManagedSource(source.kb, source.kind, source.id, mode),
            )
          }
          data-testid="bulk-extract"
        >
          {busy === "extract" && <Loader2 className="animate-spin" />}
          Extract{extractable.length ? ` (${extractable.length})` : ""}
        </Button>
      </div>
    </div>
  );
}

function BulkNotice({ kb, result }: { kb: string; result: BulkResult }) {
  const verb = result.action === "convert" ? "chuyển đổi" : "trích xuất";
  return (
    <div
      className={cn(
        "vc-notice km-bulk-result",
        result.failures.length ||
          result.duplicates.length ||
          result.nearDuplicates.length
          ? "warn"
          : "ok",
      )}
      role="status"
      data-testid="bulk-result"
    >
      <div>
        <p>
          Đã xếp {result.queued} job {verb} vào hàng đợi
          {result.skipped
            ? `; bỏ qua ${result.skipped} mục không hỗ trợ bước này`
            : ""}
          {result.duplicates.length
            ? `; ${result.duplicates.length} mục trùng nội dung đã trích xuất (không xếp hàng)`
            : ""}
          {result.nearDuplicates.length
            ? `; ${result.nearDuplicates.length} mục gần giống nguồn đã trích xuất, chờ xác nhận (mở từng nguồn để xác nhận)`
            : ""}
          {result.failures.length
            ? `; ${result.failures.length} mục lỗi (vẫn được chọn để thử lại):`
            : "."}
        </p>
        {!!result.failures.length && (
          <ul>
            {result.failures.map((failure) => (
              <li key={failure.name}>
                {failure.name}: {failure.error}
              </li>
            ))}
          </ul>
        )}
        {!!result.duplicates.length && (
          <ul data-testid="bulk-duplicates">
            {result.duplicates.map((item) => (
              <li key={item.key}>
                {item.name}: trùng với{" "}
                <Link
                  to={sourceLink({
                    kb,
                    kind: item.duplicateOf.kind,
                    id: item.duplicateOf.source_id,
                  })}
                >
                  «{item.duplicateOf.name}»
                </Link>
                {item.duplicateOf.status === "running"
                  ? " (đang trích xuất)"
                  : item.duplicateOf.same_logic === false
                    ? " (logic trích xuất cũ)"
                    : ""}
              </li>
            ))}
          </ul>
        )}
        {!!result.nearDuplicates.length && (
          <ul data-testid="bulk-near-duplicates">
            {result.nearDuplicates.map(({ source, matches }) => (
              <li key={key(source)}>
                <Link to={sourceLink(source)}>{source.name}</Link>:{" "}
                {OVERLAP_RELATIONS[matches[0].level]}{" "}
                <Link
                  to={sourceLink({
                    kb,
                    kind: matches[0].kind,
                    id: matches[0].source_id,
                  })}
                >
                  «{matches[0].name}»
                </Link>
                {matches.length > 1
                  ? ` và ${matches.length - 1} nguồn khác`
                  : ""}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export function KnowledgeManagementPage() {
  const auth = useAuth();
  const user = auth.status === "signed-in" ? auth.user : undefined;
  const canManage = user?.role === "admin" || user?.role === "expert";
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab");
  const view = tab === "knowledge-bases" || tab === "conflicts" ? tab : "sources";
  const [creatingKb, setCreatingKb] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [kb, setKb] = useState("");
  const [sources, setSources] = useState<ManagedSource[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [qaMode, setQaMode] = useState<ExtractionMode>("knowledge");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkResult, setBulkResult] = useState<BulkResult>();
  const [uploading, setUploading] = useState(false);
  const [removing, setRemoving] = useState<ManagedSource>();
  const [removeBusy, setRemoveBusy] = useState(false);
  const [removeError, setRemoveError] = useState("");
  const refresh = () => setReload((value) => value + 1);

  async function confirmRemove() {
    if (!removing) return;
    setRemoveBusy(true);
    setRemoveError("");
    try {
      await removeManagedSource(removing);
      setRemoving(undefined);
      refresh();
    } catch (error) {
      setRemoveError(errorText(error));
    } finally {
      setRemoveBusy(false);
    }
  }

  useEffect(() => {
    const abort = new AbortController();
    listKnowledgeBases(abort.signal)
      .then((items) => {
        setKbs(items);
        setCatalogError("");
        let saved = "";
        try {
          saved = localStorage.getItem("voice-capture:kb") ?? "";
        } catch {
          /* optional setting */
        }
        setKb(
          (current) =>
            items.some((item) => item.name === current)
              ? current
              : items.some((item) => item.name === saved)
                ? saved
                : (items[0]?.name ?? ""),
        );
        if (!items.length) {
          setLoading(false);
          setSources([]);
          setWarnings([]);
          setNext(null);
          setError("");
        }
      })
      .catch((reason) => {
        if (!abort.signal.aborted) {
          setCatalogError(errorText(reason));
          setLoading(false);
        }
      });
    return () => abort.abort();
  }, [reload]);

  useEffect(() => {
    if (!kb) return;
    const abort = new AbortController();
    setSources([]);
    setNext(null);
    setSelected(new Set());
    setBulkResult(undefined);
    setLoading(true);
    setError("");
    listManagedSources(kb, abort.signal)
      .then((inventory) => {
        setSources(inventory.sources);
        setWarnings(inventory.warnings);
        setNext(inventory.next_offset);
        setQaMode(inventory.qa_mode);
        setLoading(false);
      })
      .catch((reason) => {
        if (!abort.signal.aborted) {
          setError(errorText(reason));
          setLoading(false);
        }
      });
    return () => abort.abort();
  }, [kb, reload]);

  // While jobs are queued or running, refresh the first page in place.
  const polling = sources.some(
    (source) => isRunning(source) || conflictRunning(source.processing),
  );
  useEffect(() => {
    if (!kb || !polling) return;
    const abort = new AbortController();
    const timer = setInterval(() => {
      listManagedSources(kb, abort.signal)
        .then((inventory) => {
          const fresh = new Map(
            inventory.sources.map((item) => [key(item), item]),
          );
          setSources((current) => [
            ...inventory.sources,
            ...current.filter((item) => !fresh.has(key(item))),
          ]);
          setWarnings(inventory.warnings);
        })
        .catch(() => undefined);
    }, 2000);
    return () => {
      abort.abort();
      clearInterval(timer);
    };
  }, [kb, polling]);

  function queued(updated: ManagedSource[], result: BulkResult) {
    const byKey = new Map(updated.map((item) => [key(item), item]));
    setSources((current) =>
      current.map((item) => byKey.get(key(item)) ?? item),
    );
    // Failed items stay checked so they can be retried; duplicates would be refused again
    // and near-duplicates are confirmed one source at a time.
    setSelected((current) => {
      const remaining = new Set(current);
      byKey.forEach((_, itemKey) => remaining.delete(itemKey));
      result.duplicates.forEach((item) => remaining.delete(item.key));
      result.nearDuplicates.forEach((item) =>
        remaining.delete(key(item.source)),
      );
      return remaining;
    });
    setBulkResult(result);
  }

  function toggle(source: ManagedSource) {
    setSelected((current) => {
      const updated = new Set(current);
      if (!updated.delete(key(source))) updated.add(key(source));
      return updated;
    });
  }

  async function loadMore() {
    if (next === null || loading) return;
    setLoading(true);
    try {
      const inventory = await listManagedSources(kb, undefined, next);
      setSources((current) => [
        ...new Map(
          [...current, ...inventory.sources].map((item) => [key(item), item]),
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
  // Actions apply to what is checked on screen: visible and still actionable.
  const selectable = visible.filter(isSelectable);
  const checked = selectable.filter((source) => selected.has(key(source)));
  const allSelected =
    !!selectable.length && checked.length === selectable.length;
  function toggleAll() {
    setSelected((current) => {
      const updated = new Set(current);
      selectable.forEach((source) =>
        allSelected ? updated.delete(key(source)) : updated.add(key(source)),
      );
      return updated;
    });
  }

  return (
    <main className="vc-page km-page" data-testid="knowledge-management">
      <header className="km-heading">
        <div>
          <h1>Quản lý tri thức</h1>
        </div>
        <Button
          variant="outline"
          onClick={refresh}
          disabled={loading || uploading}
        >
          <RefreshCw /> Làm mới
        </Button>
      </header>
      <Tabs.Root
        className="km-workspace"
        value={view}
        orientation="vertical"
        activationMode="manual"
        onValueChange={(value) => {
          const nextParams = new URLSearchParams(params);
          if (value === "sources") nextParams.delete("tab");
          else nextParams.set("tab", value);
          setParams(nextParams);
        }}
      >
        <Tabs.List className="km-sidebar" aria-label="Nội dung quản lý tri thức">
          <Tabs.Trigger className="km-tab" value="knowledge-bases" disabled={uploading}>
            <Database aria-hidden="true" /> Knowledge base
          </Tabs.Trigger>
          <Tabs.Trigger className="km-tab" value="sources">
            <Files aria-hidden="true" /> Nguồn dữ liệu
          </Tabs.Trigger>
          <Tabs.Trigger className="km-tab" value="conflicts" disabled={uploading}>
            <GitCompareArrows aria-hidden="true" /> Mâu thuẫn
          </Tabs.Trigger>
        </Tabs.List>

        <div className="km-workspace-content">
          <div className="km-workspace-bar">
            <label className="vc-field km-kb-picker">
              <span>Knowledge base</span>
              <select
                value={kb}
                onChange={(event) => setKb(event.target.value)}
                disabled={!kbs.length || uploading}
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
            {view === "knowledge-bases" && canManage && (
              <Button onClick={() => setCreatingKb(true)}>
                <Plus /> Tạo knowledge base
              </Button>
            )}
          </div>
          {catalogError && (
            <p className="vc-notice error" role="alert">{catalogError}</p>
          )}

          <Tabs.Content value="knowledge-bases" className="km-tab-panel">
            {kb && (
              <KnowledgeBaseSettings
                key={kb}
                name={kb}
                role={user?.role}
                revision={reload}
                onChanged={refresh}
              />
            )}
            {!kb && !loading && !catalogError && (
              <p className="vc-muted">Chưa có knowledge base để quản lý.</p>
            )}
            {canManage && (
              <CreateKnowledgeBaseDialog
                open={creatingKb}
                taken={kbs.map((item) => item.name)}
                onClose={() => setCreatingKb(false)}
                onCreated={(created) => {
                  setKbs((items) => [...items, created]);
                  setKb(created.name);
                  setCreatingKb(false);
                  refresh();
                }}
              />
            )}
          </Tabs.Content>

          <Tabs.Content value="conflicts" className="km-tab-panel">
            {kb ? (
              <ConflictReviewPanel
                key={kb}
                kb={kb}
                refreshKey={`${reload}:${sources.map((source) => `${source.processing.conflict_job?.id}:${source.processing.conflict_job?.status}`).join(",")}`}
                activeJobs={polling}
                onChanged={refresh}
              />
            ) : (
              !loading && <p className="vc-muted">Chọn knowledge base để xem mâu thuẫn.</p>
            )}
          </Tabs.Content>

          <Tabs.Content value="sources" className="km-tab-panel">
            <label className="vc-field km-source-search">
              <span>Tìm nguồn dữ liệu</span>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Tên file hoặc bản ghi…"
              />
            </label>
            {kb && (
              <details className="km-add-documents">
                <summary>Thêm tài liệu</summary>
                <DocumentUpload key={kb} kb={kb} disabled={loading} onBusy={setUploading}
                  onStored={refresh} />
              </details>
            )}
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
            {bulkResult && <BulkNotice kb={kb} result={bulkResult} />}
            {!!visible.length && (
              <BulkActions
                key={qaMode}
                sources={checked}
                selectable={selectable.length}
                defaultMode={qaMode}
                allSelected={allSelected}
                onToggleAll={toggleAll}
                onQueued={queued}
              />
            )}
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
                  <li key={key(source)} data-testid="managed-source">
                    <input
                      type="checkbox"
                      className="km-select"
                      aria-label={`Chọn ${source.name}`}
                      checked={isSelectable(source) && selected.has(key(source))}
                      disabled={!isSelectable(source)}
                      title={
                        isSelectable(source)
                          ? undefined
                          : isRunning(source)
                            ? "Đang xử lý"
                            : "Không có bước Convert/Extract cho nguồn này"
                      }
                      onChange={() => toggle(source)}
                      data-testid="select-source"
                    />
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
                      <JobNote processing={source.processing} />
                      <ConflictJobNote processing={source.processing} />
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
                    {user && (canManage || user.id === source.submitted_by) && (
                      <Button variant="outline" size="sm"
                        disabled={source.processing.job?.status === "running" || source.processing.status === "running"}
                        onClick={() => {
                          setRemoveError("");
                          setRemoving(source);
                        }}>
                        Gỡ nguồn
                      </Button>
                    )}
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
              Chọn các nguồn rồi bấm Convert hoặc Extract ở đầu danh sách; hai nút
              chỉ xếp job, worker chạy nền theo thứ tự chuyển đổi → trích xuất
              (Extract trên Raw tự chuyển đổi trước). Trạng thái xử lý và trạng
              thái duyệt được lưu riêng. “Extracted” chưa có nghĩa là đã được
              chuyên gia duyệt.
            </p>
          </Tabs.Content>
        </div>
      </Tabs.Root>
      <Dialog open={!!removing} onOpenChange={(open) => {
        if (!open && !removeBusy) setRemoving(undefined);
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Gỡ nguồn dữ liệu</DialogTitle>
            <DialogDescription>
              Gỡ “{removing?.name}” khỏi knowledge base này?
            </DialogDescription>
          </DialogHeader>
          {removeError && <p className="vc-notice error" role="alert">{removeError}</p>}
          <DialogFooter>
            <Button variant="outline" disabled={removeBusy}
              onClick={() => setRemoving(undefined)}>Hủy</Button>
            <Button variant="destructive" disabled={removeBusy}
              onClick={() => void confirmRemove()}>Xác nhận gỡ</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
  // A capture that saved the source but could not queue Auto processing.
  const notice = (useLocation().state as { notice?: string } | null)?.notice;
  const [source, setSource] = useState<ManagedSourceDetail>();
  const [mode, setMode] = useState<ExtractionMode>("knowledge");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [quote, setQuote] = useState("");
  const [located, setLocated] = useState<LocatedReference>();
  const [originalObjectUrl, setOriginalObjectUrl] = useState<string>();
  const [focus, setFocus] = useState<{
    turnId: string;
    quote?: string;
    nonce: number;
  }>();
  const [mobileTab, setMobileTab] = useState("knowledge");
  const [audioObjectUrl, setAudioObjectUrl] = useState<string>();
  const audio = useRef<HTMLAudioElement>(null);
  const preview = useRef<HTMLPreElement>(null);
  const refresh = useCallback(() => setReload((value) => value + 1), []);
  const audioConversationId = source?.conversation?.id;
  const audioAvailable = source?.conversation?.audio.available;

  useEffect(() => {
    setSource(undefined);
    setError("");
    setLocated(undefined);
  }, [kb, kind, sourceId]);

  useEffect(() => {
    if (!audioConversationId || !audioAvailable) {
      setAudioObjectUrl(undefined);
      return;
    }
    const abort = new AbortController();
    let objectUrl: string | undefined;
    void fetchAudio(kb, audioConversationId, abort.signal)
      .then((blob) => {
        if (abort.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setAudioObjectUrl(objectUrl);
      })
      .catch((reason) => {
        if (!abort.signal.aborted) setError(errorText(reason));
      });
    return () => {
      abort.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [audioAvailable, audioConversationId, kb]);

  useEffect(() => {
    if (!source || source.kind !== "upload" || !located) {
      setOriginalObjectUrl(undefined);
      return;
    }
    setOriginalObjectUrl(undefined);
    const abort = new AbortController();
    let objectUrl: string | undefined;
    const pageWithBox =
      source.content_type === "application/pdf" &&
      (located.type === "page" || located.type === "image") &&
      located.bbox
        ? located.page
        : undefined;
    const load = pageWithBox
      ? fetchOriginalPdfPage(source, pageWithBox, abort.signal)
      : fetchOriginalDocument(source, abort.signal);
    void load
      .then((blob) => {
        if (abort.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setOriginalObjectUrl(objectUrl);
      })
      .catch((reason) => {
        if (!abort.signal.aborted) setError(errorText(reason));
      });
    return () => {
      abort.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [located, source]);

  useEffect(() => {
    if (!validKind) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
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
          conflictRunning(detail.processing) ||
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

  async function action(work: () => Promise<{ job: SourceJob }>) {
    setBusy(true);
    setError("");
    try {
      const { job } = await work();
      setSource((current) => current && withJob(current, job));
      refresh();
    } catch (reason) {
      if (reason instanceof DuplicateContentError)
        setSource(
          (current) =>
            current && {
              ...current,
              processing: {
                ...current.processing,
                status: "duplicate",
                duplicate_of: reason.duplicateOf,
              },
            },
        );
      else if (reason instanceof NearDuplicateError)
        setSource(
          (current) =>
            current && {
              ...current,
              processing: {
                ...current.processing,
                status: "near_duplicate",
                mode,
                overlaps: reason.matches,
                overlap_confirmed: undefined,
              },
            },
        );
      else setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  function updateRecord(record: VoiceRecord) {
    setSource((current) => {
      if (!current) return current;
      const records = current.records.map((item) =>
        item.id === record.id
          ? { ...record, references: item.references ?? [] }
          : item,
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

  function showLocated(reference: LocatedReference) {
    setLocated(reference);
    setQuote(reference.quote ?? "");
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
  const status = source.processing.status;
  const ingestion = {
    ...source.processing,
    // The review panel knows job states only; a (near-)duplicate was not extracted here.
    status:
      status === "unavailable"
        ? ("failed" as const)
        : status === "duplicate" || status === "near_duplicate"
          ? ("off" as const)
          : status,
  };
  const conversation: VoiceConversation = source.conversation
    ? {
        ...source.conversation,
        ingestion,
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
        ingestion,
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
            <Button
              variant="outline"
              onClick={() =>
                void downloadOriginalDocument(source).catch((reason) =>
                  setError(errorText(reason)),
                )
              }
            >
              <Download /> File gốc
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
        <ConflictJobNote processing={source.processing} />
        {source.stage === "extracted" ? (
          <p>
            <strong>
              {counts.draft
                ? `${counts.draft} mục chờ duyệt`
                : REVIEWS[source.review]}
            </strong>{" "}
            · {MODES[source.mode ?? "knowledge"]}. Đối chiếu với bằng chứng
            trước khi duyệt; mỗi quyết định được lưu lại.
          </p>
        ) : (
          <>
            {running ? (
              <p data-testid="source-progress">
                <Loader2 size={16} className="animate-spin" />{" "}
                <strong>{progressLabel(source.processing)}</strong>. Worker xử
                lý nền; có thể rời trang này.
              </p>
            ) : source.stage === "raw" && !source.can_convert ? (
              <p>{source.conversion_note}</p>
            ) : source.stage === "raw" ? (
              <p>
                Trích xuất sẽ tự chọn bộ chuyển đổi theo nội dung file rồi chạy
                bước Extract, không cần Convert thủ công. Convert chỉ tạo bản
                text để xem trước. File gốc được giữ nguyên.
              </p>
            ) : null}
            {canExtract(source) && (
              <>
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
                    {Object.entries(MODE_OPTIONS).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="vc-muted">
                  Mode được chọn riêng cho từng nguồn. Hỏi đáp vẫn dùng cấu hình
                  truy xuất của knowledge base; không phải mọi mode đều được tìm
                  kiếm trong mọi KB.
                </p>
              </>
            )}
            {(source.can_convert || canExtract(source)) && (
              <div className="km-actions">
                {source.can_convert && (
                  <Button
                    variant="outline"
                    disabled={busy || running}
                    onClick={() =>
                      void action(() => convertManagedSource(kb, source.id))
                    }
                    data-testid="convert-source"
                  >
                    Chuyển thành text
                  </Button>
                )}
                {canExtract(source) && (
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
                    {source.stage === "raw"
                      ? "Chuyển đổi & trích xuất"
                      : "Trích xuất"}
                  </Button>
                )}
              </div>
            )}
          </>
        )}
        <JobNote processing={source.processing} />
        {source.processing.status === "duplicate" &&
          source.processing.duplicate_of && (
            <DuplicateNote kb={kb} duplicate={source.processing.duplicate_of} />
          )}
        <OverlapNote
          kb={kb}
          processing={source.processing}
          busy={busy || running}
          onConfirm={() =>
            void action(() =>
              extractManagedSource(
                kb,
                source.kind,
                source.id,
                source.processing.mode ?? mode,
                { confirmOverlap: true },
              ),
            )
          }
        />
        {source.processing.status === "failed" &&
          !source.processing.job &&
          !source.processing.error && (
            <p className="vc-inline-error">Lần xử lý trước bị lỗi.</p>
          )}
        {source.processing.status === "interrupted" && (
          <p className="vc-inline-error">
            Lần xử lý trước bị gián đoạn. Có thể chạy lại.
          </p>
        )}
        {source.processing.status === "unavailable" &&
          source.processing.error && (
            <p className="vc-inline-error">{source.processing.error}</p>
          )}
      </section>
      {notice && (
        <p className="vc-notice warn" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="vc-notice error" role="alert">
          {error}
        </p>
      )}
      {source.conversation?.audio.available && audioObjectUrl && (
        <audio
          ref={audio}
          className="vc-audio"
          controls
          src={audioObjectUrl}
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
                {located && (
                  <LocatedEvidencePreview
                    reference={located}
                    url={originalObjectUrl}
                    contentType={source.content_type}
                    renderedPage={
                      source.content_type === "application/pdf" &&
                      (located.type === "page" || located.type === "image") &&
                      !!located.bbox
                    }
                  />
                )}
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
                      onLocated={showLocated}
                    />
                  ))}
                </ul>
              </section>
            ) : (
              <KnowledgePanel
                conversation={conversation}
                counts={counts}
                onEvidence={showEvidence}
                onLocated={showLocated}
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
  onLocated,
}: {
  source: ManagedSourceDetail;
  chunk: ManagedChunk;
  onChanged(source: ManagedSourceDetail): void;
  onLocated(reference: LocatedReference): void;
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
      {!!chunk.references?.length && (
        <div className="vc-evidence" data-testid="located-references">
          <span className="vc-evidence-label">Vị trí trong file gốc</span>
          {chunk.references.map((reference, index) => (
            <button
              key={index}
              type="button"
              className="vc-quote"
              onClick={() => onLocated(reference)}
            >
              {referenceTitle(reference)}
            </button>
          ))}
        </div>
      )}
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

function referenceTitle(reference: LocatedReference) {
  if (reference.type === "page") return `Trang ${reference.page}`;
  if (reference.type === "image") return "Vùng ảnh";
  if (reference.type === "paragraph")
    return reference.table && reference.cell
      ? `Bảng ${reference.table}, ô ${reference.cell}`
      : `Đoạn ${reference.index}`;
  if (reference.type === "cells") return `${reference.sheet} · ${reference.range}`;
  if (reference.type === "source" && reference.location_missing)
    return "Không xác định được vị trí";
  return "Toàn bộ đoạn";
}

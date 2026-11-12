import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  listConflicts,
  reviewConflict,
  recheckConflicts,
  type ConflictPage,
  type ConflictSide,
  type ConflictStatus,
  type ConflictDecision,
  type KnowledgeConflict,
} from "@/services/knowledge-conflicts";
import {
  sourceLink,
  type ProcessingState,
} from "@/services/knowledge-management";
import {
  dateTime,
  recordEvidence,
  recordScope,
  STATUS_LABELS,
  TYPE_LABELS,
} from "@/services/voice-display";

const STATUSES: Record<ConflictStatus, string> = {
  open: "Nghi mâu thuẫn",
  needs_review: "Cần kiểm tra bằng chứng",
  pending: "Chưa có kết luận",
  confirmed: "Đã xác nhận mâu thuẫn",
  dismissed: "Đã bỏ qua",
  resolved: "Đã thay thế bản ghi",
  no_conflict: "Không phát hiện mâu thuẫn",
};
const RELATIONS = {
  contradiction: "Mâu thuẫn",
  case_exception: "Ngoại lệ của quy tắc",
  scope_difference: "Khác phạm vi",
  refinement: "Bổ sung chi tiết",
  equivalent: "Tương đương",
  unrelated: "Không liên quan",
};
const ASPECTS: Record<string, string> = {
  value: "Giá trị",
  procedure_order: "Thứ tự thao tác",
  cause: "Nguyên nhân",
  action: "Hành động",
  applicability: "Điều kiện áp dụng",
  other: "Khác",
};
const DECISIONS: Record<ConflictDecision, string> = {
  confirm: "Xác nhận mâu thuẫn",
  dismiss: "Bỏ qua mâu thuẫn",
  supersede: "Thay thế bản ghi",
};
const errorText = (reason: unknown) =>
  reason instanceof Error ? reason.message : String(reason);
export const conflictRunning = (processing: ProcessingState) =>
  ["queued", "running"].includes(processing.conflict_job?.status ?? "");

/** Kept separate from extraction/review state: a finished extract may still be checking conflicts. */
export function ConflictJobNote({
  processing,
}: {
  processing: ProcessingState;
}) {
  const job = processing.conflict_job;
  if (job && conflictRunning(processing))
    return (
      <p className="vc-muted" role="status">
        <Loader2 size={14} className="animate-spin" />{" "}
        {job.status === "queued"
          ? "Chờ kiểm tra mâu thuẫn"
          : "Đang kiểm tra mâu thuẫn"}
        {job.retry_at ? ` · Thử lại lúc ${dateTime(job.retry_at)}` : ""}
        {job.error ? ` · ${job.error}` : ""}
      </p>
    );
  if (job?.status === "failed")
    return (
      <p className="vc-inline-error" role="alert">
        Kiểm tra mâu thuẫn lỗi: {job.error || "Không rõ nguyên nhân"}
      </p>
    );
  return null;
}

function RecordSide({
  kb,
  side,
  label,
  isNew,
}: {
  kb: string;
  side: ConflictSide;
  label: string;
  isNew: boolean;
}) {
  if (side.missing)
    return (
      <section className="km-conflict-side">
        <h4>{label}</h4>
        <p className="vc-inline-error">Bản ghi không còn tồn tại.</p>
        <small>{side.id}</small>
      </section>
    );
  const evidence = recordEvidence(side);
  return (
    <section className="km-conflict-side" aria-label={label}>
      <div className="km-source-status">
        <strong>{label}</strong>
        {isNew && (
          <span className="vc-chip draft">Bản ghi mới khi phát hiện</span>
        )}
        <span className={`vc-chip ${side.status}`}>
          {STATUS_LABELS[side.status]}
        </span>
      </div>
      <h4>{side.subject}</h4>
      <p className="vc-muted">{TYPE_LABELS[side.type] ?? side.type}</p>
      <p className="km-conflict-claim">{side.claim}</p>
      {side.quote && (
        <blockquote>
          <small>Trích đoạn dùng để so sánh</small>
          <p>{side.quote}</p>
        </blockquote>
      )}
      <details>
        <summary>Phạm vi và bằng chứng nguồn</summary>
        <dl className="km-conflict-scope">
          {recordScope(side).map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        {(side.valid_from || side.valid_to) && (
          <p>
            Hiệu lực: {side.valid_from ?? "Không nêu ngày bắt đầu"} →{" "}
            {side.valid_to ?? "Không nêu ngày kết thúc"}
          </p>
        )}
        {evidence.map((item, index) => (
          <blockquote key={index}>
            <small>{item.turnId ?? "Bằng chứng"}</small>
            <p>{item.quote}</p>
          </blockquote>
        ))}
        {side.evidence_id && (
          <Link
            to={sourceLink({ kb, kind: "conversation", id: side.evidence_id })}
          >
            Mở nguồn bằng chứng
          </Link>
        )}
        <p className="vc-muted">ID bản ghi: {side.id}</p>
      </details>
    </section>
  );
}

function ConflictCard({
  kb,
  conflict,
  disabled,
  onChanged,
}: {
  kb: string;
  conflict: KnowledgeConflict;
  disabled: boolean;
  onChanged(message: string): void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [replaceOpen, setReplaceOpen] = useState(false);
  const [keepId, setKeepId] = useState("");
  const missing = conflict.a.missing || conflict.b.missing;
  const locked =
    disabled ||
    busy ||
    missing ||
    conflict.stale ||
    conflict.status === "resolved";
  async function review(decision: ConflictDecision) {
    setBusy(true);
    setError("");
    try {
      await reviewConflict(kb, conflict.id, decision, { note, keepId });
      setReplaceOpen(false);
      onChanged(`${DECISIONS[decision]}: đã lưu quyết định.`);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }
  async function recheck() {
    setBusy(true);
    setError("");
    try {
      const result = await recheckConflicts(kb, [conflict.a_id, conflict.b_id]);
      onChanged(
        `Đã xếp ${result.queued.length} nguồn để kiểm tra lại; ${result.busy.length} nguồn đang có job xử lý.`,
      );
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <article
      className="km-conflict-card"
      aria-label={`Cặp mâu thuẫn ${conflict.id}`}
    >
      <header className="km-conflict-head">
        <div>
          <span
            className={`vc-chip ${["open", "pending", "needs_review"].includes(conflict.status) ? "draft" : ""}`}
          >
            {STATUSES[conflict.status]}
          </span>
          <h3>
            {conflict.relation
              ? RELATIONS[conflict.relation]
              : "Chưa phân loại"}
            {conflict.aspect
              ? ` · ${ASPECTS[conflict.aspect] ?? conflict.aspect}`
              : ""}
          </h3>
        </div>
        <span className="vc-muted">{dateTime(conflict.updated_at)}</span>
      </header>
      <p>{conflict.reason || "Chưa có lý do từ bộ kiểm tra."}</p>
      <p className="vc-muted">
        Độ tin cậy của máy:{" "}
        {conflict.confidence === null
          ? "Chưa có"
          : `${Math.round(conflict.confidence * 100)}%`}{" "}
        ·{" "}
        {conflict.confirmed_by_pair
          ? "Đã qua bước xác nhận theo cặp"
          : "Chưa có xác nhận theo cặp"}{" "}
        ·{" "}
        {conflict.linked ? "Đã liên kết mâu thuẫn" : "Chưa liên kết mâu thuẫn"}
      </p>
      {conflict.stale && (
        <p className="vc-notice warn" role="status">
          Nội dung bản ghi đã thay đổi sau lần so sánh này. Hãy kiểm tra lại
          trước khi duyệt.
        </p>
      )}
      <div className="km-conflict-sides">
        <RecordSide
          kb={kb}
          side={conflict.a}
          label="Bản ghi A"
          isNew={conflict.new_id === conflict.a_id}
        />
        <RecordSide
          kb={kb}
          side={conflict.b}
          label="Bản ghi B"
          isNew={conflict.new_id === conflict.b_id}
        />
      </div>
      {conflict.status === "resolved" ? (
        <p className="vc-muted">
          Cặp này đã được xử lý bằng quyết định thay thế bản ghi.
        </p>
      ) : (
        <div className="km-conflict-review">
          <label className="vc-field">
            <span>Ghi chú quyết định</span>
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              maxLength={1000}
              disabled={locked}
              rows={2}
            />
          </label>
          <div className="km-conflict-actions">
            <Button
              size="sm"
              disabled={locked}
              onClick={() => void review("confirm")}
            >
              Xác nhận mâu thuẫn
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={locked}
              onClick={() => void review("dismiss")}
            >
              Bỏ qua
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={locked}
              onClick={() => setReplaceOpen(true)}
            >
              Thay thế một bản ghi…
            </Button>
            {conflict.stale && (
              <Button
                size="sm"
                variant="outline"
                disabled={disabled || busy || missing}
                onClick={() => void recheck()}
              >
                <RefreshCw size={14} /> Kiểm tra lại cặp này
              </Button>
            )}
            {busy && (
              <Loader2
                size={16}
                className="animate-spin"
                aria-label="Đang lưu"
              />
            )}
          </div>
        </div>
      )}
      {error && (
        <p className="vc-notice error" role="alert">
          {error}
        </p>
      )}
      {!!conflict.review.length && (
        <details className="km-conflict-history">
          <summary>Lịch sử quyết định ({conflict.review.length})</summary>
          <ul>
            {conflict.review.map((event, index) => (
              <li key={index}>
                {dateTime(event.at)} · {DECISIONS[event.decision]} ·{" "}
                {event.actor}
                {event.note ? ` · ${event.note}` : ""}
                {event.kept ? ` · Giữ ${event.kept}` : ""}
                {event.retired ? ` · Thay thế ${event.retired}` : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
      <Dialog
        open={replaceOpen}
        onOpenChange={(open) => {
          if (!busy) setReplaceOpen(open);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Chọn bản ghi giữ lại</DialogTitle>
            <DialogDescription>
              Bản ghi còn lại sẽ ngừng được tìm kiếm và được giữ trong lịch sử
              để truy vết. Bản ghi giữ lại không tự được phê duyệt.
            </DialogDescription>
          </DialogHeader>
          <fieldset className="km-conflict-choices" disabled={busy}>
            <legend>Bản ghi giữ lại</legend>
            {[conflict.a, conflict.b].map((side, index) => (
              <label key={side.id}>
                <input
                  type="radio"
                  name={`keep-${conflict.id}`}
                  value={side.id}
                  checked={keepId === side.id}
                  disabled={
                    side.missing ||
                    side.status === "deprecated" ||
                    !!side.superseded_by
                  }
                  onChange={() => setKeepId(side.id)}
                />
                <span>
                  <strong>Bản ghi {index === 0 ? "A" : "B"}</strong>
                  <br />
                  {side.missing ? "Bản ghi không còn tồn tại" : side.claim}
                </span>
              </label>
            ))}
          </fieldset>
          {error && (
            <p className="vc-inline-error" role="alert">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setReplaceOpen(false)}
            >
              Hủy
            </Button>
            <Button
              disabled={locked || !keepId}
              onClick={() => void review("supersede")}
            >
              Lưu quyết định thay thế
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </article>
  );
}

export function ConflictReviewPanel({
  kb,
  refreshKey,
  activeJobs,
  onChanged,
}: {
  kb: string;
  refreshKey: string;
  activeJobs: boolean;
  onChanged(): void;
}) {
  const [status, setStatus] = useState<ConflictStatus | "all">("open");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ConflictPage>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const [rechecking, setRechecking] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    setError("");
    listConflicts(
      kb,
      { status: status === "all" ? undefined : status, offset },
      abort.signal,
    )
      .then((result) => {
        if (!abort.signal.aborted) setPage(result);
      })
      .catch((reason) => {
        if (!abort.signal.aborted) setError(errorText(reason));
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [kb, status, offset, reload, refreshKey]);
  useEffect(() => {
    if (!activeJobs) return;
    const timer = setInterval(() => setReload((value) => value + 1), 3000);
    return () => clearInterval(timer);
  }, [activeJobs]);
  function changed(message: string) {
    setNotice(message);
    setReload((value) => value + 1);
    onChanged();
  }
  async function recheck() {
    setRechecking(true);
    setError("");
    try {
      const result = await recheckConflicts(kb);
      changed(
        result.sources
          ? `Đã xếp ${result.queued.length} nguồn để kiểm tra lại; ${result.busy.length} nguồn đang có job xử lý.`
          : "Không có bản ghi đã thay đổi cần kiểm tra lại.",
      );
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setRechecking(false);
    }
  }
  return (
    <section className="km-conflicts" aria-label="Review mâu thuẫn">
      <header className="km-conflict-head">
        <div>
          <h2>Review mâu thuẫn</h2>
          <p className="vc-muted">
            Đối chiếu hai bản ghi và bằng chứng trước khi quyết định. Xác nhận
            mâu thuẫn không đồng nghĩa phê duyệt tri thức.
          </p>
        </div>
        <div className="km-conflict-actions">
          <Button
            variant="outline"
            size="sm"
            disabled={loading || rechecking}
            onClick={() => setReload((value) => value + 1)}
          >
            <RefreshCw /> Làm mới mâu thuẫn
          </Button>
          {page?.enabled && (
            <Button
              variant="outline"
              size="sm"
              disabled={loading || rechecking}
              onClick={() => void recheck()}
            >
              {rechecking && <Loader2 className="animate-spin" />}Kiểm tra lại
              bản ghi đã đổi
            </Button>
          )}
        </div>
      </header>
      {notice && (
        <p className="vc-notice" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="vc-notice error" role="alert">
          {error}
        </p>
      )}
      {page?.enabled === false ? (
        <p className="vc-notice">
          Knowledge base này chưa bật phát hiện mâu thuẫn.
        </p>
      ) : (
        <>
          <label className="vc-field km-conflict-filter">
            <span>Trạng thái mâu thuẫn</span>
            <select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value as ConflictStatus | "all");
                setOffset(0);
              }}
            >
              <option value="all">Tất cả trạng thái</option>
              {Object.entries(STATUSES).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                  {page?.counts[key as ConflictStatus] !== undefined
                    ? ` (${page.counts[key as ConflictStatus]})`
                    : ""}
                </option>
              ))}
            </select>
          </label>
          {activeJobs && (
            <p className="vc-muted" role="status">
              Đang có job xử lý; danh sách tự cập nhật.
            </p>
          )}
          {loading ? (
            <p className="vc-muted" role="status">
              <Loader2 size={16} className="animate-spin" /> Đang tải mâu thuẫn…
            </p>
          ) : !error && !page?.conflicts.length ? (
            <p className="km-empty">
              Chưa có cặp nào ở trạng thái này. Có thể đổi bộ lọc để xem các kết
              quả khác.
            </p>
          ) : null}
          <div className="km-conflict-list">
            {page?.conflicts.map((conflict) => (
              <ConflictCard
                key={`${conflict.id}:${conflict.updated_at}:${conflict.stale}`}
                kb={kb}
                conflict={conflict}
                disabled={loading || !!error || rechecking}
                onChanged={changed}
              />
            ))}
          </div>
          {page && (
            <nav
              className="km-conflict-pagination"
              aria-label="Phân trang mâu thuẫn"
            >
              <Button
                variant="outline"
                size="sm"
                disabled={loading || offset === 0}
                onClick={() => setOffset(Math.max(0, offset - page.limit))}
              >
                Trang trước
              </Button>
              <span>Trang {Math.floor(offset / page.limit) + 1}</span>
              <Button
                variant="outline"
                size="sm"
                disabled={loading || page.next_offset === null}
                onClick={() => {
                  if (page.next_offset !== null) setOffset(page.next_offset);
                }}
              >
                Trang sau
              </Button>
            </nav>
          )}
        </>
      )}
    </section>
  );
}

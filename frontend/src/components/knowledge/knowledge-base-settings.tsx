import { useEffect, useState } from "react";
import { Loader2, Pencil, Trash2 } from "lucide-react";
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
  deleteKnowledgeBase,
  getKnowledgeBase,
  MAX_KB_DESCRIPTION,
  MAX_KB_TITLE,
  updateKnowledgeBase,
  type KnowledgeBaseDetail,
} from "@/services/knowledge-base-admin";
import { dateTime } from "@/services/voice-display";

const sourceStatus = {
  received: "Đã nhận",
  processing: "Đang xử lý",
  processed: "Đã xử lý",
  failed: "Lỗi",
};
const chunkKind = {
  knowledge: "Mục tri thức",
  document_passage: "Đoạn tài liệu",
};
const chunkStatus = { draft: "Nháp", approved: "Đã duyệt", deprecated: "Đã loại" };

function counts(values: Record<string, number>, labels: Record<string, string>) {
  return Object.entries(values)
    .map(([status, count]) => `${labels[status] ?? status} ${count}`)
    .join(" · ");
}

export function KnowledgeBaseSettings({
  name,
  role,
  revision,
  onChanged,
}: {
  name: string;
  role?: string;
  revision: number;
  onChanged(): void;
}) {
  const [detail, setDetail] = useState<KnowledgeBaseDetail>();
  const [dialog, setDialog] = useState<"edit" | "delete">();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    const abort = new AbortController();
    getKnowledgeBase(name, abort.signal)
      .then((data) => {
        if (abort.signal.aborted) return;
        setDetail(data);
        setLoadError("");
      })
      .catch((error: Error) => {
        if (!abort.signal.aborted) setLoadError(error.message);
      });
    return () => abort.abort();
  }, [name, revision]);

  const canSubmit =
    dialog === "delete" ? confirmation === name : !!dialog && !!title.trim();

  async function submit() {
    if (!canSubmit || busy) return;
    setBusy(true);
    setActionError("");
    try {
      if (dialog === "delete") {
        await deleteKnowledgeBase(name);
      } else {
        setDetail(await updateKnowledgeBase(name, { title, description }));
      }
      setDialog(undefined);
      onChanged();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Chi tiết knowledge base">
      {loadError && <p className="vc-notice error" role="alert">{loadError}</p>}
      {!detail && !loadError && (
        <p className="vc-muted" role="status">
          <Loader2 className="animate-spin" size={16} /> Đang tải chi tiết…
        </p>
      )}
      {detail && (
        <article className="km-kb-card">
          <header className="km-kb-card-header">
            <div>
              <h2>{detail.title}</h2>
              {detail.description && <p>{detail.description}</p>}
            </div>
            {(role === "admin" || role === "expert") && (
              <div className="km-kb-actions">
                <Button variant="outline" onClick={() => {
                  setTitle(detail.title);
                  setDescription(detail.description);
                  setActionError("");
                  setDialog("edit");
                }}><Pencil /> Sửa knowledge base</Button>
                {role === "admin" && (
                  <Button variant="destructive" onClick={() => {
                    setConfirmation("");
                    setActionError("");
                    setDialog("delete");
                  }}><Trash2 /> Xóa knowledge base</Button>
                )}
              </div>
            )}
          </header>
          <dl className="km-kb-metadata">
            <div><dt>Mã</dt><dd><code>{detail.name}</code></dd></div>
            <div><dt>Mẫu</dt><dd>{detail.template}</dd></div>
            <div><dt>Người tạo</dt><dd>{detail.created_by}</dd></div>
            <div><dt>Ngày tạo</dt><dd>{dateTime(detail.created_at)}</dd></div>
            <div><dt>Cập nhật</dt><dd>{dateTime(detail.updated_at)}</dd></div>
          </dl>
          <div className="km-kb-counts">
            <div>
              <h3>Nguồn dữ liệu</h3>
              <p>{counts(detail.source_counts, sourceStatus)}</p>
            </div>
            {Object.entries(detail.chunk_counts).map(([kind, values]) => (
              <div key={kind}>
                <h3>{chunkKind[kind as keyof typeof chunkKind] ?? kind}</h3>
                <p>{counts(values, chunkStatus)}</p>
              </div>
            ))}
          </div>
        </article>
      )}
      <Dialog open={!!dialog} onOpenChange={(open) => {
        if (!open && !busy) setDialog(undefined);
      }}>
        <DialogContent className="vc-kb-dialog">
          <form className="vc-kb-form" onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}>
            <DialogHeader>
              <DialogTitle>{dialog === "delete" ? "Xóa knowledge base" : "Sửa knowledge base"}</DialogTitle>
              <DialogDescription>
                {dialog === "delete"
                  ? `Nhập mã “${name}” để xác nhận xóa.`
                  : `Cập nhật thông tin hiển thị cho ${name}.`}
              </DialogDescription>
            </DialogHeader>
            {dialog === "delete" ? (
              <label className="vc-field">
                <span>Mã xác nhận</span>
                <input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
              </label>
            ) : (
              <>
                <label className="vc-field">
                  <span>Tiêu đề</span>
                  <input required maxLength={MAX_KB_TITLE} value={title}
                    onChange={(event) => setTitle(event.target.value)} />
                </label>
                <label className="vc-field">
                  <span>Mô tả</span>
                  <textarea maxLength={MAX_KB_DESCRIPTION} value={description}
                    onChange={(event) => setDescription(event.target.value)} />
                </label>
              </>
            )}
            {actionError && <p className="vc-notice error" role="alert">{actionError}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" disabled={busy}
                onClick={() => setDialog(undefined)}>Hủy</Button>
              <Button type="submit" variant={dialog === "delete" ? "destructive" : "default"}
                disabled={busy || !canSubmit}>
                {dialog === "delete" ? "Xác nhận xóa" : "Lưu thay đổi"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </section>
  );
}

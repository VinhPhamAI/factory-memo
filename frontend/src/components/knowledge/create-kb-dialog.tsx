import { useState } from "react";
import { Loader2 } from "lucide-react";
import type { KnowledgeBase } from "@/types";
import {
  createKnowledgeBase,
  kbNameProblem,
  MAX_KB_DESCRIPTION,
  MAX_KB_TITLE,
  suggestKbName,
} from "@/services/knowledge-base-admin";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** Create a knowledge base with the default extraction policy. */
export function CreateKnowledgeBaseDialog({
  open,
  taken,
  onClose,
  onCreated,
}: {
  open: boolean;
  /** Ids already in use, checked before asking the server. */
  taken: readonly string[];
  onClose(): void;
  onCreated(kb: KnowledgeBase): void;
}) {
  return (
    <Dialog open={open} onOpenChange={(value) => !value && onClose()}>
      <DialogContent className="vc-kb-dialog">
        {/* Remounted on open, so every opening starts with an empty form. */}
        {open && (
          <CreateForm taken={taken} onClose={onClose} onCreated={onCreated} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function CreateForm({
  taken,
  onClose,
  onCreated,
}: {
  taken: readonly string[];
  onClose(): void;
  onCreated(kb: KnowledgeBase): void;
}) {
  const [title, setTitle] = useState("");
  const [name, setName] = useState("");
  // The id follows the title until the user edits it.
  const [nameEdited, setNameEdited] = useState(false);
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [touched, setTouched] = useState(false);

  const id = nameEdited ? name : suggestKbName(title);
  const nameProblem = kbNameProblem(id, taken);
  const titleProblem = title.trim() ? "" : "Nhập tên knowledge base.";

  async function submit() {
    setTouched(true);
    if (titleProblem || nameProblem || busy) return;
    setBusy(true);
    setError("");
    try {
      onCreated(
        await createKnowledgeBase({
          name: id,
          title: title.trim(),
          description: description.trim(),
        }),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setBusy(false);
    }
  }

  return (
    <form
      className="vc-kb-form"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
      data-testid="create-kb"
    >
      <DialogHeader>
        <DialogTitle>Tạo knowledge base mới</DialogTitle>
        <DialogDescription>
          Tạo một bộ sưu tập để lưu nguồn dữ liệu, trích xuất tri thức và hỏi đáp.
        </DialogDescription>
      </DialogHeader>

      <label className="vc-field">
        <span>Tên</span>
        <input
          value={title}
          maxLength={MAX_KB_TITLE}
          autoFocus
          disabled={busy}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="VD: Bảo trì máy ép thủy lực"
          aria-invalid={touched && !!titleProblem}
          data-testid="kb-title"
        />
        {touched && titleProblem && (
          <small className="vc-inline-error">{titleProblem}</small>
        )}
      </label>

      <label className="vc-field">
        <span>Mã</span>
        <input
          value={id}
          maxLength={60}
          disabled={busy}
          spellCheck={false}
          autoCapitalize="off"
          className="vc-mono"
          onChange={(event) => {
            setNameEdited(true);
            setName(event.target.value.trim());
          }}
          placeholder="bao_tri_may_ep"
          aria-invalid={(touched || nameEdited) && !!id && !!nameProblem}
          data-testid="kb-name"
        />
        {(touched || (nameEdited && id)) && nameProblem ? (
          <small className="vc-inline-error">{nameProblem}</small>
        ) : (
          <small>
            Mã dùng để định danh knowledge base và được cố định sau khi tạo.
          </small>
        )}
      </label>

      <label className="vc-field">
        <span>
          Mô tả <em>(không bắt buộc)</em>
        </span>
        <textarea
          value={description}
          rows={3}
          maxLength={MAX_KB_DESCRIPTION}
          disabled={busy}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="Knowledge base này chứa gì, dành cho ai."
          data-testid="kb-description"
        />
        <small>Hiện khi chọn knowledge base để ghi lại hoặc hỏi đáp.</small>
      </label>

      {error && (
        <p className="vc-inline-error" role="alert" data-testid="kb-error">
          {error}
        </p>
      )}

      <div className="leave-actions">
        <Button
          type="button"
          variant="outline"
          onClick={onClose}
          disabled={busy}
        >
          Huỷ
        </Button>
        <Button type="submit" disabled={busy} data-testid="kb-create">
          {busy && <Loader2 className="animate-spin" />}
          {busy ? "Đang tạo…" : "Tạo knowledge base"}
        </Button>
      </div>
    </form>
  );
}

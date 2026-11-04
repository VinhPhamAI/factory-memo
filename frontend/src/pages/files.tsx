import { useEffect, useRef, useState } from "react";
import {
  Download,
  FileText,
  FolderOpen,
  Loader2,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import { useAuthClient } from "@/hooks/auth";
import {
  ACCEPTED_EXTENSIONS,
  deleteFile,
  downloadFile,
  FileApiError,
  listFiles,
  uploadFile,
  type LibraryFile,
} from "@/services/file-api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ToastStack, useToasts } from "@/components/ui/toast";

const TYPE_LABELS: Record<string, string> = {
  "application/pdf": "PDF",
  "image/png": "PNG",
  "image/jpeg": "JPEG",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
    "DOCX",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
};

const messageOf = (error: unknown) =>
  error instanceof FileApiError
    ? error.message
    : "Thao tác thất bại. Hãy thử lại.";

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** The signed-in user's own files: upload, download and delete. */
export function FilesPage() {
  const client = useAuthClient();
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<LibraryFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const [uploading, setUploading] = useState(false);
  const toasts = useToasts();
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [pendingDelete, setPendingDelete] = useState<LibraryFile | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError("");
    listFiles(client)
      .then((items) => active && setFiles(items))
      .catch((error: unknown) => active && setLoadError(messageOf(error)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [client, reload]);

  const setRowError = (id: string, message: string) =>
    setRowErrors((current) => {
      const next = { ...current };
      if (message) next[id] = message;
      else delete next[id];
      return next;
    });

  async function upload(selected: File[]) {
    setUploading(true);
    for (const file of selected) {
      try {
        const stored = await uploadFile(client, file);
        setFiles((current) => [stored, ...current]);
        toasts.show("success", `Đã tải lên “${file.name}”.`);
      } catch (error) {
        toasts.show("error", `“${file.name}”: ${messageOf(error)}`);
      }
    }
    setUploading(false);
  }

  async function download(file: LibraryFile) {
    setRowError(file.id, "");
    try {
      await downloadFile(client, file);
    } catch (error) {
      setRowError(file.id, messageOf(error));
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    const file = pendingDelete;
    setDeleting(true);
    const removeRow = () =>
      setFiles((current) => current.filter((item) => item.id !== file.id));
    try {
      await deleteFile(client, file.id);
      removeRow();
      toasts.show("success", `Đã xóa “${file.filename}”.`);
    } catch (error) {
      // A file that is already gone (for example deleted in another tab)
      // leaves the list too.
      if (error instanceof FileApiError && error.status === 404) removeRow();
      toasts.show("error", `“${file.filename}”: ${messageOf(error)}`);
    } finally {
      setDeleting(false);
      setPendingDelete(null);
    }
  }

  return (
    <main className="vc-page km-page" data-testid="file-library">
      <header className="km-heading">
        <div>
          <h1>Thư viện file</h1>
          <p>
            File riêng của bạn. File trong thư viện chưa được đưa vào knowledge
            base nào.
          </p>
        </div>
        <div className="files-actions">
          <Button
            variant="outline"
            onClick={() => setReload((value) => value + 1)}
            disabled={loading}
          >
            <RefreshCw /> Làm mới
          </Button>
          <Button
            onClick={() => input.current?.click()}
            disabled={uploading}
            data-testid="files-upload"
          >
            {uploading ? <Loader2 className="animate-spin" /> : <Upload />}
            Tải file lên
          </Button>
          <input
            ref={input}
            type="file"
            multiple
            hidden
            accept={ACCEPTED_EXTENSIONS.join(",")}
            data-testid="files-input"
            onChange={(event) => {
              const selected = Array.from(event.target.files ?? []);
              event.target.value = "";
              if (selected.length) void upload(selected);
            }}
          />
        </div>
      </header>
      <p className="vc-muted">
        Nhận PDF, XLSX, DOCX, JPEG, PNG; tối đa 100 MiB mỗi file.
      </p>

      {loadError && (
        <p className="vc-notice error" role="alert">
          {loadError}
        </p>
      )}

      {loading && !files.length ? (
        <p className="vc-muted">
          <Loader2 className="animate-spin" size={16} /> Đang tải danh sách…
        </p>
      ) : !files.length && !loadError ? (
        <div className="km-empty" data-testid="files-empty">
          <FolderOpen size={32} />
          <h2>Chưa có file nào</h2>
          <p>Bấm “Tải file lên” để thêm file đầu tiên.</p>
        </div>
      ) : (
        <ul className="km-source-list">
          {files.map((file) => (
            <li key={file.id} data-testid="library-file">
              <span className="km-source-icon">
                <FileText size={20} />
              </span>
              <div className="km-source-main">
                <div className="km-source-name">{file.filename}</div>
                <p className="vc-muted">
                  {TYPE_LABELS[file.content_type] ?? file.content_type} ·{" "}
                  {formatSize(file.size_bytes)} ·{" "}
                  {new Date(file.created_at).toLocaleString("vi-VN")}
                </p>
                {rowErrors[file.id] && (
                  <p className="vc-inline-error" role="alert">
                    {rowErrors[file.id]}
                  </p>
                )}
              </div>
              <div className="files-actions">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void download(file)}
                  aria-label={`Tải về ${file.filename}`}
                >
                  <Download /> Tải về
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPendingDelete(file)}
                  aria-label={`Xóa ${file.filename}`}
                >
                  <Trash2 /> Xóa
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && !deleting && setPendingDelete(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Xóa file khỏi thư viện?</DialogTitle>
            <DialogDescription>
              “{pendingDelete?.filename}” sẽ biến mất khỏi thư viện của bạn. Bản
              gốc vẫn được giữ cho các trích dẫn hoặc yêu cầu đã dùng file này.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={deleting}>
                Hủy
              </Button>
            </DialogClose>
            <Button
              variant="destructive"
              onClick={() => void confirmDelete()}
              disabled={deleting}
              data-testid="files-confirm-delete"
            >
              {deleting && <Loader2 className="animate-spin" />}
              Xóa
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />
    </main>
  );
}

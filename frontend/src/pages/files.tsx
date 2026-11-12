import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  FolderOpen,
  Loader2,
  Trash2,
  Search,
  MoreHorizontal,
  ExternalLink,
  Activity,
  Check,
  Download,
  Eye,
  BookOpen,
} from "lucide-react";
import { useAuthClient } from "@/hooks/auth";
import { attachLibraryFile } from "@/services/document-api";
import { listConfiguredKnowledgeBases } from "@/services/knowledge-api";
import type { KnowledgeBase } from "@/types";
import { FilePreview } from "@/components/file-preview";
import { LibraryFileDialog } from "@/components/library-file-dialog";
import { LibraryDropzone, type UploadProgress } from "@/components/library-dropzone";
import { listManagedSources, removeManagedSource, sourceLink, sourceProcessingLabel, type ManagedSource } from "@/services/knowledge-management";
import {
  ACCEPTED_EXTENSIONS,
  downloadFile,
  openFile,
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
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

const TYPE_LABELS: Record<string, string> = {
  "application/pdf": "PDF",
  "image/png": "PNG",
  "image/jpeg": "JPEG",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
    "DOCX",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
  "text/plain": "TXT",
  "text/markdown": "MD",
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

/** The signed-in user's own files: upload, preview, download and remove from knowledge. */
export function FilesPage() {
  const client = useAuthClient();
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<LibraryFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [storedCount, setStoredCount] = useState(0);
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [defaultKb, setDefaultKb] = useState("");
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const kbReady = !!defaultKb;
  const [kbError, setKbError] = useState("");
  const [sources, setSources] = useState<ManagedSource[]>([]);
  const [sourcesError, setSourcesError] = useState("");
  const [sourcesLoaded, setSourcesLoaded] = useState(false);
  // Files stored in this page session; shown before their source appears and
  // until the user removes them from the knowledge base.
  const [sessionIds, setSessionIds] = useState<Set<string>>(new Set());
  const [sourcesRevision, setSourcesRevision] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [stateFile, setStateFile] = useState<LibraryFile | null>(null);
  const [previewFile, setPreviewFile] = useState<LibraryFile | null>(null);
  const toasts = useToasts();
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [pendingRemove, setPendingRemove] = useState<LibraryFile[]>([]);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    const abort = new AbortController();
    setKbError("");
    listConfiguredKnowledgeBases(abort.signal, client)
      .then((items) => {
        if (abort.signal.aborted) return;
        const found = items.find((item) => item.is_default)?.name ?? "";
        setKnowledgeBases(items);
        setDefaultKb(found);
        if (!found) setKbError("Chưa có kho kiến thức mặc định. Liên hệ quản trị viên để chọn trước khi tải file.");
      })
      .catch(() => { if (!abort.signal.aborted) setKbError("Không kiểm tra được kho kiến thức mặc định. Hãy tải lại trang và thử lại."); });
    return () => abort.abort();
  }, [client]);

  useEffect(() => {
    if (!kbReady) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const all: ManagedSource[] = [];
        for (const kb of knowledgeBases) {
          let offset: number | null = 0;
          while (offset !== null) {
            const page = await listManagedSources(kb.name, abort.signal, offset, client);
            if (!Array.isArray(page.sources) || (page.next_offset !== null && (!Number.isInteger(page.next_offset) || page.next_offset <= offset))) throw new Error("Invalid source page");
            all.push(...page.sources);
            offset = page.next_offset;
          }
        }
        if (!abort.signal.aborted) { setSources(all); setSourcesError(""); setSourcesLoaded(true); }
      } catch {
        if (!abort.signal.aborted) setSourcesError("Không tải được trạng thái tri thức. Hãy tải lại trang.");
      } finally {
        if (!abort.signal.aborted) timer = setTimeout(() => void load(), 5000);
      }
    };
    void load();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [client, defaultKb, knowledgeBases, sourcesRevision, kbReady]);

  const sourcesFor = (file: LibraryFile) => sources.filter((source) => source.file_id === file.id);
  const sourceFor = (file: LibraryFile) => sourcesFor(file).find((source) => source.kb === defaultKb);
  // The library lists only files that belong to the default KB, plus files stored
  // or failed to attach in this session so their state and retry stay reachable.
  const belongsToDefaultKb = (file: LibraryFile) =>
    sources.some((source) => source.kb === defaultKb && source.file_id === file.id) ||
    sessionIds.has(file.id) ||
    !!rowErrors[file.id];
  const visibleFiles = kbReady ? files.filter(belongsToDefaultKb) : [];
  const defaultKbTitle = knowledgeBases.find((item) => item.name === defaultKb)?.title || defaultKb;
  const matching = visibleFiles.filter((file) => file.filename.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const filtering = kbReady && !sourcesLoaded && !sourcesError;

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
  }, [client]);

  const setRowError = (id: string, message: string) =>
    setRowErrors((current) => {
      const next = { ...current };
      if (message) next[id] = message;
      else delete next[id];
      return next;
    });

  /** Flag a stored file so its card animates in, then clear the flag. */
  const markFresh = (id: string) => {
    setFreshIds((current) => new Set(current).add(id));
    setTimeout(() => setFreshIds((current) => { const next = new Set(current); next.delete(id); return next; }), 2500);
  };

  async function upload(selected: File[]) {
    if (!kbReady || uploading) return;
    setUploading(true);
    setStoredCount(0);
    try {
      const current = await listConfiguredKnowledgeBases(new AbortController().signal, client);
      if (!current.some((item) => item.name === defaultKb && item.is_default)) {
        setKbError("Kho kiến thức mặc định đã thay đổi. Hãy tải lại trang trước khi tải file.");
        setDefaultKb("");
        setUploading(false);
        return;
      }
    } catch {
      setKbError("Không kiểm tra được kho kiến thức mặc định. Hãy tải lại trang và thử lại.");
      setUploading(false);
      return;
    }
    let stored = 0;
    for (const [index, file] of selected.entries()) {
      setProgress({ done: index, total: selected.length, name: file.name });
      let saved: LibraryFile;
      try {
        saved = await uploadFile(client, file);
        setFiles((current) => [saved, ...current]);
        setSessionIds((current) => new Set(current).add(saved.id));
        markFresh(saved.id);
        stored += 1;
      } catch (error) {
        toasts.show("error", `“${file.name}”: ${messageOf(error)}`);
        continue;
      }
      await queueFile(saved, defaultKb);
    }
    setProgress(null);
    setUploading(false);
    if (stored) {
      setStoredCount(stored);
      setTimeout(() => setStoredCount(0), 2800);
    }
  }

  async function queueFile(file: LibraryFile, targetKb: string) {
    setRowError(file.id, "");
    try {
      const result = await attachLibraryFile(client, targetKb, file.id, "knowledge");
      setSourcesRevision((revision) => revision + 1);
      if (result.duplicate) {
        setRowError(file.id, "File đã có trong kho kiến thức. Xem nguồn hiện có để kiểm tra trạng thái xử lý.");
      } else if (result.job_error || !result.job) {
        setRowError(file.id, `File đã lưu nhưng chưa được đưa vào xử lý: ${result.job_error || "Không nhận được xác nhận từ máy chủ."}`);
      } else {
        toasts.show("success", `Đã tải lên và đưa “${file.filename}” vào hàng đợi xử lý.`);
      }
    } catch (error) {
      setRowError(file.id, `File đã lưu nhưng chưa được đưa vào xử lý: ${error instanceof Error ? error.message : "Hãy thử lại."}`);
    }
  }

  async function open(file: LibraryFile) {
    try {
      await openFile(client, file);
    } catch (error) {
      toasts.show("error", `“${file.filename}”: ${messageOf(error)}`);
    }
  }

  async function download(file: LibraryFile) {
    try {
      await downloadFile(client, file);
    } catch (error) {
      toasts.show("error", `“${file.filename}”: ${messageOf(error)}`);
    }
  }

  async function confirmRemove() {
    if (!pendingRemove.length) return;
    setDeleting(true);
    const unique = new Map(pendingRemove.map((file) => [sourceFor(file)?.id, file]));
    for (const file of unique.values()) {
      const source = sourceFor(file);
      if (!source) continue;
      try {
        await removeManagedSource(source, client);
        toasts.show("success", `Đã gỡ “${file.filename}” khỏi kho kiến thức.`);
      } catch (error) {
        toasts.show("error", `“${file.filename}”: ${error instanceof Error ? error.message : "Không gỡ được nguồn."}`);
      }
    }
    const removed = new Set(pendingRemove.map((file) => file.id));
    setSessionIds((current) => new Set([...current].filter((id) => !removed.has(id))));
    setSelected(new Set());
    setPendingRemove([]);
    setDeleting(false);
    setSourcesRevision((revision) => revision + 1);
  }

  return (
    <main className="vc-page library-page" data-testid="file-library">
      <header className="library-heading">
        <h1 className="page-heading">Thư viện tài liệu</h1>
        <div className="library-toolbar">
          <label className="library-search">
            <Search size={18} aria-hidden="true" />
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm tài liệu" aria-label="Tìm tài liệu" />
          </label>
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
      <LibraryDropzone
        disabled={!kbReady}
        progress={progress}
        storedCount={storedCount}
        onFiles={(dropped) => void upload(dropped)}
        onBrowse={() => input.current?.click()}
      />
      {kbError && <p className="vc-notice error" role="alert">{kbError}</p>}
      {sourcesError && <p className="vc-notice error" role="alert">{sourcesError}</p>}

      {loadError && (
        <p className="vc-notice error" role="alert">
          {loadError}
        </p>
      )}

      {(loading || filtering) && !visibleFiles.length ? (
        <p className="vc-muted">
          <Loader2 className="animate-spin" size={16} /> Đang tải danh sách…
        </p>
      ) : !visibleFiles.length && !loadError ? (
        <div className="km-empty" data-testid="files-empty">
          <FolderOpen size={32} />
          <h2>Chưa có file nào</h2>
          {kbReady && <p>Tài liệu bạn tải lên sẽ xuất hiện ở đây.</p>}
        </div>
      ) : (
        <section className="library-recent" aria-labelledby="library-recent-title">
          <h2 id="library-recent-title">Mới thêm gần đây{kbReady && <span className="library-kb"></span>}</h2>
          {selected.size > 0 && <div className="library-selection"><span>Đã chọn {selected.size} file</span><Button variant="outline" size="sm" disabled={sourcesError !== "" || !visibleFiles.some((file) => selected.has(file.id) && sourceFor(file))} onClick={() => setPendingRemove(visibleFiles.filter((file) => selected.has(file.id) && sourceFor(file)))}>Gỡ khỏi kho kiến thức</Button><Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>Bỏ chọn</Button></div>}
          {!matching.length && <p className="vc-muted">Không tìm thấy file phù hợp.</p>}
        <ul className="library-grid">
          {matching.map((file) => (
            <li className={`library-card${freshIds.has(file.id) ? " is-new" : ""}`} key={file.id} data-testid="library-file">
              <div className="library-card-image">
                {/* Pointer shortcut; the filename button is the keyboard target. */}
                <div className="library-preview-target" onClick={() => setPreviewFile(file)}>
                  <FilePreview file={file} />
                </div>
                <button type="button" className="library-select" aria-label={`Chọn ${file.filename}`} aria-pressed={selected.has(file.id)} onClick={() => setSelected((current) => { const next = new Set(current); if (!next.delete(file.id)) next.add(file.id); return next; })}>{selected.has(file.id) && <Check size={16} />}</button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild><button type="button" className="library-menu-trigger" aria-label={`Tùy chọn ${file.filename}`}><MoreHorizontal size={20} /></button></DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="library-menu">
                    <DropdownMenuItem onSelect={() => setPreviewFile(file)}><Eye /> Xem trước</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => void open(file)}><ExternalLink /> Mở tệp gốc</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => void download(file)}><Download /> Tải xuống</DropdownMenuItem>
                    {!sourcesError && sourcesFor(file).map((entry, _index, all) => (
                      <DropdownMenuItem key={entry.id} asChild>
                        <Link to={sourceLink(entry)}><BookOpen /> {all.length > 1 ? `Mở chi tiết trong kho ${entry.kb}` : "Mở chi tiết tri thức"}</Link>
                      </DropdownMenuItem>
                    ))}
                    <DropdownMenuItem onSelect={() => setStateFile(file)}><Activity /> Trạng thái xử lý</DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem variant="destructive" disabled={!sourceFor(file) || !!sourcesError} onSelect={() => setPendingRemove([file])}><Trash2 /> Gỡ khỏi kho kiến thức</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
              <div className="library-card-content">
                <button
                  type="button"
                  id={`library-open-${file.id}`}
                  className="library-filename"
                  title={file.filename}
                  aria-label={`Xem trước ${file.filename}`}
                  onClick={() => setPreviewFile(file)}
                >
                  {file.filename}
                </button>
                <p className="library-metadata">
                  {TYPE_LABELS[file.content_type] ?? file.content_type} ·{" "}
                  {formatSize(file.size_bytes)} ·{" "}
                  {new Date(file.created_at).toLocaleString("vi-VN")}
                </p>
                {(() => {
                  const entry = sourcesError ? undefined : sourceFor(file);
                  if (!entry) return null;
                  return (
                    <p className="library-status" data-testid="library-file-status">
                      <span>{sourceProcessingLabel(entry)}{entry.review === "pending" && " · Chờ duyệt"}</span>
                      <Link to={sourceLink(entry)} aria-label={`Xem chi tiết ${file.filename}`} title={`Kho ${entry.kb}`}>Xem chi tiết</Link>
                    </p>
                  );
                })()}
                {rowErrors[file.id] && (
                  <p className="vc-inline-error" role="alert">
                    {rowErrors[file.id]}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
        </section>
      )}

      <Dialog open={!!stateFile} onOpenChange={(open) => !open && setStateFile(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>Trạng thái xử lý</DialogTitle><DialogDescription>{stateFile?.filename}</DialogDescription></DialogHeader>
          {stateFile && (() => {
            const source = sourceFor(stateFile);
            if (sourcesError) return <p role="alert">{sourcesError}</p>;
            if (!source) return <><p>File chưa được liên kết với kho kiến thức hoặc trạng thái đang tải.</p>{rowErrors[stateFile.id] && <Button disabled={!kbReady || uploading} onClick={async () => { setUploading(true); try { await queueFile(stateFile, defaultKb); } finally { setUploading(false); } }}>Thử lại xử lý</Button>}</>;
            const processing = source.processing;
            const label = sourceProcessingLabel(source);
            return <><p><strong>{label}</strong>{source.review === "pending" && " · Tri thức đang chờ duyệt"} · Kho {source.kb}</p>{processing.error && <p role="alert">{processing.error}</p>}<Link className="link-button" to={sourceLink(source)}>Xem kết quả và chi tiết</Link>{sourcesFor(stateFile).filter((other) => other.id !== source.id).map((other) => <p key={other.id}><Link className="link-button" to={sourceLink(other)}>Xem nguồn trong kho {other.kb}</Link></p>)}</>;
          })()}
        </DialogContent>
      </Dialog>
      <Dialog open={pendingRemove.length > 0} onOpenChange={(open) => !open && !deleting && setPendingRemove([])}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Gỡ khỏi kho kiến thức?</DialogTitle>
            <DialogDescription>
              Gỡ {pendingRemove.length === 1 ? `“${pendingRemove[0].filename}” khỏi kho ${sourceFor(pendingRemove[0])?.kb ?? ""}` : `${pendingRemove.length} file đã chọn khỏi các kho tương ứng`}. File gốc vẫn được lưu trong tài khoản của bạn nhưng sẽ không còn hiện trong thư viện của kho này; tri thức chỉ còn tìm kiếm được nếu có nguồn khác đang hoạt động. Cùng một file có thể được liên kết với nhiều kho.
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
              onClick={() => void confirmRemove()}
              disabled={deleting}
              data-testid="files-confirm-delete"
            >
              {deleting && <Loader2 className="animate-spin" />}
              Gỡ khỏi kho kiến thức
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <LibraryFileDialog
        file={previewFile}
        onClose={() => setPreviewFile(null)}
        onOpenOriginal={(file) => void open(file)}
        onDownload={(file) => void download(file)}
      />
      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />
    </main>
  );
}

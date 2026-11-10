import { useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowRight,
  Check,
  ChevronRight,
  FileText,
  FileUp,
  LoaderCircle,
  Mic,
  Plus,
  ScanText,
  Search,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import type { Source } from "@/types";
import { useWorkspace } from "@/hooks/workspace";
import {
  ACCEPTED_FILES,
  prepareUpload,
  validateFile,
} from "@/services/capture";
import { sampleDocuments, sampleToSource } from "@/fixtures/seed";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ReviewSource } from "@/components/review-source";
import { OcrView } from "@/components/ocr-view";
import { SampleBadge, SourceIcon } from "@/components/source-preview";

export function SourcesPage() {
  const workspace = useWorkspace();
  const navigate = useNavigate();
  const { sourceId } = useParams();
  const [params] = useSearchParams();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File>();
  const [uploadError, setUploadError] = useState("");
  const [preparing, setPreparing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploadDraft, setUploadDraft] = useState<Source>();
  const [ocrSource, setOcrSource] = useState<Source>();
  const [notice, setNotice] = useState("");
  const [highlight, setHighlight] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const existing =
    sourceId && workspace.sources.find((source) => source.id === sourceId);
  const visible = workspace.sources
    .filter(
      (source) =>
        (filter === "all" || source.kind === filter) &&
        `${source.title} ${source.machines.map((m) => `${m.name} ${m.family}`).join(" ")}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    )
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));

  function openUpload() {
    setUploadOpen(true);
    setUploadError("");
    setSelectedFile(undefined);
  }
  function pick(files: FileList | null) {
    setUploadError("");
    if (!files?.length) return;
    if (files.length !== 1) {
      setUploadError("Mỗi lần chỉ chọn một tài liệu.");
      return;
    }
    const error = validateFile(files[0]);
    if (error) {
      setUploadError(error);
      setSelectedFile(undefined);
      return;
    }
    setSelectedFile(files[0]);
  }
  async function prepare() {
    if (!selectedFile || preparing) return;
    setPreparing(true);
    setUploadError("");
    try {
      const source = await prepareUpload(selectedFile);
      // Text files go through the simulated OCR view; others are reviewed as-is.
      if (source.text) setOcrSource(source);
      else setUploadDraft(source);
      setUploadOpen(false);
      setSelectedFile(undefined);
    } catch (error) {
      setUploadError(
        error instanceof Error ? error.message : "Không đọc được tài liệu này.",
      );
    } finally {
      setPreparing(false);
    }
  }
  async function save(source: Source) {
    await workspace.saveSource(source);
    setUploadDraft(undefined);
    setOcrSource(undefined);
    setHighlight(source.id);
    setNotice(
      existing
        ? "Đã lưu thay đổi trong trình duyệt này."
        : `Đã lưu “${source.title}” vào thư viện.`,
    );
    void navigate("/sources");
  }

  if (ocrSource)
    return (
      <OcrView
        key={ocrSource.id}
        source={ocrSource}
        onSave={save}
        onCancel={() => setOcrSource(undefined)}
      />
    );
  if (uploadDraft || existing) {
    const turn = params.get("turn");
    const turnId = turn
      ? (existing as Source | undefined)?.turns?.find((t) => t.code === turn)
          ?.id
      : undefined;
    return (
      <ReviewSource
        key={uploadDraft?.id ?? (existing && existing.id)}
        source={uploadDraft ?? (existing as Source)}
        editing={!!existing}
        citation={turnId ? { turnId, excerpt: "" } : undefined}
        onSave={save}
        onCancel={() => {
          setUploadDraft(undefined);
          void navigate("/sources");
        }}
      />
    );
  }
  if (sourceId && !workspace.loading)
    return (
      <div className="route-empty">
        <h1 className="page-heading">Không tìm thấy nguồn</h1>
        <p>Nguồn này không còn trong trình duyệt.</p>
        <Button onClick={() => void navigate("/sources")}>Về thư viện</Button>
      </div>
    );

  return (
    <main className="sources-page">
      <header className="sources-header">
        <div>
          <span className="eyebrow">BƯỚC 2 · INPUT TÀI LIỆU</span>
          <h1 className="page-heading">Thư viện tài liệu</h1>
          <p>
            Bản ghi âm, phiếu báo lỗi, biên bản họp, ghi chú chuyên gia và
            manual của hãng cho MR-JE-AX167.
          </p>
        </div>
        <div className="capture-actions">
          <Button
            variant="outline"
            data-testid="upload-open"
            onClick={openUpload}
          >
            <Upload size={16} />
            Tải tài liệu lên
          </Button>
          <Button
            variant="outline"
            onClick={() =>
              void navigate("/record", { state: { autoStart: true } })
            }
          >
            <Mic size={16} />
            Ghi âm
          </Button>
          <Button
            data-testid="sources-analyze-cta"
            onClick={() => void navigate("/analyze")}
          >
            <Sparkles size={16} />
            Phân tích bằng AI
            <ArrowRight size={16} />
          </Button>
        </div>
      </header>
      {notice && (
        <div className="save-notice" role="status" data-testid="save-notice">
          <Check size={16} />
          {notice}
          <button aria-label="Đóng thông báo" onClick={() => setNotice("")}>
            <X size={14} />
          </button>
        </div>
      )}
      <div className="library-toolbar">
        <div className="search-field">
          <Search size={16} />
          <Input
            aria-label="Tìm nguồn"
            placeholder="Tìm theo tên nguồn hoặc máy"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Select value={filter} onValueChange={setFilter}>
          <SelectTrigger aria-label="Lọc loại nguồn">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Tất cả nguồn</SelectItem>
            <SelectItem value="document">Tài liệu</SelectItem>
            <SelectItem value="conversation">Ghi âm</SelectItem>
          </SelectContent>
        </Select>
        <span className="library-count">{visible.length} nguồn</span>
      </div>
      <div className="source-table">
        <div className="source-table-head">
          <span>Nguồn</span>
          <span>Máy</span>
          <span>Trạng thái</span>
          <span>Ngày thêm</span>
          <span />
        </div>
        {visible.map((source) => (
          <button
            key={source.id}
            className={`source-row ${highlight === source.id ? "just-saved" : ""}`}
            data-testid={`source-row-${source.id}`}
            onClick={() => void navigate(`/sources/${source.id}`)}
          >
            <span className="source-name">
              <span className={`source-icon-box ${source.kind}`}>
                <SourceIcon kind={source.kind} />
              </span>
              <span>
                <strong>
                  {source.title}
                  {source.sample && <SampleBadge />}
                </strong>
                <span>
                  {source.kind === "document"
                    ? (source.docType ?? "Tài liệu")
                    : `Ghi âm · ${source.turns?.length ?? 0} lượt nói`}
                  {source.ocr && (
                    <span className="ocr-tag">
                      <ScanText size={12} />
                      AI OCR
                    </span>
                  )}
                  {source.origin === "upload" && " · Lưu trong trình duyệt"}
                  {source.origin === "recording" && " · Vừa ghi âm"}
                </span>
                <span className="source-mobile-machine">
                  {source.machines.map((machine) => machine.name).join(", ") ||
                    "Chưa xác định"}
                </span>
              </span>
            </span>
            <span className="source-machine">
              {source.machines.length
                ? source.machines.map((m) => m.name).join(", ")
                : "Chưa xác định"}
            </span>
            <span>
              <span
                className={`status-badge ${source.status === "draft" ? "draft" : "reference"}`}
              >
                {source.status === "draft" ? "Nháp" : "Tham chiếu"}
              </span>
            </span>
            <span className="source-date">
              {new Intl.DateTimeFormat("vi", {
                day: "numeric",
                month: "numeric",
                year: "numeric",
              }).format(new Date(source.createdAt))}
            </span>
            <ChevronRight size={16} />
          </button>
        ))}
        {!visible.length && (
          <div className="library-empty">
            <Search size={26} />
            <h2>
              {workspace.loading ? "Đang tải nguồn…" : "Không tìm thấy nguồn"}
            </h2>
            <p>
              {search || filter !== "all"
                ? "Thử từ khóa hoặc loại nguồn khác."
                : "Tải tài liệu lên hoặc ghi âm buổi trao đổi đầu tiên."}
            </p>
            {(search || filter !== "all") && (
              <Button
                variant="outline"
                onClick={() => {
                  setSearch("");
                  setFilter("all");
                }}
              >
                Xóa bộ lọc
              </Button>
            )}
          </div>
        )}
      </div>
      <div className="library-footer">
        <span className="small-dot" />
        <span>
          Lưu trong trình duyệt này. Nhãn “Mẫu” đánh dấu tài liệu viết cho demo;
          manual tr. 71 là trang thật của hãng.
        </span>
      </div>

      <Dialog
        open={uploadOpen}
        onOpenChange={(open) => {
          if (!preparing) setUploadOpen(open);
        }}
      >
        <DialogContent className="upload-dialog">
          <DialogHeader>
            <DialogTitle>Tải tài liệu lên</DialogTitle>
            <DialogDescription>
              Ảnh chụp phiếu, biên bản, ghi chú chuyên gia hoặc manual. AI OCR
              sẽ đọc và trích xuất văn bản.
            </DialogDescription>
          </DialogHeader>
          <input
            ref={fileInput}
            className="sr-only"
            type="file"
            accept={ACCEPTED_FILES}
            aria-label="Chọn tệp tài liệu"
            data-testid="upload-file-input"
            onChange={(e) => pick(e.target.files)}
          />
          <div
            className={`upload-dropzone ${dragging ? "dragging" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              pick(e.dataTransfer.files);
            }}
          >
            <span className="upload-symbol">
              <FileUp size={28} strokeWidth={1.5} />
            </span>
            <h3>
              {selectedFile ? selectedFile.name : "Kéo thả tài liệu vào đây"}
            </h3>
            <p>
              {selectedFile
                ? `${(selectedFile.size / 1024).toFixed(1)} KB · Sẵn sàng`
                : "PDF, TXT, MD hoặc DOCX · Tối đa 20 MB"}
            </p>
            <Button
              variant="outline"
              onClick={() => fileInput.current?.click()}
            >
              <Plus size={15} />
              {selectedFile ? "Chọn tệp khác" : "Chọn tệp"}
            </Button>
          </div>
          <div className="sample-picker">
            <span className="sample-picker-label">Dùng tài liệu mẫu</span>
            {sampleDocuments.map((doc) => (
              <button
                key={doc.id}
                type="button"
                className="sample-option"
                data-testid={`sample-doc-${doc.id}`}
                disabled={preparing}
                onClick={() => {
                  setUploadOpen(false);
                  setOcrSource(sampleToSource(doc, new Date().toISOString()));
                }}
              >
                <FileText size={16} />
                <span>
                  <strong>{doc.title}</strong>
                  <span>{doc.docType}</span>
                </span>
                {doc.sample && <SampleBadge />}
                <ArrowRight size={15} />
              </button>
            ))}
          </div>
          {uploadError && (
            <p role="alert" className="inline-error">
              {uploadError}
            </p>
          )}
          <div className="dialog-actions">
            <Button
              variant="ghost"
              disabled={preparing}
              onClick={() => setUploadOpen(false)}
            >
              Hủy
            </Button>
            <Button
              data-testid="upload-start"
              disabled={!selectedFile || preparing}
              onClick={() => void prepare()}
            >
              {preparing ? (
                <LoaderCircle size={15} className="animate-spin" />
              ) : (
                <ScanText size={15} />
              )}
              Đọc tài liệu
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </main>
  );
}

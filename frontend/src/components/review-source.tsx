import { useState } from "react";
import { ArrowLeft, Check, Cpu, FileText, LoaderCircle } from "lucide-react";
import type { Citation, Source } from "@/types";
import { SourcePreview } from "./source-preview";
import { MachinePicker } from "./machine-picker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export function ReviewSource({
  source,
  onSave,
  onCancel,
  editing = false,
  citation,
}: {
  source: Source;
  onSave(source: Source): Promise<void>;
  onCancel(): void;
  editing?: boolean;
  citation?: Pick<Citation, "turnId" | "page" | "excerpt">;
}) {
  const [draft, setDraft] = useState(source);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    if (!draft.title.trim()) {
      setError("Hãy nhập tiêu đề trước khi lưu.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onSave({
        ...draft,
        title: draft.title.trim(),
        summary: draft.summary.trim(),
      });
    } catch {
      setError("Bản nháp chưa được lưu. Hãy thử lại.");
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="review-page">
      <div className="review-heading">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          <ArrowLeft size={16} />
          Quay lại
        </Button>
        <div>
          <h1 className="page-heading">
            {editing ? "Chi tiết nguồn" : "Kiểm tra nguồn trước khi lưu"}
          </h1>
          <p>
            {editing
              ? "Giữ nguồn và ngữ cảnh của nó ở cùng một chỗ."
              : "Kiểm tra nguồn, máy và tóm tắt trước khi lưu."}
          </p>
        </div>
        <Button onClick={() => void save()} disabled={saving}>
          {saving ? (
            <LoaderCircle className="animate-spin" size={16} />
          ) : (
            <Check size={16} />
          )}
          {editing ? "Lưu thay đổi" : "Lưu nháp"}
        </Button>
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <div className="review-grid">
        <section className="review-source">
          <div className="section-label">
            <FileText size={17} />
            <h2>Nguồn</h2>
            <span className="section-number">01</span>
          </div>
          <label htmlFor="source-title" className="field-label">
            Tiêu đề
          </label>
          <Input
            id="source-title"
            value={draft.title}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
          />
          <div className="review-source-body">
            <SourcePreview source={source} citation={citation} />
          </div>
        </section>
        <div className="review-context">
          <section>
            <div className="section-label">
              <Cpu size={17} />
              <h2>Máy</h2>
              <span className="section-number">02</span>
            </div>
            <p className="field-description">
              Nguồn này áp dụng cho máy hoặc dòng máy nào?
            </p>
            <MachinePicker
              value={draft.machines}
              onChange={(value) => setDraft({ ...draft, machines: value })}
            />
          </section>
          <section>
            <div className="section-label">
              <FileText size={17} />
              <h2>Tóm tắt</h2>
              <span className="section-number">03</span>
            </div>
            <label htmlFor="source-summary" className="field-description">
              Quan sát chính, hành động và kết quả.
            </label>
            <Textarea
              id="source-summary"
              rows={9}
              placeholder="Thêm tóm tắt ngắn (không bắt buộc)"
              value={draft.summary}
              onChange={(e) => setDraft({ ...draft, summary: e.target.value })}
            />
            {source.origin === "upload" && (
              <p className="field-note">
                Lưu trong trình duyệt. Chưa nối với backend trích xuất.
              </p>
            )}
            {source.kind === "conversation" && (
              <p className="field-note">
                Đây là bản nháp. Lưu không có nghĩa là đã được chuyên gia duyệt.
              </p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

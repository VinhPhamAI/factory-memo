import { useEffect, useState } from "react";
import {
  ArrowLeft,
  Check,
  CloudUpload,
  LoaderCircle,
  Save,
  ScanText,
  Sparkles,
} from "lucide-react";
import type { Source } from "@/types";
import { detectFields, OCR_TIMING, ocrLines } from "@/services/ocr";
import { sleep } from "@/services/timing";
import { Button } from "@/components/ui/button";
import { SampleBadge } from "./source-preview";
import { cn } from "@/utils/cn";
import { machines } from "@/fixtures/ax167";

type Stage = "upload" | "ocr" | "done";

export function OcrView({
  source,
  onSave,
  onCancel,
}: {
  source: Source;
  onSave(source: Source): Promise<void>;
  onCancel(): void;
}) {
  const lines = ocrLines(source.text ?? "");
  const [stage, setStage] = useState<Stage>("upload");
  const [progress, setProgress] = useState(0);
  const [line, setLine] = useState(-1);
  const [showFields, setShowFields] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const fields = detectFields(source.text ?? "", source.pageStart);

  useEffect(() => {
    const abort = new AbortController();
    const all = ocrLines(source.text ?? "");
    (async () => {
      for (let step = 1; step <= OCR_TIMING.uploadSteps; step++) {
        await sleep(OCR_TIMING.uploadStep, abort.signal);
        setProgress(Math.round((step / OCR_TIMING.uploadSteps) * 100));
      }
      setStage("ocr");
      for (let i = 0; i < all.length; i++) {
        setLine(i);
        if (all[i].trim()) await sleep(OCR_TIMING.line, abort.signal);
      }
      setLine(all.length);
      setStage("done");
      await sleep(OCR_TIMING.fields, abort.signal);
      setShowFields(true);
    })().catch(() => undefined);
    return () => abort.abort();
  }, [source.text]);

  async function save() {
    setSaving(true);
    setError("");
    try {
      const detected = machines.filter((machine) =>
        fields.some((field) => field.value === machine.name),
      );
      await onSave({
        ...source,
        machines: source.machines.length ? source.machines : detected,
        ocr: true,
        createdAt: new Date().toISOString(),
      });
    } catch {
      setError("Chưa lưu được tài liệu. Hãy thử lại.");
      setSaving(false);
    }
  }

  const stepState = (step: Stage) => {
    const order: Stage[] = ["upload", "ocr", "done"];
    const diff = order.indexOf(step) - order.indexOf(stage);
    if (step === "done" && stage === "done") return "done";
    return diff < 0 ? "done" : diff === 0 ? "active" : "pending";
  };
  const extracted = lines.slice(
    0,
    Math.max(0, line + (stage === "done" ? 0 : 1)),
  );
  const beam = lines.length ? Math.min(1, (line + 1) / lines.length) : 0;

  return (
    <main className="ocr-page" data-testid="ocr-view" data-stage={stage}>
      <header className="ocr-header">
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={saving}>
          <ArrowLeft size={16} />
          Thư viện
        </Button>
        <div className="ocr-title">
          <span className="eyebrow">BƯỚC 2 · INPUT TÀI LIỆU</span>
          <h1>
            {source.title}
            {source.sample && <SampleBadge />}
          </h1>
        </div>
        <ol className="ocr-steps" data-testid="ocr-status" data-stage={stage}>
          {(
            [
              ["upload", "Đang tải lên", CloudUpload],
              ["ocr", "AI đang đọc (OCR)", ScanText],
              ["done", "Đã trích xuất", Check],
            ] as const
          ).map(([key, label, Icon]) => (
            <li
              key={key}
              className={`ocr-step ${stepState(key)}`}
              data-testid={`ocr-step-${key}`}
              data-state={stepState(key)}
            >
              <span>
                {stepState(key) === "active" && key !== "done" ? (
                  <LoaderCircle size={15} className="animate-spin" />
                ) : (
                  <Icon size={15} />
                )}
              </span>
              {label}
            </li>
          ))}
        </ol>
      </header>
      <div className="ocr-grid">
        <section className="scan-stage" aria-label="Trang tài liệu được quét">
          <div className="scan-file">
            <ScanText size={15} />
            {source.fileName ?? "tai-lieu"}
            {source.pageStart && <span>· Trang {source.pageStart}</span>}
          </div>
          <div className={cn("paper", stage === "upload" && "uploading")}>
            {lines.map((text, i) => (
              <div
                key={i}
                className={cn(
                  "paper-line",
                  i < line && "scanned",
                  i === line && stage === "ocr" && "reading",
                )}
              >
                {text || " "}
              </div>
            ))}
            {stage === "ocr" && (
              <span className="scan-beam" style={{ top: `${beam * 100}%` }} />
            )}
          </div>
          {stage === "upload" && (
            <div className="upload-progress" data-testid="ocr-upload-progress">
              <span>Đang tải lên… {progress}%</span>
              <span className="progress-track">
                <span style={{ width: `${progress}%` }} />
              </span>
            </div>
          )}
        </section>
        <section className="ocr-output">
          <div className="ocr-output-head">
            <Sparkles size={17} />
            <h2>Văn bản trích xuất (AI OCR)</h2>
            <span className="count-pill" data-testid="ocr-line-count">
              {extracted.filter((item) => item.trim()).length} dòng
            </span>
          </div>
          <pre className="ocr-text" data-testid="ocr-text">
            {extracted.join("\n")}
            {stage === "ocr" && <span className="stream-cursor" />}
          </pre>
          <div className="ocr-fields">
            <span className="ocr-fields-label">Trường nhận diện</span>
            {showFields ? (
              <div className="field-chips">
                {fields.map((field) => (
                  <span
                    key={field.label}
                    className="field-chip"
                    data-testid="ocr-field"
                  >
                    <span>{field.label}</span>
                    {field.value}
                  </span>
                ))}
              </div>
            ) : (
              <span className="muted">Sẽ hiện sau khi đọc xong.</span>
            )}
          </div>
          {error && (
            <p role="alert" className="inline-error">
              {error}
            </p>
          )}
          <div className="ocr-actions">
            {stage === "done" && (
              <span className="ocr-done" data-testid="ocr-done">
                <Check size={16} />
                Đã trích xuất {lines.filter((item) => item.trim()).length} dòng
              </span>
            )}
            <Button
              data-testid="ocr-save"
              disabled={stage !== "done" || !showFields || saving}
              onClick={() => void save()}
            >
              {saving ? (
                <LoaderCircle size={16} className="animate-spin" />
              ) : (
                <Save size={16} />
              )}
              Lưu vào thư viện
            </Button>
          </div>
        </section>
      </div>
    </main>
  );
}

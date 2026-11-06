import { useEffect, useRef, useState } from "react";
import { FileText } from "lucide-react";
import { useAuthClient } from "@/hooks/auth";
import { fetchFilePreview, type LibraryFile } from "@/services/file-api";

/** Load an authenticated preview only when its card approaches the viewport. */
export function FilePreview({ file }: { file: LibraryFile }) {
  const client = useAuthClient();
  const container = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [preview, setPreview] = useState<{ image?: string; text?: string; error?: boolean }>({});

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setVisible(true);
        observer.disconnect();
      }
    }, { rootMargin: "200px" });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
    const abort = new AbortController();
    let objectUrl: string | undefined;
    void fetchFilePreview(client, file.id, abort.signal).then(async (blob) => {
      if (abort.signal.aborted) return;
      if (blob.type.startsWith("image/")) {
        objectUrl = URL.createObjectURL(blob);
        setPreview({ image: objectUrl });
      } else if (blob.type.startsWith("text/plain")) {
        const value = await blob.text();
        if (!abort.signal.aborted) setPreview({ text: value });
      } else setPreview({ error: true });
    }).catch(() => { if (!abort.signal.aborted) setPreview({ error: true }); });
    return () => {
      abort.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, file.id, visible]);

  return (
    <div ref={container} className="library-preview" aria-label={`Xem trước ${file.filename}`}>
      {preview.image ? <img src={preview.image} alt={`Xem trước ${file.filename}`} />
        : preview.text ? <pre>{preview.text}</pre>
          : <span><FileText aria-hidden="true" />{preview.error ? "Không xem trước được" : "Xem trước"}</span>}
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Database, Inbox, Plus, Search } from "lucide-react";
import { useAuth } from "@/hooks/auth";
import { Navigate, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { CreateKnowledgeBaseDialog } from "@/components/knowledge/create-kb-dialog";
import { KnowledgeBaseSettings } from "@/components/knowledge/knowledge-base-settings";
import { KnowledgeBaseSourceList } from "@/components/knowledge/kb-source-list";
import { listConfiguredKnowledgeBases } from "@/services/knowledge-api";
import { setDefaultKnowledgeBase } from "@/services/knowledge-base-admin";
import type { KnowledgeBase } from "@/types";

/** Restrict the administration screen to administrators. */
export function KnowledgeAdminPage() {
  const auth = useAuth();
  if (auth.status !== "signed-in" || auth.user.role !== "admin")
    return <Navigate to="/files" replace />;
  return <KnowledgeAdminWorkspace />;
}

const displayName = (item: KnowledgeBase) => item.title || item.name;

/**
 * Let administrators find a collection, choose where new uploads go, and
 * create or edit collections. IDs and settings stay behind disclosure.
 */
function KnowledgeAdminWorkspace() {
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  // The chosen KB lives in `?kb=` so returning from a source page restores it.
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState(params.get("kb") ?? "");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  const detailPanel = useRef<HTMLElement>(null);

  /** Select a collection; on one-column layouts, bring its details into view. */
  function choose(name: string) {
    setSelected(name);
    setParams({ kb: name }, { replace: true });
    if (window.matchMedia?.("(max-width: 850px)").matches)
      requestAnimationFrame(() =>
        detailPanel.current?.scrollIntoView({ block: "start" }),
      );
  }

  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    listConfiguredKnowledgeBases(abort.signal)
      .then((data) => {
        if (abort.signal.aborted) return;
        setItems(data);
        setSelected((current) =>
          data.some((item) => item.name === current)
            ? current
            : (data.find((item) => item.is_default)?.name ??
              data[0]?.name ??
              ""),
        );
        setError("");
      })
      .catch((reason: unknown) => {
        if (!abort.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "Không tải được danh sách kho kiến thức.",
          );
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [revision]);

  async function makeDefault(name: string) {
    setSaving(true);
    setError("");
    try {
      await setDefaultKnowledgeBase(name);
      reload();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Không đổi được nơi nhận tài liệu mới.",
      );
    } finally {
      setSaving(false);
    }
  }

  const selectedItem = items.find((item) => item.name === selected);
  const needle = query.trim().toLocaleLowerCase();
  const visible = items.filter((item) =>
    `${item.title ?? ""} ${item.name} ${item.description}`
      .toLocaleLowerCase()
      .includes(needle),
  );
  const defaultItem = items.find((item) => item.is_default);
  return (
    <main className="kb-admin-page" data-testid="knowledge-admin">
      <header className="kb-admin-header">
        <div>
          <h1 className="page-heading">Kho kiến thức</h1>
          <p>Chọn nơi lưu tài liệu tải lên và quản lý từng kho.</p>
        </div>
        <Button className="kb-admin-create" onClick={() => setCreating(true)}>
          <Plus /> Tạo kho kiến thức
        </Button>
      </header>
      <div className="kb-admin-destination" data-testid="kb-admin-default">
        <Inbox aria-hidden="true" />
        <div>
          <span>Nơi nhận tài liệu mới</span>
          <strong className="kb-admin-default-name">
            {defaultItem ? displayName(defaultItem) : "Chưa chọn"}
          </strong>
          {!defaultItem && (
            <small>Chọn một kho để nhân viên có thể tải tài liệu lên.</small>
          )}
        </div>
      </div>
      {error && (
        <p className="vc-notice error" role="alert">
          {error}
        </p>
      )}
      <div className="kb-admin-layout">
        <section
          className="kb-admin-list-panel"
          aria-labelledby="kb-admin-list-title"
        >
          <div className="kb-admin-section-heading">
            <h2 id="kb-admin-list-title">Tất cả kho</h2>
            <span>{items.length}</span>
          </div>
          <label className="kb-admin-search">
            <Search size={17} aria-hidden="true" />
            <input
              type="search"
              placeholder="Tìm theo tên"
              aria-label="Tìm kho kiến thức"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          {loading && !items.length ? (
            <p className="kb-admin-empty" role="status">
              Đang tải kho kiến thức…
            </p>
          ) : visible.length === 0 ? (
            <div className="kb-admin-empty">
              <Database aria-hidden="true" />
              <p>
                {items.length
                  ? "Không tìm thấy kho phù hợp."
                  : "Chưa có kho kiến thức nào."}
              </p>
              {!items.length && (
                <Button variant="outline" onClick={() => setCreating(true)}>
                  Tạo kho đầu tiên
                </Button>
              )}
            </div>
          ) : (
            <div className="kb-admin-list">
              {visible.map((item) => (
                <button
                  key={item.name}
                  type="button"
                  className={`kb-admin-item ${item.name === selected ? "selected" : ""}`}
                  aria-pressed={item.name === selected}
                  onClick={() => choose(item.name)}
                >
                  <span className="kb-admin-item-icon" aria-hidden="true">
                    <Database size={18} />
                  </span>
                  <span className="kb-admin-item-text">
                    <strong>{displayName(item)}</strong>
                    {item.description && <small>{item.description}</small>}
                  </span>
                  {item.is_default && (
                    <span className="kb-admin-badge">
                      <CheckCircle2 size={13} aria-hidden="true" /> Mặc định
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </section>
        <section
          ref={detailPanel}
          className="kb-admin-detail-panel"
          aria-label="Kho kiến thức được chọn"
        >
          {selectedItem ? (
            <>
              <div className="kb-admin-detail-top">
                <div>
                  <h2>{displayName(selectedItem)}</h2>
                  <p>{selectedItem.description || "Chưa có mô tả."}</p>
                </div>
                {selectedItem.is_default ? (
                  <span className="kb-admin-badge active">
                    <CheckCircle2 size={14} aria-hidden="true" /> Mặc định
                  </span>
                ) : (
                  <Button
                    variant="outline"
                    disabled={saving}
                    onClick={() => void makeDefault(selectedItem.name)}
                  >
                    Đặt làm mặc định
                  </Button>
                )}
              </div>
              <p className="kb-admin-explainer">
                {selectedItem.is_default
                  ? "Tài liệu tải lên từ Thư viện tài liệu sẽ vào kho này."
                  : "Khi đặt làm mặc định, chỉ tài liệu tải lên sau đó vào kho này. Tài liệu cũ vẫn ở kho hiện tại."}
              </p>
              <KnowledgeBaseSettings
                key={selectedItem.name}
                name={selectedItem.name}
                role="admin"
                revision={revision}
                isDefault={selectedItem.is_default}
                compact
                onChanged={reload}
              />
              <KnowledgeBaseSourceList
                key={selectedItem.name}
                kb={selectedItem.name}
                revision={revision}
                returnTo={`/admin/knowledge-bases?kb=${encodeURIComponent(selectedItem.name)}`}
              />
            </>
          ) : (
            <div className="kb-admin-detail-empty">
              <Database size={32} aria-hidden="true" />
              <h2>Chọn một kho</h2>
              <p>Chọn kho trong danh sách để xem và quản lý.</p>
            </div>
          )}
        </section>
      </div>
      <CreateKnowledgeBaseDialog
        open={creating}
        taken={items.map((item) => item.name)}
        onClose={() => setCreating(false)}
        onCreated={(created) => {
          setCreating(false);
          setSelected(created.name);
          setParams({ kb: created.name }, { replace: true });
          reload();
        }}
      />
    </main>
  );
}

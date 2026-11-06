import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Database, Layers3, Plus, Search, ShieldCheck, Sparkles } from "lucide-react";
import { useAuth } from "@/hooks/auth";
import { Navigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { CreateKnowledgeBaseDialog } from "@/components/knowledge/create-kb-dialog";
import { KnowledgeBaseSettings } from "@/components/knowledge/knowledge-base-settings";
import { listConfiguredKnowledgeBases } from "@/services/knowledge-api";
import { setDefaultKnowledgeBase } from "@/services/knowledge-base-admin";
import type { KnowledgeBase } from "@/types";

/** Restrict the administration screen to administrators. */
export function KnowledgeAdminPage() {
  const auth = useAuth();
  if (auth.status !== "signed-in" || auth.user.role !== "admin") return <Navigate to="/files" replace />;
  return <KnowledgeAdminWorkspace />;
}

function KnowledgeAdminWorkspace() {
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [selected, setSelected] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const reload = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    listConfiguredKnowledgeBases(abort.signal).then((data) => {
      if (abort.signal.aborted) return;
      setItems(data);
      setSelected((current) => data.some((item) => item.name === current) ? current : (data.find((item) => item.is_default)?.name ?? data[0]?.name ?? ""));
      setError("");
    }).catch((reason: unknown) => {
      if (!abort.signal.aborted) setError(reason instanceof Error ? reason.message : "Không tải được knowledge base.");
    }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [revision]);

  async function makeDefault(name: string) {
    setSaving(true);
    setError("");
    try {
      await setDefaultKnowledgeBase(name);
      reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể cập nhật mặc định.");
    } finally {
      setSaving(false);
    }
  }

  const selectedItem = items.find((item) => item.name === selected);
  const visible = items.filter((item) => `${item.title ?? ""} ${item.name} ${item.description}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const defaultItem = items.find((item) => item.is_default);
  return (
    <main className="kb-admin-page" data-testid="knowledge-admin">
      <header className="kb-admin-header">
        <div><p className="kb-admin-eyebrow"><ShieldCheck size={15} /> ADMINISTRATION</p><h1>Knowledge bases</h1><p>Quản lý không gian tri thức và lựa chọn nơi tiếp nhận tài liệu.</p></div>
        <Button className="kb-admin-create" onClick={() => setCreating(true)}><Plus /> Tạo knowledge base</Button>
      </header>
      <div className="kb-admin-overview">
        <div className="kb-admin-overview-icon"><Layers3 /></div>
        <div><span>Không gian tri thức</span><strong>{items.length}</strong><small>Knowledge base đang hoạt động</small></div>
        <div className="kb-admin-overview-divider" />
        <div className="kb-admin-default-icon"><Sparkles /></div>
        <div><span>Đang dùng cho Library</span><strong className="kb-admin-default-name">{defaultItem?.title || defaultItem?.name || "Chưa chọn"}</strong><small>{defaultItem ? defaultItem.name : "Chọn một KB mặc định để tải tài liệu"}</small></div>
      </div>
      {error && <p className="vc-notice error" role="alert">{error}</p>}
      <div className="kb-admin-layout">
        <section className="kb-admin-list-panel" aria-labelledby="kb-admin-list-title">
          <div className="kb-admin-section-heading"><div><h2 id="kb-admin-list-title">Tất cả knowledge base</h2><p>Chọn một KB để xem và quản lý.</p></div><span>{items.length}</span></div>
          <label className="kb-admin-search"><Search size={17} /><input type="search" placeholder="Tìm theo tên hoặc mã" aria-label="Tìm knowledge base" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
          {loading && !items.length ? <p className="kb-admin-empty">Đang tải knowledge base…</p> : visible.length === 0 ? <div className="kb-admin-empty"><Database /><p>{items.length ? "Không tìm thấy kết quả phù hợp." : "Chưa có knowledge base nào."}</p>{!items.length && <Button variant="outline" onClick={() => setCreating(true)}>Tạo knowledge base đầu tiên</Button>}</div> : (
            <div className="kb-admin-list">{visible.map((item) => <button key={item.name} type="button" className={`kb-admin-item ${item.name === selected ? "selected" : ""}`} aria-pressed={item.name === selected} onClick={() => setSelected(item.name)}><span className="kb-admin-item-icon"><Database size={19} /></span><span className="kb-admin-item-text"><strong>{item.title || item.name}</strong><small>{item.name}</small></span>{item.is_default && <span className="kb-admin-badge"><CheckCircle2 size={13} /> Mặc định</span>}</button>)}</div>
          )}
        </section>
        <section className="kb-admin-detail-panel" aria-label="Knowledge base được chọn">
          {selectedItem ? <><div className="kb-admin-detail-top"><div><span className="kb-admin-detail-kicker">CHI TIẾT KNOWLEDGE BASE</span><h2>{selectedItem.title || selectedItem.name}</h2><p>{selectedItem.description || "Chưa có mô tả cho knowledge base này."}</p></div>{selectedItem.is_default ? <span className="kb-admin-badge active"><CheckCircle2 size={14} /> Mặc định</span> : <Button variant="outline" disabled={saving} onClick={() => void makeDefault(selectedItem.name)}><Sparkles /> Đặt làm mặc định</Button>}</div><div className="kb-admin-explainer">Tài liệu tải lên từ Library được đưa vào knowledge base mặc định. Thay đổi này không di chuyển tài liệu đã tải trước đó.</div><KnowledgeBaseSettings key={selectedItem.name} name={selectedItem.name} role="admin" revision={revision} isDefault={selectedItem.is_default} compact onChanged={reload} /></> : <div className="kb-admin-detail-empty"><Database size={32} /><h2>Chọn knowledge base</h2><p>Chọn một mục bên trái để xem chi tiết.</p></div>}
        </section>
      </div>
      <CreateKnowledgeBaseDialog open={creating} taken={items.map((item) => item.name)} onClose={() => setCreating(false)} onCreated={(created) => { setCreating(false); setSelected(created.name); reload(); }} />
    </main>
  );
}

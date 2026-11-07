import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, FileText, Loader2, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  listManagedSources,
  sourceLink,
  sourceProcessingLabel,
  type ManagedSource,
} from "@/services/knowledge-management";
import { dateTime } from "@/services/voice-display";

/**
 * List the sources stored in one knowledge base, each linking to its detail
 * page, so administrators can reach any source without its library file.
 *
 * Args:
 *   kb: Knowledge base ID. Changing it reloads the list from the first page.
 *   revision: Changes when the caller wants the list reloaded.
 *   returnTo: Route the source page's back link should return to.
 */
export function KnowledgeBaseSourceList({
  kb,
  revision = 0,
  returnTo,
}: {
  kb: string;
  revision?: number;
  returnTo: string;
}) {
  const [sources, setSources] = useState<ManagedSource[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);

  // Restart from the first page when the knowledge base or caller revision changes.
  const [key, setKey] = useState({ kb, revision });
  if (key.kb !== kb || key.revision !== revision) {
    setKey({ kb, revision });
    setSources([]);
    setNext(null);
    setPage(0);
    setQuery("");
  }

  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    setError("");
    const offset = page === 0 ? 0 : (next ?? 0);
    listManagedSources(kb, abort.signal, offset)
      .then((data) => {
        if (abort.signal.aborted) return;
        setSources((current) =>
          page === 0 ? data.sources : [...current, ...data.sources],
        );
        setNext(data.next_offset);
      })
      .catch((reason: unknown) => {
        if (!abort.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "Không tải được danh sách tài liệu.",
          );
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
    // `next` is the cursor for the page being requested, not a trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kb, revision, page]);

  const needle = query.trim().toLocaleLowerCase();
  const visible = sources.filter((source) =>
    source.name.toLocaleLowerCase().includes(needle),
  );

  return (
    <section className="kb-source-list" aria-label="Tài liệu trong kho" data-testid="kb-source-list">
      <div className="kb-admin-section-heading">
        <h3>Tài liệu trong kho</h3>
        <span>{sources.length}{next !== null && "+"}</span>
      </div>
      <label className="kb-admin-search">
        <Search size={17} aria-hidden="true" />
        <input
          type="search"
          placeholder="Tìm theo tên tài liệu"
          aria-label="Tìm tài liệu trong kho"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      {error && (
        <p className="vc-notice error" role="alert">
          {error}
        </p>
      )}
      {loading && !sources.length ? (
        <p className="vc-muted" role="status">
          <Loader2 className="animate-spin" size={16} /> Đang tải tài liệu…
        </p>
      ) : !visible.length && !error ? (
        <p className="kb-admin-empty">
          {sources.length
            ? "Không tìm thấy tài liệu phù hợp."
            : "Kho này chưa có tài liệu."}
        </p>
      ) : (
        <ul className="kb-source-items">
          {visible.map((source) => (
            <li key={`${source.kind}:${source.id}`} data-testid="kb-source-item">
              <FileText size={18} aria-hidden="true" />
              <div className="kb-source-text">
                <strong>{source.name}</strong>
                <small>
                  {sourceProcessingLabel(source)}
                  {source.review === "pending" && " · Chờ duyệt"}
                  {source.created_at && ` · ${dateTime(source.created_at)}`}
                </small>
              </div>
              <Button variant="outline" size="sm" asChild>
                <Link
                  to={sourceLink(source)}
                  state={{ from: returnTo }}
                  aria-label={`Xem chi tiết ${source.name}`}
                >
                  Xem chi tiết <ArrowRight />
                </Link>
              </Button>
            </li>
          ))}
        </ul>
      )}
      {next !== null && (
        <Button
          variant="outline"
          disabled={loading}
          onClick={() => setPage((value) => value + 1)}
        >
          {loading && <Loader2 className="animate-spin" />} Tải thêm
        </Button>
      )}
    </section>
  );
}

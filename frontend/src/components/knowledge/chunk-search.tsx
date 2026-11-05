import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Loader2, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { sourceLink } from "@/services/knowledge-management";
import {
  evidenceKind,
  searchChunks,
  type ChunkKind,
  type ChunkStatus,
  type SearchEvidence,
  type SearchMode,
  type SearchResponse,
  type SearchResult,
} from "@/services/knowledge-search";
import { STATUS_LABELS } from "@/services/voice-display";
import type { LocatedReference } from "@/types";
import { cn } from "@/utils/cn";

const MODES: Record<SearchMode, string> = {
  hybrid: "Kết hợp (hybrid)",
  dense: "Ngữ nghĩa (dense)",
  keyword: "Từ khóa (keyword)",
};
const KINDS: Record<ChunkKind, string> = {
  knowledge: "Knowledge item",
  document_passage: "Đoạn tài liệu",
};
const STATUSES: ChunkStatus[] = ["draft", "approved"];
const CONTENT_EXCERPT = 400;

function locationLabel(reference: LocatedReference): string {
  switch (reference.type) {
    case "page":
    case "image":
      return `trang ${reference.page}`;
    case "paragraph":
      return reference.table && reference.cell
        ? `bảng ${reference.table}, ô ${reference.cell}`
        : `đoạn ${reference.index}`;
    case "cells":
      return `${reference.sheet}!${reference.range}`;
    case "passage":
      return `đoạn ${reference.chunk_index}`;
    default:
      return "";
  }
}

function excerpt(content: string) {
  return content.length > CONTENT_EXCERPT
    ? `${content.slice(0, CONTENT_EXCERPT).trimEnd()}…`
    : content;
}

function EvidenceChip({ kb, evidence }: { kb: string; evidence: SearchEvidence }) {
  const located = evidence.references.find((item) => !(item.type === "source"));
  const where = located ? locationLabel(located) : "";
  return (
    <Link
      className="vc-chip km-search-evidence"
      data-testid="search-evidence"
      to={sourceLink({
        kb,
        kind: evidenceKind(evidence),
        id: evidence.source_id,
      })}
      state={located ? { located } : undefined}
    >
      {evidence.source_title}
      {where && ` · ${where}`}
    </Link>
  );
}

function ResultCard({ kb, result }: { kb: string; result: SearchResult }) {
  return (
    <li
      className={cn("km-search-result", result.status)}
      data-testid="search-result"
      data-status={result.status}
    >
      <header>
        <span className="km-search-rank">#{result.rank}</span>
        <span className="vc-chip">{KINDS[result.kind]}</span>
        <span
          className={cn("vc-chip", result.status)}
          data-testid="search-status"
        >
          {result.status === "draft"
            ? `Bản nháp · ${STATUS_LABELS.draft}`
            : STATUS_LABELS[result.status]}
        </span>
        <code className="vc-muted">{result.citation}</code>
      </header>
      {result.title && <h3>{result.title}</h3>}
      <p>{excerpt(result.content)}</p>
      {result.evidence.length > 0 && (
        <div className="km-search-evidence-list" aria-label="Bằng chứng">
          {result.evidence.map((item) => (
            <EvidenceChip key={item.source_id} kb={kb} evidence={item} />
          ))}
        </div>
      )}
    </li>
  );
}

function toggle<T>(items: T[], item: T): T[] {
  return items.includes(item)
    ? items.filter((value) => value !== item)
    : [...items, item];
}

export function ChunkSearchPanel({ kb }: { kb: string }) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [kinds, setKinds] = useState<ChunkKind[]>([
    "knowledge",
    "document_passage",
  ]);
  const [statuses, setStatuses] = useState<ChunkStatus[]>([...STATUSES]);
  const [response, setResponse] = useState<SearchResponse>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const running = useRef<AbortController | undefined>(undefined);

  // A new knowledge base starts with a clean page; a late answer is dropped.
  useEffect(() => {
    setResponse(undefined);
    setError("");
    return () => running.current?.abort();
  }, [kb]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = query.trim();
    if (!text || !kinds.length || !statuses.length) return;
    running.current?.abort();
    const abort = new AbortController();
    running.current = abort;
    setLoading(true);
    setError("");
    try {
      const found = await searchChunks(
        kb,
        { query: text, mode, kinds, statuses },
        abort.signal,
      );
      if (!abort.signal.aborted) setResponse(found);
    } catch (failure) {
      if (abort.signal.aborted) return;
      setResponse(undefined);
      setError(
        failure instanceof Error ? failure.message : "Không tìm kiếm được.",
      );
    } finally {
      if (!abort.signal.aborted) setLoading(false);
    }
  }

  return (
    <section className="km-search" aria-label="Tìm kiếm tri thức">
      <form className="km-search-form" onSubmit={submit}>
        <label className="vc-field km-source-search">
          <span>Câu hỏi hoặc từ khóa</span>
          <input
            value={query}
            maxLength={2000}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Ví dụ: E-105 trục Z"
          />
        </label>
        <label className="vc-field">
          <span>Chế độ</span>
          <select
            value={mode}
            onChange={(event) => setMode(event.target.value as SearchMode)}
          >
            {Object.entries(MODES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="km-search-filter">
          <legend>Loại</legend>
          {(Object.keys(KINDS) as ChunkKind[]).map((kind) => (
            <label key={kind}>
              <input
                type="checkbox"
                checked={kinds.includes(kind)}
                onChange={() => setKinds((items) => toggle(items, kind))}
              />
              {KINDS[kind]}
            </label>
          ))}
        </fieldset>
        <fieldset className="km-search-filter">
          <legend>Trạng thái</legend>
          {STATUSES.map((status) => (
            <label key={status}>
              <input
                type="checkbox"
                checked={statuses.includes(status)}
                onChange={() => setStatuses((items) => toggle(items, status))}
              />
              {STATUS_LABELS[status]}
            </label>
          ))}
        </fieldset>
        <Button
          type="submit"
          disabled={loading || !query.trim() || !kinds.length || !statuses.length}
        >
          {loading ? <Loader2 className="animate-spin" /> : <Search />} Tìm kiếm
        </Button>
      </form>
      {error && (
        <p className="vc-notice error" role="alert">
          {error}
        </p>
      )}
      {response &&
        (response.results.length ? (
          <ol className="km-search-results" aria-label="Kết quả tìm kiếm">
            {response.results.map((result) => (
              <ResultCard key={result.chunk_id} kb={kb} result={result} />
            ))}
          </ol>
        ) : (
          <p className="vc-muted" role="status">
            Không tìm thấy
          </p>
        ))}
    </section>
  );
}

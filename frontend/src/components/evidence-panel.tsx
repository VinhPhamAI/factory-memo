import { Link } from "react-router-dom";
import {
  ArrowLeft,
  ArrowUpRight,
  BadgeCheck,
  BookOpen,
  Cpu,
  ExternalLink,
} from "lucide-react";
import type { Citation, KnowledgeCard, Source } from "@/types";
import { Button } from "@/components/ui/button";
import { SampleBadge, SourceIcon, SourcePreview } from "./source-preview";
import { CardRows, StatusBadge } from "@/pages/card";
import { citationTitle } from "@/services/citation-display";
import { caseCardFor } from "@/services/case-card";
import { CaseCitationCard } from "./case-citation-card";

function kindLabel(citation: Citation, source?: Source) {
  if (citation.kind === "card") return "Knowledge card";
  if (source?.kind === "conversation") return "Ghi âm";
  return source?.sample ? (source.docType ?? "Tài liệu") : "Manual hãng";
}

export function EvidencePanel({
  citations,
  sources,
  card,
  active,
  onSelect,
  onBack,
}: {
  citations: Citation[];
  sources: Source[];
  card?: KnowledgeCard;
  active?: Citation;
  onSelect(citation: Citation): void;
  onBack(): void;
}) {
  const selected =
    active && sources.find((source) => source.id === active.sourceId);
  const caseCard = active && caseCardFor(active);
  return (
    <div className="evidence-panel">
      <div className="evidence-heading">
        <BookOpen size={18} />
        <h2>Nguồn</h2>
        <span className="count-pill">{citations.length}</span>
      </div>
      {!citations.length ? (
        <div className="evidence-empty">
          <span className="empty-icon">
            <BookOpen size={23} strokeWidth={1.5} />
          </span>
          <h3>Nguồn của câu trả lời</h3>
          <p>
            Các nguồn được agent trích dẫn sẽ hiện ở đây khi có câu trả lời.
          </p>
        </div>
      ) : active?.marker ? (
        <div className="evidence-detail" data-testid="evidence-api-detail">
          <Button variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft size={14} />
            Tất cả nguồn
          </Button>
          <div className="source-detail-heading">
            <div className="source-kind">
              <span className="citation-index">{active.number}</span>
              {caseCard
                ? "Case card"
                : active.kind === "knowledge"
                  ? "Tri thức"
                  : "Tài liệu"}
            </div>
            {!caseCard && <h3>{citationTitle(active)}</h3>}
          </div>
          {caseCard ? (
            <CaseCitationCard
              key={active.id}
              citation={active}
              card={caseCard}
            />
          ) : active.excerpt ? (
            <p className="api-evidence-text">{active.excerpt}</p>
          ) : (
            <p className="field-note">
              Chưa có nội dung xem trước cho tài liệu này.
            </p>
          )}
          {active.statedBy && (
            <p className="field-note">Người cung cấp: {active.statedBy}</p>
          )}
          {active.recordType && !caseCard && (
            <p className="field-note">Loại: {active.recordType}</p>
          )}
          {active.kind === "knowledge" && (
            <p className="field-note">
              Được truy xuất không đồng nghĩa với đã được chuyên gia duyệt.
            </p>
          )}
        </div>
      ) : active?.kind === "card" && card ? (
        <div className="evidence-detail" data-testid="evidence-card-detail">
          <Button variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft size={14} />
            Tất cả nguồn
          </Button>
          <div className="source-detail-heading">
            <div className="source-kind">
              <BadgeCheck size={14} />
              Knowledge card
              <StatusBadge card={card} />
            </div>
            <h3>{card.subject} · Case</h3>
            <Link className="link-button" to={`/card?row=${active.cardRow}`}>
              Mở knowledge card
              <ExternalLink size={13} />
            </Link>
          </div>
          <CardRows
            card={card}
            compact
            highlight={active.cardRow}
            onOpenRef={(ref) =>
              onSelect({
                id: `${ref.sourceId}:${ref.label}`,
                number: active.number,
                sourceId: ref.sourceId,
                turnId: ref.turnId,
                turnCode: ref.turnId ? ref.label : undefined,
                page: ref.page,
                excerpt: ref.quote,
              })
            }
          />
        </div>
      ) : selected && active ? (
        <div className="evidence-detail" data-testid="evidence-source-detail">
          <Button variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft size={14} />
            Tất cả nguồn
          </Button>
          <div className="source-detail-heading">
            <div className="source-kind">
              <SourceIcon kind={selected.kind} size={14} />
              {kindLabel(active, selected)}
              {selected.sample && <SampleBadge />}
            </div>
            <h3>{selected.title}</h3>
            <div className="source-scope">
              <Cpu size={13} />
              {selected.machines.map((m) => m.name).join(", ") ||
                "Chưa xác định máy"}
            </div>
          </div>
          <SourcePreview source={selected} citation={active} compact />
        </div>
      ) : (
        <div className="evidence-list">
          <p className="evidence-caption">Được trích dẫn trong câu trả lời</p>
          {citations.map((citation) => {
            if (citation.marker) {
              const casePreview = caseCardFor(citation);
              return (
                <button
                  key={citation.id}
                  className={`evidence-card ${casePreview ? "is-card" : ""}`}
                  data-testid={`source-card-${citation.number}`}
                  onClick={() => onSelect(citation)}
                  aria-label={`Mở nguồn ${citation.number}: ${citationTitle(citation)}`}
                >
                  <div className="evidence-card-top">
                    <span className="citation-index">{citation.number}</span>
                    <BookOpen size={13} />
                    <span className="source-kind">
                      {casePreview
                        ? "Case card"
                        : citation.kind === "knowledge"
                          ? "Tri thức"
                          : "Tài liệu"}
                    </span>
                    <ArrowUpRight size={15} />
                  </div>
                  <h3>{citationTitle(citation)}</h3>
                  {(casePreview?.observation || citation.excerpt) && (
                    <p className="evidence-quote">
                      {casePreview?.observation || citation.excerpt}
                    </p>
                  )}
                </button>
              );
            }
            const isCard = citation.kind === "card";
            const source = sources.find(
              (item) => item.id === citation.sourceId,
            );
            if ((isCard && !card) || (!isCard && !source))
              return (
                <p key={citation.id} className="inline-error">
                  Nguồn được trích dẫn không còn trong trình duyệt.
                </p>
              );
            const title = isCard ? `${card!.subject} · Case` : source!.title;
            return (
              <button
                key={citation.id}
                className={`evidence-card ${isCard ? "is-card" : ""}`}
                data-testid={`source-card-${citation.number}`}
                onClick={() => onSelect(citation)}
                aria-label={`Mở nguồn ${citation.number}: ${title}`}
              >
                <div className="evidence-card-top">
                  <span className="citation-index">{citation.number}</span>
                  <span className="source-kind">
                    {isCard ? (
                      <BadgeCheck size={13} />
                    ) : (
                      <SourceIcon kind={source!.kind} size={13} />
                    )}
                    {kindLabel(citation, source)}
                  </span>
                  {isCard ? (
                    <StatusBadge card={card!} />
                  ) : (
                    source!.sample && <SampleBadge />
                  )}
                  <ArrowUpRight size={15} />
                </div>
                <h3>{title}</h3>
                {source?.section && (
                  <div className="source-scope">{source.section}</div>
                )}
                <p className="evidence-quote">“{citation.excerpt}”</p>
                <div className="evidence-card-bottom">
                  <span>
                    {citation.page
                      ? `Trang ${citation.page}`
                      : `Lượt ${citation.turnCode}`}
                  </span>
                  <span>
                    {isCard
                      ? "Dòng: Cách kiểm tra"
                      : source!.machines.map((m) => m.name).join(", ")}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

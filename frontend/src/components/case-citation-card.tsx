import { useState } from "react";
import type { CaseCardData, Citation, EvidenceRef } from "@/types";
import { QuoteBox, RefChip } from "./ref-chip";
import { citationTitle } from "@/services/citation-display";

/** Read-only record view using the demo's card layout, never its fixture content. */
export function CaseCitationCard({
  citation,
  card,
}: {
  citation: Citation;
  card: CaseCardData;
}) {
  const [open, setOpen] = useState<number>();
  const rows = [
    { key: "observation", label: "Hiện tượng", text: card.observation },
    { key: "context", label: "Bối cảnh", text: card.context },
    { key: "reasoning", label: "Nhận định & lập luận", text: card.reasoning },
    // The stored schema combines diagnostic checks and corrective actions.
    { key: "action", label: "Kiểm tra & xử lý", text: card.action },
    {
      key: "outcome",
      label: "Kết quả",
      text: card.outcome || "Chưa có kết quả được ghi nhận.",
    },
    ...(citation.scope?.condition
      ? [
          {
            key: "condition",
            label: "Điều kiện áp dụng",
            text: citation.scope.condition,
          },
        ]
      : []),
  ];
  const scope = [
    "machine_id",
    "machine_model_specific",
    "machine_model",
    "line_id",
    "station",
  ]
    .map((key) => citation.scope?.[key])
    .filter((value): value is string => !!value);
  const refs: EvidenceRef[] = card.evidence.map((item) => ({
    label: item.turn_id,
    quote: item.quote,
    sourceId: citation.evidenceId ?? citation.sourceId,
    turnId: Number(item.turn_id.slice(1)),
  }));
  const active = open === undefined ? undefined : refs[open];
  const status = citation.reviewStatus;
  return (
    <article
      className="knowledge-card case-citation-card"
      data-testid="case-citation-card"
    >
      <header className="knowledge-card-head">
        <div>
          <span className="eyebrow">CASE CARD</span>
          <h3>{citationTitle(citation)}</h3>
          {!!scope.length && (
            <div className="scope-tags">
              {[...new Set(scope)].map((value) => (
                <span key={value} className="scope-tag">
                  {value}
                </span>
              ))}
            </div>
          )}
          <span
            className={`status-badge ${status === "approved" ? "approved" : "draft"}`}
            data-testid="case-review-status"
          >
            {status === "approved"
              ? "Đã duyệt"
              : status === "draft"
                ? "Nháp — chờ duyệt"
                : status === "deprecated"
                  ? "Ngừng sử dụng"
                  : "Chưa có trạng thái duyệt"}
          </span>
        </div>
      </header>
      <div className="card-rows compact">
        {rows.map((row, index) => (
          <div
            className="card-row"
            key={row.key}
            data-testid={`case-row-${row.key}`}
          >
            <div className="card-row-label">
              <span>{index + 1}</span>
              {row.label}
            </div>
            <div className="card-row-body">
              <p>{row.text || "Chưa ghi nhận."}</p>
            </div>
          </div>
        ))}
      </div>
      {!!refs.length && (
        <section
          className="case-citation-evidence"
          aria-label="Trích dẫn nguồn của case"
        >
          <h4>Trích dẫn nguồn</h4>
          <div className="ref-chips">
            {refs.map((ref, index) => (
              <RefChip
                key={`${ref.label}:${index}`}
                evidence={ref}
                active={open === index}
                testId={`case-ref-${index + 1}`}
                onClick={() =>
                  setOpen((current) => (current === index ? undefined : index))
                }
              />
            ))}
          </div>
          {active && <QuoteBox evidence={active} />}
        </section>
      )}
    </article>
  );
}

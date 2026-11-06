import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useInquiriesApi } from "@/hooks/inquiries";
import { Badge } from "@/components/ui/badge";
import {
  InquiryError,
  STATUS_LABELS,
  type InquirySummary,
} from "@/services/inquiries-api";

type Tab = "draft" | "sent" | "assigned";

const TABS: { id: Tab; label: string; empty: string }[] = [
  { id: "draft", label: "Bản nháp", empty: "Chưa có bản nháp nào." },
  { id: "sent", label: "Đã gửi", empty: "Bạn chưa gửi yêu cầu nào." },
  {
    id: "assigned",
    label: "Được giao",
    empty: "Chưa có yêu cầu nào được giao.",
  },
];

/** Keep the rows of a tab: drafts and submitted requests share the sent box. */
export function rowsFor(tab: Tab, rows: InquirySummary[]): InquirySummary[] {
  if (tab === "draft") return rows.filter((row) => row.status === "draft");
  if (tab === "sent") return rows.filter((row) => row.status !== "draft");
  return rows;
}

/** Inbox of the user's drafts, sent requests and requests assigned to them. */
export function InquiriesPage() {
  const api = useInquiriesApi();
  const [tab, setTab] = useState<Tab>("draft");
  const [rows, setRows] = useState<InquirySummary[] | null>(null);
  const [error, setError] = useState("");
  const box = tab === "assigned" ? "assigned" : "sent";

  useEffect(() => {
    let current = true;
    api
      .list(box)
      .then((items) => {
        if (!current) return;
        setRows(items);
        setError("");
      })
      .catch((cause: unknown) => {
        if (!current) return;
        setRows([]);
        setError(
          cause instanceof InquiryError
            ? cause.message
            : "Không tải được danh sách yêu cầu.",
        );
      });
    return () => {
      current = false;
    };
  }, [api, box]);

  const spec = TABS.find((item) => item.id === tab)!;
  const shown = rows ? rowsFor(tab, rows) : [];
  return (
    <main className="inquiry-page" data-testid="inquiries-page">
      <header className="inquiry-page-heading">
        <h1>Yêu cầu chuyên gia</h1>
        <p>Yêu cầu bạn soạn từ trò chuyện và yêu cầu được giao cho bạn.</p>
      </header>
      <div role="tablist" aria-label="Hộp thư" className="inquiry-tabs">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            data-testid={`tab-${item.id}`}
            className="inquiry-tab"
            onClick={() => {
              if ((item.id === "assigned") !== (tab === "assigned"))
                setRows(null);
              setTab(item.id);
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
      {rows === null ? (
        <p className="field-note" aria-busy="true">
          Đang tải…
        </p>
      ) : !shown.length ? (
        !error && (
          <p className="field-note" data-testid="inquiries-empty">
            {spec.empty}
          </p>
        )
      ) : (
        <ul className="inquiry-list">
          {shown.map((row) => (
            <li key={row.id} data-testid="inquiry-row" className="inquiry-row">
              {/* The link covers the row; its name stays the title alone. */}
              <Link to={`/inquiries/${row.id}`} className="inquiry-row-link">
                <strong>{row.title || "(Chưa có tiêu đề)"}</strong>
              </Link>
              <Badge
                variant="secondary"
                className="inquiry-status"
                data-status={row.status}
              >
                {STATUS_LABELS[row.status] ?? row.status}
              </Badge>
              <span className="field-note inquiry-row-meta">
                {tab === "assigned"
                  ? `Từ ${row.requester_name} · `
                  : row.assignee_name
                    ? `Gửi ${row.assignee_name} · `
                    : ""}
                {new Date(row.updated_at).toLocaleString("vi-VN")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}

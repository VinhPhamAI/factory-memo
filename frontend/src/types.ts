export interface MachineScope {
  id: string;
  name: string;
  kind: "asset" | "family";
  family: string;
  location?: string;
}

export interface TranscriptTurn {
  id: number;
  /** Turn label shown to people and used in citations, e.g. "T01" or "F05". */
  code: string;
  speakerId: string;
  speaker: string;
  role: string;
  text: string;
  seconds: number;
}

export interface Source {
  id: string;
  title: string;
  kind: "document" | "conversation";
  status: "reference" | "draft";
  origin: "demo" | "upload" | "recording";
  createdAt: string;
  machines: MachineScope[];
  summary: string;
  /** Simulated sample content written for the demo ("Mẫu" badge). */
  sample?: boolean;
  /** Short document type label, e.g. "Phiếu báo lỗi". */
  docType?: string;
  /** True once the document text came out of the simulated OCR step. */
  ocr?: boolean;
  text?: string;
  turns?: TranscriptTurn[];
  pageStart?: number;
  pageEnd?: number;
  section?: string;
  file?: Blob;
  fileName?: string;
  fileType?: string;
  fileSize?: number;
  originalId?: string;
}

/** One quote that supports a knowledge-card row. */
export interface EvidenceRef {
  /** Chip label: a turn code ("T04") or a short document label. */
  label: string;
  sourceId: string;
  quote: string;
  turnId?: number;
  page?: number;
}

export type CardRowKey =
  | "symptom"
  | "context"
  | "cause"
  | "check"
  | "countermeasure"
  | "result"
  | "applies";

export interface CardRow {
  key: CardRowKey;
  label: string;
  question: string;
  text: string;
  refs: EvidenceRef[];
}

export interface RuledOut {
  text: string;
  note: string;
  refs: EvidenceRef[];
}

export interface KnowledgeCard {
  id: string;
  subject: string;
  status: "draft" | "approved";
  createdAt: string;
  scope: string[];
  rows: CardRow[];
  ruledOut: RuledOut[];
  sourceIds: string[];
  verified: { matched: number; total: number };
  reviewer: string;
  checklist: boolean[];
  comment: string;
  changesRequested?: boolean;
  approvedBy?: string;
  approvedAt?: string;
}

/** Facets already extracted and stored by the backend; no UI-generated claims. */
export interface CaseCardData {
  context: string;
  observation: string;
  reasoning: string;
  action: string;
  outcome: string | null;
  evidence: { turn_id: string; quote: string }[];
}

export interface Citation {
  id: string;
  number: number;
  /** Source id, or the knowledge card id when kind is "card". */
  sourceId: string;
  kind?: "source" | "card" | "knowledge" | "document";
  /** Original backend marker, preserved in storage and shown as a numbered reference. */
  marker?: string;
  title?: string;
  evidenceId?: string;
  statedBy?: string;
  recordType?: string;
  caseCard?: CaseCardData;
  scope?: Record<string, string>;
  reviewStatus?: "draft" | "approved" | "deprecated";
  excerpt: string;
  page?: number;
  turnId?: number;
  turnCode?: string;
  cardRow?: CardRowKey;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  status: "streaming" | "complete" | "stopped" | "failed";
  progress?: string;
}

export interface ChatThread {
  id: string;
  title: string;
  updatedAt: string;
  messages: ChatMessage[];
  /** Live conversations belong to exactly one KB. Unscoped threads are demos. */
  kb?: string;
}

export interface KnowledgeBase {
  name: string;
  /** Display name of a knowledge base created in the UI; falls back to name. */
  title?: string;
  description: string;
}

// Keeps mock and future SSE transports behind the same UI boundary.
export type AnswerEvent =
  | { event: "answer.delta"; text: string }
  | { event: "answer.reset" }
  | { event: "answer.citations"; citations: Citation[] }
  | { event: "run.status"; text: string }
  | { event: "run.completed" };

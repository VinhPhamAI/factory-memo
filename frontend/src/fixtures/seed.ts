import type { Source } from "@/types";
import { ax167, documents, followUpTurns, machines, SOURCE_IDS } from "./ax167";
import { recordedTurns } from "@/services/capture";

export { machines };

export interface SampleDocument {
  id: string;
  title: string;
  docType: string;
  fileName: string;
  text: string;
  sample: boolean;
  pageStart?: number;
  section?: string;
}

/** Documents offered by "Dùng tài liệu mẫu" so a recording run is deterministic. */
export const sampleDocuments: SampleDocument[] = [
  {
    id: SOURCE_IDS.faultReport,
    title: "Phiếu báo lỗi FR-L11-1007",
    docType: "Phiếu báo lỗi",
    fileName: "FR-L11-1007_scan.png",
    text: documents.faultReport,
    sample: true,
  },
  {
    id: SOURCE_IDS.minutes,
    title: "Biên bản họp 2026-10-08",
    docType: "Biên bản họp",
    fileName: "bien-ban-hop-2026-10-08_scan.png",
    text: documents.minutes,
    sample: true,
  },
  {
    id: SOURCE_IDS.expertNote,
    title: "Ghi chú chuyên gia — MR-JE bearing noise",
    docType: "Ghi chú chuyên gia",
    fileName: "expert-note-mr-je_scan.png",
    text: documents.expertNote,
    sample: true,
  },
  {
    id: SOURCE_IDS.manual,
    title: "MELSERVO-JE Troubleshooting Manual · tr. 71",
    docType: "Manual hãng",
    fileName: "melservo-je-troubleshooting_p71.pdf",
    text: documents.manual.text,
    sample: false,
    pageStart: documents.manual.page_start,
    section: documents.manual.section_path.at(-1),
  },
];

export function sampleToSource(
  sample: SampleDocument,
  createdAt: string,
): Source {
  return {
    id: sample.id,
    originalId: sample.id,
    title: sample.title,
    kind: "document",
    status: "reference",
    origin: "demo",
    createdAt,
    machines: sample.sample ? [ax167] : [machines[1]],
    summary: "",
    sample: sample.sample,
    docType: sample.docType,
    fileName: sample.fileName,
    text: sample.text,
    pageStart: sample.pageStart,
    pageEnd: sample.pageStart,
    section: sample.section,
  };
}

export function seedSources(): Source[] {
  const conversation: Source = {
    id: SOURCE_IDS.conversation,
    originalId: SOURCE_IDS.conversation,
    title: "Ghi âm trao đổi · MR-JE-AX167 tiếng mài trục Z",
    kind: "conversation",
    status: "draft",
    origin: "demo",
    createdAt: "2026-10-07T08:30:00Z",
    machines: [ax167],
    summary:
      "Junior technician and senior expert discuss a grinding noise on the Z-axis servo motor of MR-JE-AX167 (Line 11, machining cell B).",
    turns: recordedTurns,
  };
  const followUp: Source = {
    id: SOURCE_IDS.followUp,
    originalId: SOURCE_IDS.followUp,
    title: "Ghi âm follow-up 2026-10-09",
    kind: "conversation",
    status: "draft",
    origin: "demo",
    createdAt: "2026-10-09T09:00:00Z",
    machines: [ax167],
    summary: "",
    sample: true,
    turns: followUpTurns,
  };
  const dates = [
    "2026-10-07T07:10:00Z",
    "2026-10-08T10:00:00Z",
    "2026-10-08T11:30:00Z",
    "2026-10-06T00:00:00Z",
  ];
  return [
    conversation,
    ...sampleDocuments.map((doc, i) => sampleToSource(doc, dates[i])),
    followUp,
  ];
}

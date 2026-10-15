// Case MR-JE-AX167 demo data. Texts come verbatim from the repository files:
// conversation-mr-je-ax167.txt, case-mr-je-ax167.json, docs/demo-script-mr-je-ax167.md
// (Phụ lục A–D, Cảnh 5, Cảnh 6) and benchmark chunk melservo_je_p0071_c02.
// src/fixtures/ax167.test.ts re-reads those files and checks every quote.
import raw from "./ax167-sources.json";
import type {
  CardRow,
  EvidenceRef,
  MachineScope,
  RuledOut,
  TranscriptTurn,
} from "@/types";

export const CASE_ID = "f2bd9589-c74c-4511-8690-e42dd4ac1707";
export const CASE_SUBJECT = "MR-JE-AX167 servo motor";
export const MACHINE_ID = "MR-JE-AX167";

export const SOURCE_IDS = {
  conversation: "conv-ax167",
  faultReport: "doc-fr-l11-1007",
  minutes: "doc-minutes-2026-10-08",
  expertNote: "doc-expert-note-mr-je",
  manual: "melservo_je_p0071_c02",
  followUp: "conv-ax167-followup",
} as const;

export const machines: MachineScope[] = [
  {
    id: "MR-JE-AX167",
    name: "MR-JE-AX167",
    kind: "asset",
    family: "MR-JE",
    location: "Line 11 · Machining cell B",
  },
  { id: "MR-JE", name: "MELSERVO-JE", kind: "family", family: "MR-JE" },
];
export const ax167 = machines[0];
export const scopeChips = [
  "MR-JE-AX167",
  "Line 11",
  "Machining cell B",
  "wear",
];

export const speakers = {
  junior_01: {
    id: "junior_01",
    name: "junior_01",
    label: "Kỹ thuật viên",
    role: "vận hành",
    initials: "KT",
  },
  expert_01: {
    id: "expert_01",
    name: "expert_01",
    label: "Chuyên gia",
    role: "kỹ thuật viên cao cấp",
    initials: "CG",
  },
} as const;
export type SpeakerId = keyof typeof speakers;

function toTurns(
  turns: { code: string; speakerId: string; role: string; text: string }[],
): TranscriptTurn[] {
  return turns.map((turn, index) => ({
    id: index + 1,
    code: turn.code,
    speakerId: turn.speakerId,
    speaker: turn.speakerId === "expert_01" ? "Chuyên gia" : "Kỹ thuật viên",
    role: turn.role.replaceAll("_", " "),
    text: turn.text,
    seconds: 0,
  }));
}

/** T01–T14 without timestamps; capture.ts adds the simulated offsets. */
export const conversationTurns = toTurns(raw.conversation);
export const followUpTurns = toTurns(raw.followUp).map((turn, index) => ({
  ...turn,
  seconds: index * 14,
}));

export const documents = {
  faultReport: raw.faultReport,
  minutes: raw.meetingMinutes,
  expertNote: raw.expertNote,
  manual: raw.manual,
};

/** The eight evidence quotes stored with the extracted case (case JSON). */
export const caseEvidence: { quote: string; turnCode: string }[] = [
  {
    quote:
      "Operator keeps hearing a grinding noise from the HG servo motor during Z-axis rapid traverse, about three times a shift since Monday. No alarm, drive runs fine otherwise, and it only happens when the axis moves.",
    turnCode: "T01",
  },
  {
    quote: "Records say the motor's from 2019, around 21,000 running hours.",
    turnCode: "T03",
  },
  {
    quote:
      "On this cell, bearing noise on MR-JE-AX167 usually first shows up between 18,000 and 22,000 motor running hours. You're right in that window.",
    turnCode: "T04",
  },
  {
    quote:
      "Before you remove the load, jog MR-JE-AX167 at 50 mm/min and listen with a stethoscope right at the motor flange. That'll tell you if it's the bearing or the machine.",
    turnCode: "T06",
  },
  {
    quote:
      "No, if the bearing is suspect, tag the motor and send it to the Binh Duong workshop for replacement, not local repair.",
    turnCode: "T08",
  },
  {
    quote:
      "Don't do that. Always disconnect the motor power cable and wait 15 minutes before rotating the shaft by hand to release the brake safely.",
    turnCode: "T10",
  },
  {
    quote:
      "Then the motor's fine and you go maintain the machine itself. But if it makes noise when you turn it by hand, bearing's worn and the motor gets replaced.",
    turnCode: "T12",
  },
  {
    quote:
      "No, don't touch the gain to hide a noise. Check the bearing first, that's the real issue here.",
    turnCode: "T14",
  },
];

const turnRef = (code: string, quote: string): EvidenceRef => ({
  label: code,
  sourceId: code.startsWith("F")
    ? SOURCE_IDS.followUp
    : SOURCE_IDS.conversation,
  turnId: Number(code.slice(1)),
  quote,
});
const caseRef = (code: string) =>
  turnRef(code, caseEvidence.find((item) => item.turnCode === code)!.quote);

export const MANUAL_QUOTE =
  "When a noise occurs, the bearing is at the end of its life. Replace the servo motor. When no noise occurs, maintain the machine.";
export const FOLLOW_UP_QUOTE =
  "Replacement motor installed, Z-axis rapid traverse ran two full shifts with no grinding noise.";

export const cardRows: CardRow[] = [
  {
    key: "symptom",
    label: "Hiện tượng",
    question: "Hiện tượng là gì?",
    text: "Grinding noise from the HG servo motor during Z-axis rapid traverse, about 3 times a shift since Monday. No alarm. Quiet when the axis is idle.",
    refs: [
      caseRef("T01"),
      turnRef("T03", "Yeah, quiet when it's sitting still."),
      {
        label: "FR-L11-1007",
        sourceId: SOURCE_IDS.faultReport,
        quote: "Grinding noise during Z-axis rapid traverse",
      },
    ],
  },
  {
    key: "context",
    label: "Bối cảnh",
    question: "Bối cảnh như thế nào?",
    text: "MR-JE-AX167, Line 11, machining cell B. Motor from 2019, about 21,000 running hours.",
    refs: [
      turnRef(
        "T01",
        "Hey, need your input on MR-JE-AX167 on Line 11, machining cell B.",
      ),
      caseRef("T03"),
    ],
  },
  {
    key: "cause",
    label: "Nguyên nhân",
    question: "Nguyên nhân là gì?",
    text: "Worn motor bearing. On this cell, bearing noise on MR-JE-AX167 first shows up at 18,000–22,000 running hours. Encoder failure was considered but not confirmed.",
    refs: [
      caseRef("T04"),
      {
        label: "Ghi chú CG",
        sourceId: SOURCE_IDS.expertNote,
        quote:
          "On this cell, bearing noise on MR-JE-AX167 usually first shows up between 18,000 and 22,000 motor running hours.",
      },
      turnRef("F04", "Then the bearing's worn."),
    ],
  },
  {
    key: "check",
    label: "Cách kiểm tra",
    question: "Kiểm tra bằng cách nào?",
    text: "1) Before removing the load, jog at 50 mm/min and listen with a stethoscope at the motor flange. 2) Disconnect the motor power cable, wait 15 minutes, then rotate the shaft by hand. Noise means a worn bearing. No noise means the machine needs maintenance.",
    refs: [
      caseRef("T06"),
      caseRef("T10"),
      caseRef("T12"),
      {
        label: "Manual tr. 71",
        sourceId: SOURCE_IDS.manual,
        quote: MANUAL_QUOTE,
        page: 71,
      },
    ],
  },
  {
    key: "countermeasure",
    label: "Đối sách",
    question: "Đối sách là gì?",
    text: "Tag the motor and send it to the Binh Duong workshop for replacement, not local repair. Do not change the gain to hide the noise.",
    refs: [
      caseRef("T08"),
      caseRef("T14"),
      {
        label: "Biên bản 08/10",
        sourceId: SOURCE_IDS.minutes,
        quote:
          "Proposal to tune servo amplifier gain to suppress the noise: rejected.",
      },
    ],
  },
  {
    key: "result",
    label: "Kết quả",
    question: "Kết quả ra sao?",
    text: "Hand rotation confirmed the grinding. Motor sent to Binh Duong. The replacement motor ran two shifts with no noise.",
    refs: [
      turnRef(
        "F03",
        "Disconnected the motor power cable, waited 15 minutes, then turned the shaft by hand. It grinds when turned by hand.",
      ),
      turnRef("F05", FOLLOW_UP_QUOTE),
    ],
  },
  {
    key: "applies",
    label: "Điều kiện áp dụng",
    question: "Áp dụng khi nào?",
    text: "MR-JE-AX167 on Line 11, machining cell B. Motor at 18,000–22,000 running hours. Noise only while the axis moves, with no alarm. Safety: never rotate the shaft while the power cable is connected.",
    refs: [caseRef("T04"), caseRef("T10")],
  },
];

export const ruledOut: RuledOut[] = [
  {
    text: "Encoder failure",
    note: "not confirmed",
    refs: [
      turnRef(
        "T03",
        "I was thinking maybe encoder's going bad, should we just swap the whole motor out?",
      ),
      turnRef("T04", "Hold on, don't jump to the encoder."),
    ],
  },
  {
    text: "Adjust servo gain",
    note: "rejected, hides the noise",
    refs: [
      turnRef(
        "T13",
        "One more thing, someone suggested tweaking the servo amplifier gain parameters to kill the noise.",
      ),
      turnRef("T14", "No, don't touch the gain to hide a noise."),
    ],
  },
];

export const SUGGESTED_QUESTION =
  "MR-JE-AX167 trục Z kêu rít khi chạy nhanh, không có alarm. Do vòng bi hay do máy? Cần làm gì?";

/** Vietnamese rendering of the Cảnh 6 answer: same facts, no new ones. */
export const ANSWER_VI = [
  "Hãy kiểm tra vòng bi motor trước, và không chỉnh gain để che tiếng kêu [1].",
  "Trước khi tháo tải, chạy jog MR-JE-AX167 ở 50 mm/min và nghe bằng ống nghe (stethoscope) ngay tại mặt bích motor [1].",
  "Để xác nhận, ngắt cáp nguồn motor và chờ 15 phút để nhả phanh an toàn, rồi mới xoay trục bằng tay [1][2].",
  "Nếu có tiếng mài, vòng bi đã mòn: gắn thẻ motor và gửi về xưởng Binh Duong để thay mới, không sửa tại chỗ [1].",
  "Nếu không có tiếng kêu, hãy bảo dưỡng phần máy [2].",
  "Lần trước trên chính máy này, xoay trục bằng tay đã xác nhận vòng bi mòn và motor thay thế chạy hai ca không còn tiếng kêu [3].",
].join(" ");

// Simulated AI OCR: the "scanned page" is the document text, revealed line by
// line at fixed intervals. Field detection is plain pattern matching.

export const OCR_TIMING = {
  uploadStep: 90,
  uploadSteps: 14,
  line: 230,
  fields: 450,
};

export function ocrLines(text: string) {
  return text.split("\n").map((line) => line.replace(/\s+$/, ""));
}

export interface DetectedField {
  label: string;
  value: string;
}

export function detectFields(text: string, page?: number): DetectedField[] {
  const fields: DetectedField[] = [];
  const add = (label: string, value?: string) => {
    if (value && !fields.some((field) => field.value === value))
      fields.push({ label, value });
  };
  add("Máy", text.match(/MR-JE-[A-Z]+\d+/)?.[0]);
  if (!text.match(/MR-JE-[A-Z]+\d+/) && /MELSERVO|servo motor/i.test(text))
    add("Dòng máy", "MELSERVO-JE");
  add("Line", text.match(/Line \d+/)?.[0]);
  add("Trạm", text.match(/[Mm]achining cell [A-Z]\b/)?.[0]?.replace(/^m/, "M"));
  add("Mã phiếu", text.match(/FR-[A-Z0-9]+-\d+/)?.[0]);
  add("Ngày", text.match(/\b\d{4}-\d{2}-\d{2}\b/)?.[0]);
  if (page) add("Trang", String(page));
  if (/bearing/i.test(text)) add("Chủ đề", "bearing");
  return fields;
}

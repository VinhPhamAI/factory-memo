import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import type { InquiryContext } from "@/services/inquiries-api";

export type TextFields = Pick<
  InquiryContext,
  "situation" | "observations" | "attempted_actions" | "proposed_fix"
>;

export const FIELD_LABELS: [keyof TextFields, string][] = [
  ["situation", "Tình huống"],
  ["observations", "Quan sát"],
  ["attempted_actions", "Đã thử"],
  ["proposed_fix", "Giải pháp đề xuất"],
];

/** Editable title and the four text fields of a draft. */
export function InquiryForm({
  title,
  fields,
  onTitle,
  onField,
}: {
  title: string;
  fields: TextFields;
  onTitle: (value: string) => void;
  onField: (name: keyof TextFields, value: string) => void;
}) {
  return (
    <div className="inquiry-form">
      <label>
        Tiêu đề
        <Input
          value={title}
          maxLength={200}
          onChange={(event) => onTitle(event.target.value)}
        />
      </label>
      {FIELD_LABELS.map(([name, label]) => (
        <label key={name}>
          {label}
          <Textarea
            rows={4}
            maxLength={4000}
            value={fields[name]}
            onChange={(event) => onField(name, event.target.value)}
          />
        </label>
      ))}
    </div>
  );
}

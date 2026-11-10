/** Number of colour pairs defined for `.ticket-avatar[data-tone]` in index.css. */
const TONES = 6;

/** Initials of the first and last word of a name ("Nguyễn Văn An" → "NA"). */
function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  const first = words[0][0];
  const last = words.length > 1 ? words[words.length - 1][0] : "";
  return (first + last).toLocaleUpperCase("vi");
}

/** Pick a stable colour for a name, so one person looks the same on every card. */
function toneOf(name: string): number {
  let sum = 0;
  for (const char of name) sum += char.codePointAt(0) ?? 0;
  return sum % TONES;
}

/**
 * Round badge with a person's initials. It is decorative: put the name in
 * nearby text or pass `label` when the avatar stands alone.
 */
export function Avatar({
  name,
  label,
  size = "sm",
}: {
  name: string | null;
  /** Accessible name and tooltip; omit when the name is shown next to it. */
  label?: string;
  size?: "sm" | "md";
}) {
  return (
    <span
      className="ticket-avatar"
      data-size={size}
      data-tone={name ? toneOf(name) : "none"}
      title={label}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {name ? initials(name) : "?"}
    </span>
  );
}

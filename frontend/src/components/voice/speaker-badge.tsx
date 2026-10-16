import type { Speaker } from "@/services/voice-api";
import { speakerName, speakerSlot } from "@/services/voice-display";
import { cn } from "@/utils/cn";

/** Letter avatar plus the reviewer-given name, coloured per speaker. */
export function SpeakerBadge({
  label,
  speakers = [],
  compact = false,
}: {
  label: string | null;
  speakers?: Speaker[];
  compact?: boolean;
}) {
  return (
    <span
      className={cn("vc-speaker", compact && "compact")}
      data-slot={speakerSlot(label)}
    >
      <span className="vc-speaker-letter" aria-hidden="true">
        {label ?? "?"}
      </span>
      {!compact && (
        <span className="vc-speaker-name">{speakerName(label, speakers)}</span>
      )}
    </span>
  );
}

export function LevelMeter({
  level,
  active,
}: {
  level: number;
  active: boolean;
}) {
  const bars = 5;
  return (
    <span
      className={cn("vc-meter", active && "active")}
      role="meter"
      aria-label="Mức âm thanh micro"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(level * 100)}
    >
      {Array.from({ length: bars }, (_, index) => (
        <span
          key={index}
          className={cn(active && level > (index + 0.5) / (bars + 1) && "on")}
        />
      ))}
    </span>
  );
}

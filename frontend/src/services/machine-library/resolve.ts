import { CNC_MACHINE } from "./machines";
import { listMachines, listScenarios } from "./registry";
import type { MachineView } from "./types";

/** Fold diacritics and case so Vietnamese text matches ASCII keywords. */
export function foldText(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[đĐ]/g, "d")
    .toLowerCase();
}

const DIAGNOSIS =
  /\b(chan doan|chuan doan|diagnos\w*|su co|loi may|bao loi|alarm|dung may|may bi|may dang|day chuyen|production line|machine|mo hinh 3d|xem 3d)\b/;

/** Count distinct keyword hits of a global pattern in folded text. */
function hits(pattern: RegExp, text: string): number {
  return new Set(text.match(new RegExp(pattern.source, "g")) ?? []).size;
}

/** Match folded text against machine and fault keywords without history. */
function match(text: string): MachineView | null {
  const named = listMachines().filter((machine) => machine.keywords.test(text));
  const ranked = listScenarios()
    .map((fault, order) => ({ fault, order, score: hits(fault.keywords, text) }))
    .filter(({ score }) => score > 0)
    // A fault on a machine the text names wins; then more keyword hits, then library order.
    .sort((a, b) => {
      const named_a = named.some((machine) => machine.id === a.fault.machine) ? 1 : 0;
      const named_b = named.some((machine) => machine.id === b.fault.machine) ? 1 : 0;
      return named_b - named_a || b.score - a.score || a.order - b.order;
    });
  if (ranked.length) return { machine: ranked[0].fault.machine, scenario: ranked[0].fault.id };
  if (named.length) return { machine: named[0].id };
  return DIAGNOSIS.test(text) ? { machine: CNC_MACHINE.id } : null;
}

/**
 * Choose the machine and fault scenario a chat message or ticket is about.
 *
 * The message itself decides first. When it only asks for a diagnosis or
 * names a machine without a symptom, recent history may supply the symptom.
 *
 * Args:
 *   text: Chat message, agent answer or ticket text, Vietnamese or English.
 *   history: Earlier messages of the same conversation, oldest first.
 *
 * Returns:
 *   The view to open, or null when the text is not about a machine.
 */
export function resolveMachineView(text: string, history: string[] = []): MachineView | null {
  const own = match(foldText(text));
  if (own?.scenario || !history.length) return own;
  const earlier = match(foldText(history.slice(-6).join("\n")));
  if (!earlier) return own;
  if (!own) return DIAGNOSIS.test(foldText(text)) ? earlier : null;
  return earlier.machine === own.machine || !listMachines().some((machine) => machine.keywords.test(foldText(text)))
    ? earlier
    : own;
}

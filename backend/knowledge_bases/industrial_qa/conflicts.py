"""Conflict-detection policy of industrial QA: scope keys and anchors.

Hard keys must be equal, or unset on one side, for two records to be compared:
another family, asset or product is another scope, not a contradiction.
Records are compared after catalog normalization, so family names are
canonical. Soft keys only rank candidates. Anchors are identifiers whose
presence in both records keeps a candidate ahead of the similarity ranking.
"""
from __future__ import annotations

HARD_SCOPE_KEYS = ("machine_model", "machine_model_specific", "machine_id", "component_model",
                   "product_model", "configuration", "line_id", "station")
SOFT_SCOPE_KEYS = ("condition", "failure_mode")
_UNITS = r"(?:ms|s|sec|min|h|mm|um|µm|m|mV|V|mA|A|kW|W|kHz|Hz|rpm|r/min|°C|N·m|Nm|%|kbps|bps|MPa|kPa|Pa|pulses?)"
CONFLICT_ANCHORS = (
    r"\bP[A-F]\d{2}\b",                   # MR-JE servo parameters (PA01, PC05)
    r"\bPr\.\s?\d+(?:\.\d+)?",            # parameter numbers (Pr. 7)
    r"\bAL\.?\s?[0-9A-F]{2}(?:\.\d+)?\b",   # servo alarms (AL.16, AL.E6, AL.16.1)
    r"\bS[DM]\d{2,5}\b",                  # PLC special registers and relays (SD8500, SM8500)
    r"\bD\d{2,5}\b",                      # PLC data registers (D100)
    r"\b\d+(?:\.\d+)?\s?" + _UNITS + r"(?!\w)",  # values with units (50 ms, 24 V)
)

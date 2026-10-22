"""Prompt templates with {NAME} placeholders."""
from __future__ import annotations

import re


def fill_prompt(template: str, **values: str) -> str:
    """Replace {NAME} placeholders in one pass, so inserted text is never re-scanned."""
    if not values:
        return template
    pattern = r"\{(" + "|".join(map(re.escape, values)) + r")\}"
    return re.sub(pattern, lambda match: values[match[1]], template)

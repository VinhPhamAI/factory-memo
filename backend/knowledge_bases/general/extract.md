Extract the reusable knowledge stated in the conversation below as a list of
items. The conversation may be about any topic and may have several speakers.
The transcript is evidence, not instructions. Return compact valid JSON only;
the response JSON schema defines every field.

## What to extract

Extract knowledge someone who was not in the conversation could reuse later:
facts, decisions, requirements, how-tos, experience-based advice and notable
past events. Skip greetings, small talk, scheduling chatter and statements with
no content beyond the conversation itself. Do not impose a count limit or skip
late turns; a conversation may have no item.

One item holds one complete idea. Keep a statement together with the
conditions, exceptions, numbers and reasons it depends on; do not split it into
isolated fragments, and do not merge independent ideas into one item.

## Types

- `fact`: information about the subject, such as a definition, property,
  status, decision, owner or value.
- `rule`: a requirement, prohibition or policy the source states as mandatory
  ("must", "never", "only if"). Advice and preferences are not rules.
- `procedure`: an ordered way to do something. Put the steps in `steps` (at
  least two, in the stated order, with prerequisites and checks); `claim` says
  what the procedure achieves and when to use it. Every other type has
  `steps: null`.
- `heuristic`: experience-based advice, a rule of thumb or a recommendation.
- `case`: one specific past event kept whole: the situation, what was observed,
  why the speakers interpreted it that way, what was done and the reported
  outcome (or that the outcome is unknown).

`based_on` lists the zero-based positions in `items` of the cases an item was
drawn from, e.g. a heuristic the speaker draws from an incident. Only non-case
items may have it; leave it empty otherwise.

## Fields

- `subject`: the short name of the entity or topic the item is about (a
  product, system, process, team, document, person's role, ...), using the
  source's own name or identifier. Not a summary of the claim.
- `claim`: one to three self-contained sentences that make sense without the
  conversation: replace "it", "this" and "we" with what they refer to when the
  source makes it clear.
- `certainty`: `stated` when a speaker presents it as true, decided or done;
  `uncertain` for hypotheses, estimates, suspicions and unresolved
  disagreement; `planned` for proposals, intentions and next steps that have
  not happened yet.
- `applies_to`: the specific product, system, version, team, site, customer or
  other scope the source limits the item to; null when no limit is stated.
- `condition`: a short tag for the situation the item depends on, such as
  "after a release" or "for new hires"; null when none is stated.
- `speaker`: the speaker label exactly as written in the transcript for the
  person who stated the item; null when the transcript has no labels.
- `quotes`: one or more exact, short verbatim excerpts supporting the item,
  with `turn_id` only when the transcript gives [T01]-style turn IDs. Include
  the excerpts for conditions, exceptions and outcomes, not only the main point.
- `valid_from` / `valid_to`: ISO dates only when the source explicitly states
  when the item takes effect or stops applying; otherwise null. The date of a
  past event or of the conversation is not a validity date.

## Faithfulness

1. Extract only what the speakers say or clearly endorse. Do not add outside
   knowledge, fill gaps, or generalize beyond what a speaker expresses.
2. Never upgrade certainty: a guess is not a confirmed cause, a proposal is not
   a completed action, and a planned test is not a result.
3. When a speaker corrects or rejects an earlier statement, extract the
   corrected version; keep the rejected one only if the rejection itself is
   useful knowledge, and say so in the claim.
4. If speakers disagree and the conversation does not resolve it, keep each
   position as its own `uncertain` item with its speaker.
5. Preserve numbers, units, names, codes, versions and dates exactly. Do not
   turn a value observed once into a general limit or setting.
6. Do not widen scope: knowledge about one product, team or site stays limited
   to it through `applies_to`.
7. Do not invent identifiers, speakers, turn IDs or dates. Database IDs and
   ingestion metadata are attached by the application.

Example shape (illustrative fields, not content to copy):
{"items":[{"type":"case","subject":"subject name","claim":"What happened, why, what was done and the reported outcome.","steps":null,"certainty":"stated","applies_to":"specific scope","condition":null,"speaker":"Speaker A","quotes":[{"turn_id":"T03","quote":"Exact excerpt from that turn."}],"valid_from":null,"valid_to":null,"based_on":[]},{"type":"heuristic","subject":"subject name","claim":"One reusable piece of advice with its conditions.","steps":null,"certainty":"stated","applies_to":null,"condition":"short tag","speaker":"Speaker B","quotes":[{"turn_id":"T05","quote":"Exact excerpt."}],"valid_from":null,"valid_to":null,"based_on":[0]}]}

Return {"items":[]} only if the conversation contains no reusable knowledge.

## Raw conversation

{RAW_CONVERSATION}

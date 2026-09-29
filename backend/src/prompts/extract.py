EXTRACT_KNOWLEDGE_PROMPT = """Your task is to extract reusable expert knowledge from a raw conversation transcript.

The conversation may contain one or more independent knowledge items. Extract each meaningful knowledge item separately.

For each knowledge item, extract only the following fields:

* `subject`: The shortest noun or noun phrase that identifies only the physical component or part discussed. Prefer its explicit ID, tag, part number, or model when available (for example, `CAM-8`). Do not include symptoms, failure modes, causes, effects, actions, operating behavior, or any description of the claim in `subject`; put those details in `claim` or `context` instead. For example, use `CAM-8`, not `CAM-8 false rejects due to external light glare`.

* `claim`: The reusable knowledge expressed by the expert. Write it as a clear, self-contained statement while preserving the original meaning.

* `context`: Conditions under which the claim applies. Represent this as a JSON object. Include only context explicitly stated or strongly implied by the conversation, such as:

  * `machine_id`
  * `machine_model`
  * `line_id`
  * `station`
  * `component_id`
  * `component_model`
  * `product_model`
  * configuration or version
  * operating conditions
  * environmental conditions
  * symptoms
  * measurements such as temperature, pressure, speed, distance, duration, etc.

  Put conditional information under `condition` when appropriate.

* `type`: Classify the knowledge into one of:

  * `fact`: observation or generally stated property
  * `rule`: condition, recommendation, constraint, or action that should normally be followed
  * `case`: knowledge derived from a specific historical incident or experience
  * `procedure`: ordered diagnostic, maintenance, setup, or operational steps
  * `heuristic`: practical guidance, approximate rule, weak signal, trade-off, or experience-based judgment that is not guaranteed to always hold

* `valid_from`: Date from which the knowledge became valid, only if explicitly stated or clearly derivable from the conversation. Use ISO `YYYY-MM-DD` where possible. Otherwise return `null`.

* `valid_to`: Date until which the knowledge was valid, only if explicitly stated or clearly derivable from the conversation. Use ISO `YYYY-MM-DD` where possible. Otherwise return `null`.

## Extraction rules

1. Extract knowledge primarily from statements made by the expert, not from assumptions or hypotheses introduced only by the junior engineer.

2. Do not invent missing metadata. If the conversation does not identify a machine, model, component, condition, date, or other context, omit that field from `context`.

3. Keep `subject` strictly limited to the component or part identifier:

   * Prefer an explicit identifier alone when it unambiguously names the component, such as `CAM-8`, `S14`, or `AX-220`.
   * Otherwise use the shortest concrete component noun phrase stated in the conversation, such as `rear photoelectric sensor`.
   * Do not turn `subject` into a summary. Exclude faults, symptoms, causes, conditions, measurements, recommendations, and actions.
   * Put information such as `false rejects`, `external light glare`, `when pressure is low`, or `replace after 500 hours` in `claim` or `context`, never in `subject`.

4. Preserve important applicability conditions. For example:

   * "for product B17"
   * "when pressure is below 0.45 MPa"
   * "after one hour of operation"
   * "for serial numbers 500 and above"
   * "during the morning shift"

5. Preserve quantitative values and units exactly when they are important to the knowledge.

6. If the expert corrects the junior engineer's assumption, extract the expert's corrected knowledge rather than the incorrect assumption.

7. Distinguish between:

   * a universal/general rule,
   * a rule that applies only in a specific configuration,
   * a historical case,
   * and a heuristic that expresses uncertainty or experience rather than certainty.

8. A single conversation may contain multiple independent knowledge items. Split them when they have meaningfully different:

   * subjects,
   * claims,
   * applicability contexts,
   * knowledge types,
   * or validity periods.

9. Do not split a single causal explanation into many trivial facts if they are only useful together. Prefer one self-contained claim that preserves the causal relationship.

10. If a general rule has an explicit exception, normally extract them as separate knowledge items when the exception has a different applicable context.

11. Do not create `valid_from` or `valid_to` from:

    * the conversation timestamp,
    * dates of historical incidents,
    * approximate phrases such as "a few years ago",
      unless the speaker explicitly says that the knowledge became valid or stopped being valid at that time.

12. Serial-number ranges, machine versions, product models, firmware versions, and configurations belong in `context` unless they explicitly correspond to a time-based validity interval.

13. Do not evaluate whether the expert is factually correct. Extract what the expert states.

14. Do not infer conflict with other knowledge records. Conflict detection is handled by a separate system.

15. Do not extract ingestion metadata such as evidence ID, speaker ID, or record creation timestamp.

## Output format

Return valid JSON only.

Use this structure:

```json
{
  "knowledge_items": [
    {
      "subject": "string",
      "claim": "string",
      "context": {},
      "type": "fact | rule | case | procedure | heuristic",
      "valid_from": null,
      "valid_to": null
    }
  ]
}
```

If no reusable expert knowledge is present, return:

```json
{
  "knowledge_items": []
}
```

## Raw conversation

{RAW_CONVERSATION}
"""

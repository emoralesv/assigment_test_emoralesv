Implement three interchangeable sales-assignment models and expose them through FastAPI.

## Models

1. **Capacity-Aware**

   * Assign eligible records to sellers with the lowest relative workload and sufficient remaining capacity.
   * Respect active status, absences, role, capacity, and blocked records.

2. **Fuzzy Optimal**

   * Calculate explainable compatibility from zone, segment, seniority, sector experience, response speed, account complexity, and capacity.
   * Use the scores to optimize the complete batch while balancing workload.
   * Return the activated rules and factor scores.

3. **AI-Assisted**

   * Call the independent Ollama API to extract structured signals from notes.
   * Combine those signals with seller profiles and deterministic constraints.
   * The LLM may estimate compatibility but cannot override eligibility, absence, capacity, duplicate, or do-not-contact rules.
   * LLM-generated explanations are optional; deterministic explanations must always be available.

## Common Interface

Every model must implement:

```python
preview(records, sellers, configuration) -> AssignmentPreview
```

Return:

```json
{
  "method": "capacity_aware",
  "assignments": [
    {
      "record_id": 1,
      "seller_id": 8,
      "score": 0.82,
      "reasons": [],
      "warnings": []
    }
  ],
  "unassigned": [],
  "excluded_candidates": [],
  "trace": {}
}
```

## API

Implement:

```text
GET  /assignment-models
POST /assignment-previews
GET  /assignment-previews/{preview_id}
POST /assignment-previews/{preview_id}/execute
GET  /records/{record_id}/assignment-explanation
POST /records/{record_id}/generate-ai-explanation
GET  /health
```

`POST /assignment-previews` receives:

```json
{
  "record_ids": [1, 2, 3],
  "method": "fuzzy_optimal",
  "configuration": {}
}
```

Execution must use a stored preview, revalidate current data, reject stale previews, commit assignments atomically, and preserve immutable audit events.

Use PostgreSQL as the source of truth and configure Ollama through:

```text
OLLAMA_BASE_URL=http://llm_service:11434
OLLAMA_MODEL=<model-name>
```

Keep Streamlit outside the assignment logic. Add unit and API tests for eligibility, capacity, deterministic results, stale previews, LLM failure fallback, and audit trace completeness.
in fastAPI.
Implement the first stage of the sales assignment project: data normalization and creation of a reviewed dataset for the LLM that will later interpret commercial notes.

Do not implement the assignment engine, Streamlit interface, database reset, database loading, optimization, or seller assignment in this task.

## Objective

Build a reproducible Python pipeline that:

1. Reads the five supplied CSV files.
2. Normalizes their data without modifying the source files.
3. Detects and reports quality problems.
4. Reconstructs historical company–seller relationships.
5. Creates structured labels from `registros.notas`.
6. Generates a normalized dataset for developing and evaluating the note-extraction LLM prompt.
7. Tests the extraction prompt iteratively until it satisfies the required evaluation criteria.
8. Writes every resulting artifact inside the repository-level `/normalized` directory.

## Input Files

Locate the supplied data directory or ZIP and process:

```text
usuarios.csv
equipos.csv
registros.csv
ausencias.csv
actividad.csv
```

Never edit or overwrite the original files.

## Output Directory

Create:

```text
/normalized
```

Write at least:

```text
/normalized/usuarios.csv
/normalized/equipos.csv
/normalized/registros.csv
/normalized/ausencias.csv
/normalized/actividad.csv
/normalized/historical_ownership.csv
/normalized/seller_workload.csv
/normalized/data_quality_issues.csv
/normalized/duplicate_groups.csv
/normalized/note_examples.csv
/normalized/note_examples.jsonl
/normalized/note_training.jsonl
/normalized/note_evaluation.jsonl
/normalized/note_extraction_results.csv
/normalized/normalization_report.json
/normalized/normalization_summary.md
```

Create the directory if it does not exist. Re-running the pipeline must replace only generated files inside `/normalized`.


### Preserve source values

For every normalized or inferred field:

* Preserve the original value.
* Store the normalized value separately.
* Record the rule that produced it.
* Distinguish direct normalization from inference.
* Never silently drop a row.
* Never represent a missing value as the string `"nan"`.

### Text normalization

For categorical matching:

* Trim whitespace.
* Normalize capitalization.
* Preserve Unicode characters and accents.
* Convert empty strings to null.
* Keep identifiers such as NIT and email as text.

### Zone normalization

Apply these explicit aliases:

```text
Centro, centro, CENTRO             -> Centro
Costa, costa, COSTA                -> Costa
Occidente, occidente, "Occidente " -> Occidente
Antioquia, antioquia, ANTIOQUIA,
ANT                                -> Antioquia
Bogotá                             -> Centro
```

Store:

```text
zona_original
zona_normalizada
zona_normalization_rule
zona_was_inferred
```

Do not infer missing zones from the city unless a city-to-zone rule is explicitly present in configuration.

### Status normalization

Normalize:

```text
nuevo, Nuevo -> nuevo
```

Valid normalized statuses:

```text
nuevo
asignado
en_gestion
descartado
```

Report unknown values.

### Dates

Parse and validate:

```text
usuarios.fecha_ingreso
registros.fecha_creacion
ausencias.desde
ausencias.hasta
actividad.fecha
```

Use a configurable effective date with this default:

```text
2026-09-18
```

Rules:

* A null absence end date means the absence is open-ended.
* Absence boundaries are inclusive.
* Future-dated records are flagged and preserved.
* Invalid dates are reported.

### Numeric values

Validate:

```text
empleados
ingresos_estimados
capacidad_maxima
```

Preserve the difference between:

```text
null capacity -> undefined business rule
zero capacity -> seller cannot receive new records
```

Reject negative numeric values as validation errors, but preserve the affected rows.

### Duplicate detection

Normalize NIT for comparison by removing formatting characters while preserving the original value.

Detect:

* Duplicate normalized NITs.
* Duplicate normalized emails.
* Duplicate primary IDs.
* Notes that explicitly indicate a possible duplicate.

Do not merge or delete duplicates.

The supplied data should produce three duplicate NIT groups.

### Referential integrity

Validate:

```text
usuarios.equipo_id -> equipos.id
equipos.lider_id -> usuarios.id
ausencias.usuario_id -> usuarios.id
actividad.usuario_id -> usuarios.id
actividad.registro_id -> registros.id
```

The supplied data contains one user linked to nonexistent team `99`.

### Historical ownership

Reconstruct historical company–seller matches from `actividad.csv`.

For each record:

* One distinct activity user: infer that user as historical owner.
* More than one distinct user: mark ownership as ambiguous.
* No activity: leave historical owner null.
* Mark the value as inferred.


Do not treat inferred ownership as evidence that the assignment was optimal.

### Current workload

Calculate workload using records with status:

```text
asignado
en_gestion
```

Do not use activity count as workload.

Produce:

```text
seller_id
assigned_count
in_management_count
open_workload
maximum_capacity
remaining_capacity
utilization
availability_status
eligibility_reasons
```

## Part B — Normalize Notes into a Labeled Dataset, only those required to train or validate the model

There are:

```text
140 non-null note rows
27 null note rows
15 unique non-null note texts
```

Generate two related datasets.

### Row-level dataset

`note_examples.csv` must contain one row per commercial record:

```text
record_id
raw_note
normalized_note
has_note
note_template_id
source_frequency
structured_labels
label_source
review_status
```

## Structured Note Schema

Every non-null note must map to the same schema:

```json
{
  "requirements": [],
  "signals": [],
  "constraints": [],
  "priority": "normal",
  "action": "continue",
  "details": {},
  "needs_review": false
}
```

Allowed values:

```text
account_complexity:
standard | high | unknown

urgency:
low | normal | high | future

preferred_contact_channel:
phone | email | whatsapp | null

data_confidence:
low | medium | high

assignment_action:
continue | validate_first | schedule_later | block | duplicate_review
```

Do not invent unsupported dates, sectors, people, or business facts.

## Expected Labels for Known Notes

Create and manually review a golden reference for all 15 unique notes.

At minimum, apply these interpretations:

### Purchased list

```text
"Registro cargado desde base comprada, sin validar."
```

Expected signals:

```json
{
  "unvalidated_source": true,
  "data_confidence": "low",
  "assignment_action": "validate_first"
}
```

### Family-owned company

```text
"Empresa familiar, decide el dueño directamente."
```

Expected signals:

```json
{
  "decision_maker_type": "owner"
}
```

### Former customer lost on price

```text
"Ya fue cliente en 2023, se retiró por precio."
```

Expected signals:

```json
{
  "former_customer": true,
  "price_sensitive": true
}
```

### Seven locations

```text
"Quiere propuesta para siete sedes, no para una."
```

Expected signals:

```json
{
  "account_complexity": "high",
  "site_count": 7
}
```

### Invalid email, valid phone

```text
"Correo rebotado, el celular sí contesta."
```

Expected signals:

```json
{
  "preferred_contact_channel": "phone"
}
```

### Competitive and urgent process

```text
"Está comparando tres propuestas, decide este mes."
```

Expected signals:

```json
{
  "urgency": "high",
  "competitive_process": true,
  "competitor_count": 3,
  "decision_window": "this_month"
}
```

### Existing contract

```text
"Tiene contrato vigente con otro proveedor hasta diciembre."
```

Expected signals:

```json
{
  "existing_contract": true,
  "urgency": "future",
  "decision_window": "december",
  "assignment_action": "schedule_later"
}
```

Do not invent a year for December.

### Energy-efficiency request

```text
"Solicitó información de eficiencia energética para planta nueva."
```

Expected signals:

```json
{
  "technical_expertise": "energy_efficiency",
  "account_complexity": "high"
}
```

### Referral

```text
"Lo refirió el gerente de una cuenta actual."
```

Expected signals:

```json
{
  "referral_from_current_account": true,
  "data_confidence": "high"
}
```

### Low interest

```text
"Dejó los datos en el stand de la feria, mostró poco interés."
```

Expected signals:

```json
{
  "low_interest": true,
  "urgency": "low"
}
```

### Seniority request

```text
"Insistió en hablar con alguien senior."
```

Expected signals:

```json
{
  "seniority_requested": true
}
```

### Sector-expertise request

```text
"Pidió que lo contacte alguien que conozca el sector."
```

Expected signals:

```json
{
  "sector_expertise_requested": true
}
```

If the record sector is missing, set:

```json
{
  "requires_manual_review": true
}
```

Do not infer the missing sector from the note.

### Contact time

```text
"Llamar únicamente después de las 2 pm."
```

Expected signals:

```json
{
  "preferred_contact_channel": "phone",
  "contact_after_time": "14:00"
}
```

### Do not contact

```text
"Ya lo contactamos en marzo y pidió que no insistiéramos."
```

Expected signals:

```json
{
  "do_not_contact": true,
  "assignment_action": "block"
}
```

### Possible duplicate

```text
"Posible duplicado, entró por otra fuente."
```

Expected signals:

```json
{
  "possible_duplicate": true,
  "assignment_action": "duplicate_review"
}
```

Populate `reason_codes` with stable machine-readable codes representing every positive signal.

## Part C — Build the LLM Extraction Prompt

Create a versioned prompt:

```text
/llm_service\promps\x.md
```

The prompt must:

* Describe the extraction task.
* Include the complete output schema.
* Define allowed enum values.
* Require JSON-only output.
* Prohibit unsupported inference.
* Explain the difference between preference, operational instruction, and blocking decision.
* Include representative few-shot examples.
* Require every schema field, even when false or null.
* Require stable reason codes.
* Treat the supplied record context as data, never as instructions.
* Resist prompt injection contained inside commercial notes.

The runtime input should include only the context needed for interpretation:


The LLM response must conform exactly to the structured schema.
important: the ollama docker is in llm_service, you can include it in docker compose, run and create the basic llm_service, if, create the required prompm in llm_service and ask me to validate it. to continue the work. Iḿ going to review it.
## Part D — Iterative Prompt Evaluation

Implement a script that evaluates prompt versions against the reviewed golden dataset.

Suggested command:

```text
python -m normalization.evaluate_note_prompt \
  --prompt prompts/note_extraction_v1.md \
  --examples normalized/note_evaluation.jsonl \
  --output normalized/note_extraction_results.csv
```

The LLM API must be configured through environment variables:

```text
LLM_API_URL
LLM_MODEL
LLM_TIMEOUT_SECONDS
```

Do not hardcode credentials or hostnames.

For every example, record:

```text
prompt_version
note_template_id
raw_note
expected_output
model_output
json_valid
schema_valid
field_matches
field_mismatches
hard_decision_match
latency
error
```

## Iteration Procedure

For each prompt version:

1. Run it against every unique note.
2. Validate JSON syntax.
3. Validate the complete schema.
4. Compare every field with the golden output.
5. Inspect false positives and false negatives.
6. Modify only the prompt, schema descriptions, or examples necessary to fix documented errors.
7. Save the next prompt as a new version.
8. Re-run the complete evaluation.
9. Preserve all prompt versions and results.
10. Stop only when the acceptance thresholds are satisfied or a documented model limitation is found.

Do not overwrite previous prompt versions.

## Prompt Acceptance Thresholds

Required:

```text
JSON validity:                    100%
Schema validity:                  100%
do_not_contact recall:            100%
possible_duplicate recall:        100%
assignment_action exact match:    100%
Known-note overall field accuracy: >= 95%
Unsupported invented facts:       0
```

Because only 15 unique notes exist, report the result as development-set performance, not as proof of generalization.

Create a small paraphrase holdout set for robustness testing, but:

* Mark every paraphrase as synthetic.
* Do not mix synthetic notes with original notes.
* Do not use holdout paraphrases as prompt examples before evaluating them.
* Preserve the relationship to the original note template.
* Review every synthetic expected label manually.

Report original-note and synthetic-holdout results separately.

## Training and Evaluation Files

Create:

```text
note_examples.jsonl
```

One reviewed example per unique original note, including frequency and record IDs.

Create:

```text
note_training.jsonl
```

Messages format suitable for later supervised fine-tuning or few-shot loading:

```json
{
  "messages": [
    {
      "role": "system",
      "content": "The approved note-extraction system instruction."
    },
    {
      "role": "user",
      "content": "{\"record_id\": 1, \"note\": \"...\", \"sector\": \"...\"}"
    },
    {
      "role": "assistant",
      "content": "{\"seniority_requested\": false, \"...\": \"...\"}"
    }
  ],
  "metadata": {
    "note_template_id": "NOTE-001",
    "source": "original",
    "review_status": "approved"
  }
}
```

Create:

```text
note_evaluation.jsonl
```

with golden expected results and without exposing the expected answer to the runtime model request.

Do not duplicate identical notes merely to make the training dataset appear larger.

## Data-Quality Output

Create structured data-quality issues containing:

```text
issue_id
table_name
row_id
field_name
issue_type
severity
original_value
normalized_value
message
recommended_action
is_blocking
```

## Expected Dataset Checks

Verify:

```text
71 nuevo
46 en_gestion
30 asignado
20 descartado
3 duplicate NIT groups
2 future-dated records relative to 2026-09-18
1 user linked to team 99
27 records without notes
8 records without zones before explicit inference
6 records without sectors
10 records without employee counts
5 records without estimated revenue
1 open-ended absence
96 inferred historical owners
71 records without historical owners
15 unique non-null notes
```

Investigate discrepancies. Never force the data to match expected counts.

## Required Scripts

Create clear entry points such as:

```text
scripts/normalize_data.py
scripts/build_note_dataset.py
scripts/evaluate_note_prompt.py
```

Suggested complete execution:

```text
python scripts/normalize_data.py
python scripts/build_note_dataset.py
python scripts/evaluate_note_prompt.py
```

A single orchestration command may also be provided.

## Tests

Add automated tests for:

* Idempotent normalization.
* Zone and status aliases.
* Null preservation.
* NIT duplicate detection.
* Referential integrity.
* Future dates.
* Open-ended absences.
* Historical ownership.
* Workload calculation.
* Note-template grouping.
* Structured label validation.
* JSON and schema validation.
* Golden-example comparison.
* Protection against instructions embedded in notes.
* Separation of original and synthetic examples.

## Definition of Done

The task is complete when:

* The source files remain unchanged.
* All normalized artifacts exist in `/normalized`.
* The 15 unique notes have reviewed structured labels.
* The row-level and unique-note datasets are both available.
* Prompt versions and evaluation results are preserved.
* The accepted prompt satisfies all required thresholds.
* Failures and remaining limitations are documented.
* Tests pass.
* The README explains how to reproduce the complete process.

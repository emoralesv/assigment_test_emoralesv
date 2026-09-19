Implement the second stage of the sales assignment project: recreate the local development database and load the normalized artifacts produced by the first stage.

This task intentionally performs a destructive reset, but only against the explicitly configured local or test project database.

Do not run against production, staging, shared infrastructure, or an unidentified database.

## Objective

Create a script that:

1. Validates that the target is an authorized local or test database.
2. Deletes the existing application database or schema when it exists.
3. Recreates the required database structure.
4. Loads the normalized files from `/normalized`.
5. Verifies row counts, relationships, and important business invariants.
6. Produces a clear import report.
7. Can be run repeatedly with the same result.

## Safety Requirements

Before any destructive operation:

1. Read the target from environment configuration.
2. Require:

```text
APP_ENV=local
```

or:

```text
APP_ENV=test
```

3. Reject:

```text
production
prod
staging
shared
```

4. Require an explicit database name.
5. Reject empty, default, administrative, or system database names such as:

```text
postgres
template0
template1
```

6. Require the command-line confirmation flag:

```text
--confirm-reset
```

7. Print the exact target host, port, and database before resetting.
8. Never use unresolved shell variables or wildcard database names.
9. Never delete files or databases outside the configured project target.

If any safety check fails, stop without making changes.

## Input Directory

Load only generated artifacts from:

```text
/normalized
```

Required files:

```text
usuarios.csv
equipos.csv
registros.csv
ausencias.csv
actividad.csv
historical_ownership.csv
seller_workload.csv
data_quality_issues.csv
duplicate_groups.csv
note_examples.csv
note_examples.jsonl
normalization_report.json
```

Fail clearly if a required input is missing.

## Database

Use PostgreSQL.

Configuration must come from environment variables, preferably:

```text
APP_ENV
DATABASE_HOST
DATABASE_PORT
DATABASE_NAME
DATABASE_USER
DATABASE_PASSWORD
```

Alternatively support a validated `DATABASE_URL`.

Do not hardcode credentials.

## Required Tables

Create tables for at least:

```text
teams
users
records
absences
activities
historical_ownership
seller_workload_snapshots
record_signals
data_quality_issues
duplicate_groups
normalization_runs
prompt_versions
llm_extractions
assignment_previews
assignment_preview_items
assignments
assignment_events
llm_explanations
```

The assignment-related tables may initially be empty, but their structure should support later development.

## Source and Normalized Values

Where applicable, preserve:

* Original source values.
* Normalized values.
* Whether a value was inferred.
* Normalization rule.
* Import run identifier.
* Source filename.
* Creation and update timestamps.

Do not discard audit metadata produced by the first stage.

## Relationship Handling

Create explicit primary and foreign keys.

Validate:

```text
users.team_id -> teams.id
teams.leader_id -> users.id
absences.user_id -> users.id
activities.user_id -> users.id
activities.record_id -> records.id
historical_ownership.record_id -> records.id
historical_ownership.user_id -> users.id
record_signals.record_id -> records.id
```

The source has a circular relationship:

```text
users reference teams
teams reference leaders in users
```

Handle it safely by either:

1. Loading teams without leaders, loading users, and then updating team leaders, or
2. Using properly deferred constraints.

Do not disable referential validation permanently.

The source also contains a user linked to nonexistent team `99`. Preserve the normalized row and its data-quality issue without creating a false team. If the operational table requires valid foreign keys, store the invalid original reference separately and keep the normalized `team_id` null until reviewed.

## Reset Strategy

Create:

```text
scripts/reset_database.py
```

Suggested command:

```text
python scripts/reset_database.py --confirm-reset
```

The reset must:

1. Acquire a database connection.
2. Re-run all safety validations.
3. Remove only the project-owned schema or explicitly named local database.
4. Recreate the schema using migrations or project metadata.
5. Start a new normalization/import run.
6. Load the normalized files in dependency order.
7. Validate the import.
8. Commit only after all blocking validations succeed.
9. Roll back the import transaction when a blocking error occurs.
10. Write the final import report.

Prefer resetting a project-owned schema over dropping the entire PostgreSQL server database when both approaches satisfy the project requirements.

## Suggested Loading Order

Use a sequence that respects relationships:

```text
1. normalization_runs
2. teams without leader references
3. users
4. update team leaders
5. records
6. absences
7. activities
8. historical_ownership
9. seller_workload_snapshots
10. record_signals
11. data_quality_issues
12. duplicate_groups
13. prompt_versions
14. llm_extractions
```

Assignment tables remain empty until the application creates previews and assignments.

## Expected Imported Counts

Verify at minimum:

```text
teams:       5
users:      18
records:   167
absences:    8
activities: 264
```

Also verify:

```text
71 nuevo records
46 en_gestion records
30 asignado records
20 descartado records
96 inferred historical owners
71 records without historical owners
3 duplicate NIT groups
15 unique note templates
```

Verify that:

* Every normalized record remains present.
* No primary ID is duplicated.
* Valid foreign keys resolve.
* Invalid source relationships remain represented in data-quality issues.
* Every `record_signal` references an existing record.
* Null values remain null.
* Original and normalized values remain distinguishable.
* No assignment records are created during import.

If expected counts differ, roll back or mark the import failed and report the exact discrepancy. Do not insert fabricated rows to match expectations.

## Idempotency

Running the reset script twice with the same normalized inputs must produce:

* The same operational data.
* The same row counts.
* No duplicate records.
* No duplicate issue rows.
* A new import-run identifier only when the previous database was intentionally reset.

## Import Report

Create a report such as:

```text
/normalized/database_import_report.json
```

Include:

```text
started_at
completed_at
environment
database_target
normalization_run_id
input_files
input_checksums
rows_loaded_by_table
rows_rejected_by_table
warnings
blocking_errors
foreign_key_results
business_invariant_results
transaction_status
```

Do not include credentials.

## Docker Compose Compatibility

The script must work with the PostgreSQL service defined in `docker-compose.yml`.

Wait for PostgreSQL health before attempting the reset.

Do not assume that `depends_on` alone guarantees database readiness.

## Tests

Add tests for:

* Safety rejection in production-like environments.
* Rejection without `--confirm-reset`.
* Rejection of system database names.
* Missing normalized files.
* Successful empty-database creation.
* Successful replacement of an existing local database or schema.
* Correct load order.
* Circular team/user references.
* Invalid source team `99`.
* Transaction rollback.
* Expected row counts.
* Foreign-key integrity.
* Null preservation.
* Idempotent repeated execution.

Use an isolated test database or disposable PostgreSQL container.

## Definition of Done

The task is complete when:

* The reset script refuses unsafe targets.
* The authorized local/test database can be recreated with one command.
* All normalized artifacts are imported.
* Expected counts and relationships are verified.
* Invalid source relationships remain auditable.
* No assignment is created during import.
* Re-running the command produces the same database state.
* Tests pass.
* The README documents configuration, reset, loading, verification, and recovery steps.

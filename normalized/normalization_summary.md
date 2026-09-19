# Normalización

Fecha efectiva: 2026-09-18. CSV originales conservados y hashes verificados.

| Comprobación | Esperado | Observado | Coincide |
|---|---:|---:|---|
| nuevo | 71 | 71 | True |
| en_gestion | 46 | 46 | True |
| asignado | 30 | 30 | True |
| descartado | 20 | 20 | True |
| duplicate_nit_groups | 3 | 3 | True |
| future_records | 2 | 2 | True |
| users_team_99 | 1 | 1 | True |
| missing_notes | 27 | 27 | True |
| missing_zones | 8 | 8 | True |
| missing_sectors | 6 | 6 | True |
| missing_employees | 10 | 10 | True |
| missing_revenue | 5 | 5 | True |
| open_ended_absences | 1 | 1 | True |
| inferred_owners | 96 | 96 | True |
| no_owners | 71 | 71 | True |
| unique_notes | 15 | 15 | True |

## Límites

- Ownership and workload are inferred from activity, not verified current assignments.
- Leaders and sellers are reported separately by source role; no seller assignment is performed.
- No activity, ambiguous ownership and invalid owners are not attributed; see unattributed_active_records.
- Missing capacity has no implicit default; zero capacity prohibits new records.

Evaluación del LLM: consultar `note_extraction_results*.summary.json`; estos controles no prueban precisión del modelo.

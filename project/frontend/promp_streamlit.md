Implement a simple Streamlit frontend for the sales assignment platform.

Streamlit must contain no assignment or business logic. It must communicate exclusively with FastAPI using `API_BASE_URL`.

Create these views:

1. **Dashboard** — pending records, available sellers, capacity utilization, assignments, and data-quality warnings.
2. **Records** — searchable and filterable table, record details, original note, extracted signals, warnings, and signal editing.
3. **Sellers** — availability, absence, seniority, expertise, current workload, capacity, and historical indicators.
4. **Assignment** — select records, choose `capacity_aware`, `fuzzy_optimal`, or `ai_assisted`, request a preview, inspect assignments and exclusions, and execute the approved preview.
5. **Audit** — assignment history, reassignment events, deterministic explanation, and optional AI explanation.

Requirements:

* Use a reusable FastAPI client with timeouts and clear error handling.
* Store preview state in `st.session_state`.
* Never execute an assignment without displaying and approving its preview.
* Show stale-preview conflicts and request a new preview.
* Disable invalid actions and display API validation errors.
* Add loading, empty, success, and failure states.
* Keep the UI clean, responsive, and appropriate for a technical demo.
* Configure the backend through `API_BASE_URL=http://api:8000`.
* Add a Dockerfile, health check, requirements file, and README instructions.
* Add basic tests for API-client behavior and the preview-to-execution workflow.

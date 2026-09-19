# Build an Explainable Sales Assignment Engine

**Type:** Technical Challenge  
**Priority:** Urgent  
**Due date:** September 19, 2026, 5:00 PM  
**Suggested labels:** `AI`, `Data`, `Assignment`, `Frontend`, `Auditability`

## Objective

Build a system that cleans and enriches the supplied commercial data, assigns pending company records to suitable sales representatives, and allows an operator to understand, preview, execute, and audit every decision.

The product should address delayed follow-up, uneven workloads, unclear ownership, disputes over valuable accounts, and the lack of an explanation for historical assignments.

## 1. Data Ingestion and Cleaning

Load the five supplied CSV files: users, teams, records, absences, and activity.

The system must identify and handle:

- Inconsistent capitalization, spacing, and zone names.
- Missing sectors, zones, capacities, dates, and notes.
- Duplicate companies and repeated tax IDs.
- Records with future creation dates.
- Users linked to invalid teams.
- Inactive users, open-ended absences, and zero capacity.
- Historical company–seller relationships inferred from activity.

The original values must remain available for auditing. Cleaned or inferred values must be distinguishable from source data.

## 2. Company Intelligence from Notes

Convert free-text notes into structured business signals that can be reviewed and edited in the web interface.

Relevant signals include:

- Senior representative requested.
- Sector expert requested.
- Technical expertise required.
- Urgent decision or competitive process.
- Complex or multi-site account.
- Former customer or price sensitivity.
- Referral from an existing account.
- Low interest or unvalidated source.
- Preferred contact channel or contact time.
- Existing contract and future follow-up date.
- Do-not-contact request.
- Possible duplicate.

Some signals become preferences, while others become mandatory decisions. For example, a seniority request can influence the match, but a do-not-contact request must block assignment.

The operator must be able to see the original note, review the extracted signals, and correct them before execution.

## 3. Seller Profiling

Create a clear profile for each sales representative using the available operational and historical data.

The profile should include:

- Active status and current absence.
- Team, zone, and declared segment expertise.
- Tenure and relative seniority.
- Maximum capacity, current workload, and remaining capacity.
- Historical number of companies handled.
- Observed response speed.
- Historical sector experience.
- Experience with complex or technical accounts.
- Historical meetings, quotations, and follow-up activity.
- Confidence level for metrics based on limited samples.

These indicators describe observed behavior. They must not be presented as sales performance because the source data does not contain conversions or closed revenue.

## 4. Assignment Engine

The console must offer exactly four assignment methods:

### 4.1 Round Robin

Distribute records sequentially among eligible representatives.

### 4.2 Capacity-Aware

Prioritize representatives with more remaining capacity and lower relative workload.

### 4.3 Fuzzy Optimal Assignment

Evaluate gradual concepts such as seniority, sector experience, response speed, account complexity, and available capacity. Use these compatibility results to produce a balanced assignment for the selected batch.

The method must provide understandable reasons, such as strong sector experience, sufficient capacity, or a good seniority match.

### 4.4 AI-Assisted Optimal Assignment

Use the structured information extracted from notes together with company and seller profiles. Produce an explainable assignment that respects all mandatory business restrictions.

AI may interpret business context and improve compatibility assessment, but it must not override eligibility, absence, capacity, duplication, or contact restrictions.

## 5. Operations Console

Provide a web console where an operator can:

- View and filter pending records.
- Review data-quality warnings.
- Open a company and inspect its original and structured information.
- Review and edit signals extracted from notes.
- View seller profiles, workload, availability, and observed experience.
- Select records and choose one of the four assignment methods.
- Configure business preferences for the selected method.
- Preview assignments before changing ownership.
- Inspect reasons, warnings, and excluded sellers.
- Execute the approved preview.
- Reassign a record without deleting its previous history.
- Open a record and answer: “Why was this record assigned here?”

If relevant data changes after preview, the system must request a new preview before execution.

## 6. Traceability and Evaluation

Every assignment and reassignment must preserve:

- Company and selected representative.
- Previous representative, when applicable.
- Assignment method.
- Business preferences used.
- Extracted and operator-edited note signals.
- Eligible and excluded representatives, with reasons.
- Human-readable explanation.
- Operator and execution date.
- Relationship to any previous assignment.
- Complete AI input and output when AI is used.

The solution should also make it possible to compare the four methods using clear indicators such as:

- Number of assigned and unassigned records.
- Workload distribution.
- Capacity utilization.
- Zone and segment compatibility.
- Fulfillment of seniority or expertise requests.
- Data-quality warnings and blocked records.

Historical activity can be used to reconstruct previous company–seller matches, but it must not be treated as proof that those assignments were optimal.

## Acceptance Criteria

- [x] All supplied CSV files can be loaded and reviewed.
- [x] Data inconsistencies and invalid relationships are reported clearly.
- [x] Duplicate, future-dated, and restricted records are handled before assignment.
- [x] Historical company–seller matches can be reconstructed from activity.
- [x] Notes are converted into structured, reviewable business signals.
- [x] Seller profiles show availability, capacity, seniority, experience, and confidence.
- [x] Inactive, absent, unavailable, or ineligible representatives are not assigned records.
- [x] Operators can preview and understand assignments before execution.
- [x] Stale previews cannot be executed silently.
- [x] Every record provides a clear answer to why it was assigned to its current owner.
- [x] AI-assisted decisions preserve the complete input and output used.
- [x] The methods can be compared using the same batch of records.

## Required Deliverables

- [x] Private GitHub repository containing the complete project.
- [ ] Invite `diegogarsfe` and `tatianatorobia` as collaborators.
- [ ] README explaining the product, assumptions, assignment methods, and how to run it.
- [ ] Short video or demo showing data review, note interpretation, preview, assignment, reassignment, and explanation.
- [ ] One or two development prompts included exactly as originally used, with a brief explanation of how their results were verified.
- [x] Optional deployed service URL.

## Demo Flow

1. Load and review the supplied data.
2. Show at least one data-quality issue.
3. Open a record and review the signals extracted from its note.
4. Inspect seller profiles and availability.
5. Preview the same batch using different assignment methods.
6. Execute one approved preview.
7. Reassign one record.
8. Show the complete explanation and history.

## Out of Scope

- CRM integrations.
- Automated messaging or sales follow-up.
- Training a proprietary language model.
- Treating activity counts as confirmed sales performance.
- Enterprise authentication unless added as an optional enhancement.

## Definition of Done

The issue is complete when the repository and README are accessible to the requested reviewers, the application runs through documented steps, the acceptance criteria are satisfied, the demo is available, and the required AI-usage prompts are included before the deadline.

## Contact

e- [tatiana.toro@bia.app](mailto:tatiana.toro@bia.app)
- [diego.garcia@bia.app](mailto:diego.garcia@bia.app)

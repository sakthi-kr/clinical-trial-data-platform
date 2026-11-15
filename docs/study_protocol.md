# Synthetic study protocol (demonstration only)

## Identity and objective

- **Study ID:** SYN-CTQA-001
- **Design:** entirely fictional, four-site, twelve-week study for testing data-management software
- **Participants:** 500 by default, all computer-generated; adult ages 18–80
- **Groups:** ARM_A and ARM_B are arbitrary labels; not treatment arms from a real trial. No efficacy/safety mechanism is modeled.
- **Sites:** four invented sites; none corresponds to a real research facility.
- **Start and enrolment:** simulated enrolment dates follow a configured start date and an enrolment window.
- **Purpose:** evaluate data-pipeline reproducibility, quality checks and auditability; **not** clinical research.

## Visit schedule

| Code | Nominal day from enrolment | Initial generated actual date |
|---|---:|---|
| BASELINE | 0 | day 0 |
| WEEK04 | 28 | nominal day ± 2 days |
| WEEK08 | 56 | nominal day ± 2 days |
| WEEK12 | 84 | nominal day ± 2 days |

The planned allowable window is ±3 days, and clean data is generated within it. Windows are **fictional protocol rules**, not universal standards.

## Observations

The generator creates one reading per visit for each configured measurement:

- `SBP` (mmHg) and `HR` (beats/min) in `vital_signs`.
- `HGB` (g/dL), `GLU` (mg/dL), `CREAT` (mg/dL), `SOD` (mmol/L) in `laboratory_results`.

Numbers are drawn from configurable truncated normal distributions with arbitrary simulation bounds. They are **not medically approved reference ranges** and should not be used to judge a real patient's condition. Subsequent rules will focus on data structure, permitted units and deliberate protocol deviations.

## Fictional adverse events

A small percentage of participants get fabricated adverse-event terms and severity labels, with consistent event dates. Event generation is independent of group and is not a model of risk. End dates can be empty to represent an ongoing event; that is intentional, not a default missing-data error.

## Data protection and regulatory boundaries

- No names, contact information, addresses, health identifiers or real medical records are used.
- No statistical comparison of ARM_A and ARM_B can establish a treatment effect.
- `DM`, `VS`, `LB`, `AE`, and related CDISC terms are only useful concepts for later mapping; the present tables are **not SDTM datasets**.
- Do not use this software for diagnosis, trial operation or a regulatory submission.

See machine-readable controls in [`../configs/study_protocol.json`](../configs/study_protocol.json).

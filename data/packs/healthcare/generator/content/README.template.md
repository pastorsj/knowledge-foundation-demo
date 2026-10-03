<!-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
# healthcare pack

Riverside Health Network is a **fictional** regional provider with 3 hospitals and 12 clinics. This pack gives the
NVIDIA Knowledge Foundation demo a healthcare industry: encounter and claims tables for SQL and Kumo predictions, and
clinical and payer documents (PDF, DOCX, PPTX and a scanned form) for cited retrieval.

**All of it is synthetic.** There is no real patient, provider, payer, address or PHI. Patients are ids such as
`P000123` with an age band, no name and no date of birth; providers are ids with a specialty; the payers
(Northstar Advantage, Evergreen Health Plan, Cascade Mutual) are invented. ICD-10 codes are real code values used
only as labels. The documents are written for this demo and are not clinical, legal or billing guidance.

## Data card

| | |
|---|---|
| Organization | Riverside Health Network (fictional): Riverside Memorial Hospital (F01), Lakeview Community Hospital (F02), Eastgate Regional Hospital (F03) and clinics F04 to F15 |
| As of | 2026-09-30; encounters run from 2025-04-01 to 2026-09-30 (18 months) |
| Origin | `generator/build.py`, seeded with `[[seed]]`; the same inputs give the same bytes. The seed was picked from a scan so that the committee deck's story holds (Eastgate above target, readmissions improving after protocol v4.2) |
| License | Apache-2.0 for the generator and the generated files (all synthetic) |
| Size | `files/` is under 3 MB (about 2.3 MB); [[ak.rows_total]] table rows in total |

Regenerate everything under `files/` (and this README, whose answer key is computed from the tables) with:

```bash
uv run data/packs/healthcare/generator/build.py
```

### Sources

| Source id | Name | Kind | Files |
|---|---|---|---|
| `clinical` | Encounters & Claims | structured (DuckDB, Kumo) | `files/clinical/*.csv` and `quality_scorecard.xlsx` |
| `guidelines` | Clinical Guidelines & Policies | documents | HF discharge protocol (PDF, 4 pages), infection-control SOP (PDF, 3 pages), scanned HF discharge checklist (PNG) |
| `operations` | Payer & Operations | documents | prior-authorization policy (PDF, 3 pages), payer contract summary (DOCX), Q2 2026 quality committee deck (PPTX) |

### Tables (source `clinical`)

| Table | Rows | Key | What it holds |
|---|---|---|---|
| `facilities` | [[ak.rows.facilities]] | `facility_id` | 3 hospitals and 12 clinics |
| `providers` | [[ak.rows.providers]] | `provider_id` | clinicians by specialty and home facility |
| `patients` | [[ak.rows.patients]] | `patient_id` | age band, sex, payer, primary clinic and chronic-condition flags (heart failure, COPD, diabetes, CKD, hypertension) |
| `encounters` | [[ak.rows.encounters]] | `encounter_id` | [[ak.n_inpatient]] inpatient, [[ak.n_ed]] ED and [[ak.n_outpatient]] outpatient encounters with admit and discharge timestamps, length of stay, discharge disposition and visit reason |
| `diagnoses` | [[ak.rows.diagnoses]] | `diagnosis_id` | ICD-10 codes per encounter, one primary (`is_primary = 1`) and up to two secondary |
| `claims` | [[ak.rows.claims]] | `claim_id` | one claim per billed encounter: billed and paid amounts, status, denial flag and reason, appeal status |
| `quality_scorecard_readmissions` | [[ak.rows.quality_scorecard_readmissions]] | `record_id` | monthly 30-day readmission counts and rates by hospital and cohort (sheet `Readmissions`) |
| `quality_scorecard_hcahps` | [[ak.rows.quality_scorecard_hcahps]] | `record_id` | quarterly HCAHPS-style top-box scores by hospital and domain (sheet `HCAHPS`) |

Claims for ED visits that end in an admission are billed on the inpatient claim, so those ED encounters have no claim
of their own. Encounters discharged in the last few days of September 2026 have no claim yet, and claims adjudicated
after 2026-09-30 have status `pending`.

How the data was shaped (so Kumo has signal): 30-day readmission is more likely after heart failure and COPD
admissions, for older age bands (85 and over most of all), after short stays (two days or less) and for frail patients
with many past admissions. Riverside Memorial Hospital has the lowest readmission rate; Lakeview and Eastgate are
higher, and Eastgate is the highest in Q2 2026. A seven-day visit after a heart failure discharge happens for
[[q1_fu_all_rate]] of eligible discharges in Q1 2026 and [[q2_fu_all_rate]] in Q2 2026. Denial rates differ by payer
(Northstar Advantage highest, Medicare lowest, self-pay never denied) and rise for one-day inpatient stays.

### Definitions used by the questions and the answer key

- **Index discharge:** an inpatient encounter whose `discharge_disposition` is not `expired` or `hospice`.
- **30-day readmission:** the same patient has another inpatient encounter whose `admit_ts` is within 30 days of the
  index `discharge_ts`. By construction no gap falls on the 30/31-day boundary, so counting in calendar days or in
  24-hour days gives the same answer.
- **Quarter and month assignment** use the discharge date. Q2 2026 is 2026-04-01 to 2026-06-30.
- **Heart failure stay:** the primary diagnosis (`is_primary = 1`) starts with `I50`; COPD starts with `J44`.
- **Eligible for the 7-day visit** (protocol RHN-CLN-HF-014 section 2): a heart failure index discharge to `home` or
  `home_health`. A **completed visit** is an `outpatient` encounter of the same patient 0 to 7 calendar days after the
  discharge date; by construction no visit falls on day 7 or 8, so the reading of "within 7 days" does not matter.
- **Appeal window** (payer contract summary RHN-CON-009): days from the denial notice (`claims.adjudicated_date`);
  a claim is open while 2026-09-30 is on or before `adjudicated_date` plus the window. By construction no deadline
  falls exactly on 2026-09-30.

## Questions

`questions.yaml` has ten questions: 3 documents, 3 SQL, 2 hybrid and 2 prediction; six are featured and come first in
`examples`.

| Id | Tag | Featured | Uses |
|---|---|---|---|
| `scanned-checklist` | DOCUMENTS | yes | the scanned PNG (Nemotron Parse) |
| `prior-auth-mri` | DOCUMENTS | yes | the table in the prior-authorization policy PDF |
| `hf-followup-schedule` | DOCUMENTS | | the follow-up schedule table in the HF protocol PDF |
| `readmissions-by-hospital` | SQL | yes | `encounters` |
| `top-denial-reasons` | SQL | | `claims` |
| `readmission-by-condition` | SQL | | `encounters`, `diagnoses` |
| `missed-hf-followup` | HYBRID | yes | HF protocol (7 days, eligible dispositions) and `encounters`, `diagnoses` |
| `appealable-denials` | HYBRID | yes | payer contract summary (windows) and `claims` |
| `hf-readmission-risk` | PREDICTION | yes | Kumo, template `readmission_30d` |
| `claim-denial-risk` | PREDICTION | | Kumo, template `claim_denial_60d` |

## Answer key

Every number below is computed from the files by `generator/build.py`. Rates are percentages.

### 1. `scanned-checklist`

[[ak.q1]]

### 2. `prior-auth-mri`

Yes. Advanced imaging, which lists MRI and cardiac MRI, requires prior authorization (Prior Authorization Policy
RHN-REV-021, section 3 table). The payer's **standard decision time is 5 business days** and the **expedited decision
time is 24 hours**. The request must be submitted at least 5 business days before the scan.

### 3. `hf-followup-schedule`

From the Heart Failure Discharge and Transitional Care Protocol RHN-CLN-HF-014 v4.2, section 5:

- within 48 hours: Care Transitions nurse phone call (up to 3 attempts);
- within 7 calendar days: follow-up visit at an HF clinic (Pine Valley Heart Failure Clinic or Oakridge Cardiology
  Clinic), the primary care clinic, or by telehealth; a phone call does not count;
- within 14 days: basic metabolic panel (potassium, creatinine) after a diuretic, ARNI, ACE inhibitor or MRA dose change;
- within 30 days: cardiology or HF clinic review of volume status and therapy titration;
- at 90 days: functional status, adherence and need for a repeat echocardiogram.

### 4. `readmissions-by-hospital`

30-day readmission rate for Q2 2026 discharges (deaths and hospice excluded):

[[ak.q4]]

These agree with the Q2 2026 quality committee deck (slide 3) and with the sum of the `Readmissions` sheet's `All-cause`
rows for April to June 2026. A reference query:

```sql
WITH ip AS (
  SELECT encounter_id, patient_id, facility_id, discharge_ts, discharge_disposition,
         LEAD(admit_ts) OVER (PARTITION BY patient_id ORDER BY admit_ts) AS next_admit_ts
  FROM encounters WHERE encounter_type = 'inpatient')
SELECT facility_id, COUNT(*) AS index_discharges,
       SUM(CASE WHEN next_admit_ts <= discharge_ts + INTERVAL 30 DAY THEN 1 ELSE 0 END) AS readmissions
FROM ip
WHERE discharge_disposition NOT IN ('expired', 'hospice')
  AND CAST(discharge_ts AS DATE) BETWEEN DATE '2026-04-01' AND DATE '2026-06-30'
GROUP BY facility_id ORDER BY facility_id;
```

### 5. `top-denial-reasons`

Claims whose denial notice is dated in 2026 (`denied = 1` and `adjudicated_date` from 2026-01-01 to 2026-09-30; the
submission date does not matter): [[ak.q5_n]] claims with [[ak.q5_total]] billed. The three
reasons with the most billed dollars:

[[ak.q5]]

Together they are [[ak.q5_top3_sum]], which is **[[ak.q5_share]]** of all denied billed dollars.

### 6. `readmission-by-condition`

30-day readmission rate for index discharges from 2025-04-01 to 2026-08-31 (the last month with a full 30-day
follow-up; deaths and hospice excluded), by the stay's **primary** diagnosis (`diagnoses.is_primary = 1`): heart
failure is a primary I50.x code, COPD a primary J44.x code, and every other discharge is "other". A secondary
I50.x code does not make a stay a heart failure discharge.

[[ak.q6]]

Heart failure and COPD discharges are readmitted roughly twice as often as all other discharges.

### 7. `missed-hf-followup`

The protocol (section 2 and section 5.1) requires a completed visit within 7 days for heart failure patients
discharged **home or home health**; other destinations follow section 6 and are not counted. In August 2026 there were
[[ak.q7_all_n]] heart failure discharges (primary diagnosis I50.x). [[ak.q7_eligible]] were to
home or home health, [[ak.q7_completed]] of them had a visit within 7 days and **[[ak.q7_missed_n]] did not** ([[ak.q7_missed_patients]] distinct
patients; a patient discharged twice in the month appears twice):

[[ak.q7]]

Not counted, because the destination is not covered by the 7-day measure: [[ak.q7_other_dispositions]].
Patient [[case_patient]] on the scanned checklist (question 1) is in this list. Reference logic: heart failure index stays
discharged in August 2026 to `home` or `home_health` with no `outpatient` encounter of the same patient between the
discharge date and 7 days later.

### 8. `appealable-denials`

Appeal windows from the payer contract summary (RHN-CON-009, section 3), in days: Medicare [[appeal.Medicare]],
Northstar Advantage [[appeal.Northstar Advantage]], Medicaid [[appeal.Medicaid]],
Evergreen Health Plan [[appeal.Evergreen Health Plan]], Cascade Mutual [[appeal.Cascade Mutual]]. Of the [[ak.q8_none_n]] denied claims with
`appeal_status = 'none'`, [[ak.q8_closed_n]] are past their window. **[[ak.q8_n]] claims are still open on 2026-09-30,
with [[ak.q8_total]] billed**:

[[ak.q8]]

The first deadline to close is claim [[ak.q8_next]].

### 9. `hf-readmission-risk` (Kumo)

A Kumo answer varies with the model, so the key states what a correct run looks like, not an exact ranking. Kumo scores
at most 1,000 entities per request (after the entity filter) and returns the top 25, so the question names a
subpopulation and the run must filter: the `readmission_30d` template with
`FOR EACH patients.patient_id WHERE patients.has_heart_failure = 1` (the full PQL is
`PREDICT COUNT(encounters.* WHERE encounters.encounter_type = 'inpatient', 0, 30, days) > 0 FOR EACH patients.patient_id
WHERE patients.has_heart_failure = 1`), with `anchor_time` 2026-10-01T00:00:00Z. The population is
[[ak.q9_hf_n]] heart failure patients (all 1,400 patients would be too many). The run returns the top 25 `patient_id`s
with probabilities. Reference rates from the tables:

- Averaged over [[ak.q9_windows]] consecutive 30-day windows from 2025-04-01, [[ak.q9_hf]] of the heart failure
  patients were admitted in a window, against [[ak.q9_all]] of all patients.
- After an index discharge, the readmission rate within 30 days is [[ak.q9_hf_readmit]] for heart failure and
  [[ak.q9_all_readmit]] for all discharges (question 6), so recently discharged heart failure patients are the highest risk.
- The top 25 should include several of the [[ak.q9_sep_hf_n]] heart failure patients discharged in September 2026
  ([[ak.q9_sep_hf_ids]]) and be weighted to older patients (75 and over) after a short stay with several earlier
  admissions. Overlap with that list and those characteristics is the check; the exact order is not.

### 10. `claim-denial-risk` (Kumo)

Same limits: the question names Northstar Advantage patients ([[ak.q10_payer_n]] of the 1,400 patients, the payer with
the highest denial rate), so the run must filter. It must use the `claim_denial_60d` template with
`FOR EACH patients.patient_id WHERE patients.payer = 'Northstar Advantage'` (the full PQL is
`PREDICT COUNT(claims.* WHERE claims.denied = 1, 0, 60, days) > 0 FOR EACH patients.patient_id WHERE patients.payer =
'Northstar Advantage'`), with `anchor_time` 2026-10-01T00:00:00Z, and return the top 25 `patient_id`s with
probabilities. Reference rates from the claims table, averaged over [[ak.q10_windows]] consecutive 60-day windows of
claim submission dates from [[ak.q10_first]] to 2026-07-30 (outcomes all known): [[ak.q10_payer_rate]] of Northstar
Advantage patients had a claim denied in a window, against [[ak.q10_all]] of all patients, and by payer:

[[ak.q10]]

Denials also repeat for the same patient: of the [[ak.q10_prior_n]] Northstar Advantage patients with a denied claim
submitted on or before 2026-05-31, [[ak.q10_prior_rate]] had another denied claim submitted from 2026-06-01 to
2026-07-30, against [[ak.q10_never_rate]] of Northstar Advantage patients never denied before. The top 25 should
include several of the [[ak.q10_recent_n]] Northstar Advantage patients with a denied claim submitted since 2026-07-01
([[ak.q10_recent_ids]]), and favor patients denied before with many recent claims. Overlap with that list is the check;
the exact order is not.

## Other facts the documents and tables share

- The Q2 2026 deck (`quality_committee_q2_2026.pptx`) reports all-cause readmission of [[q2_all_rate]] ([[q2_all_r]] of
  [[q2_all_n]]) against [[q1_all_rate]] in Q1; heart failure [[q2_hf_rate]] against [[q1_hf_rate]]; 7-day follow-up
  completion [[q2_fu_all_rate]] against the [[target.hf_followup]] target. Every figure is computed from the tables.
- The deck's decisions name [[q2_worst_name]] (highest Q2 readmission rate, [[q2_worst_rate]]) for a reduction plan.
- The scanned form `hf_discharge_checklist_scan.png` is grayscale, rotated by about 1.3 degrees and noisy, so
  Nemotron Parse's reading of a form is visible: typed fields, check marks and handwritten notes.

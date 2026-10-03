<!-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
% title: Q3 2026 Plant Performance Review
% subtitle: Atlas Precision Components, Dayton, Monterrey and Brno
% meta: 1 July to 30 September 2026. Prepared by Operations Excellence, October 2026.

# Q3 2026 Plant Performance Review
subtitle: Atlas Precision Components | Dayton, Monterrey, Brno
- Reporting period: 1 July to 30 September 2026
- Prepared by Operations Excellence for the Executive Operations Committee
- Synthetic data for a software demonstration; all companies, plants and people are fictional
notes: Title slide.

---

# Executive summary
- Plan attainment was {q3_attain_total} percent across the three plants: {q3_units_total} good units against a target of {q3_target_total} (12 weeks, 6 July to 27 September).
- Scrap rate was {q3_scrap_total} percent of units started on work orders completed in the quarter. Monterrey was above its {monterrey_scrap_target} percent target at {monterrey_scrap} percent; Dayton and Brno met theirs.
- {q3_ue_total} unplanned failures cost {q3_dt_total} hours of downtime. Dayton ({dayton_dt} h) and Monterrey ({monterrey_dt} h) missed their downtime targets; Brno ({brno_dt} h) met its target.
- Spindle bearing wear (SPN-BRG) caused {q3_spn_share} percent of unplanned downtime hours.
- Quality escape: 8D-2026-014, bore oversize on valve body AP-3312 from CNC lathe MTY-LTH-04 at Monterrey, ${eight_d_cost} of nonconforming parts; closed 18 September.
notes: Figures in this deck are computed from the plant operations tables as of 30 September 2026.

---

# Plant scorecard
!table:scorecard
- Plan attainment is the sum of actual units divided by the sum of target units in the weekly output plan, for the 12 weeks starting 6 July to 21 September 2026.
- Scrap rate is scrapped units divided by units started, for work orders completed from 1 July to 30 September 2026.
- Unplanned downtime is the sum of downtime hours of unplanned maintenance events started in the quarter.
notes: Targets come from the production plan workbook, scrap_targets sheet.

---

# Unplanned downtime by plant and month
!chart:downtime
- Dayton had the most unplanned downtime in the quarter: {dayton_ue} events, {dayton_dt} hours.
- Monterrey's longest outage of the quarter was {monterrey_longest}.
- Brno stayed under its target of {brno_dt_target} hours.
notes: Monthly unplanned downtime hours, summed by plant.

---

# Quality: where the cost came from
!table:defect_cost
- {q3_def_records} defect records were logged in Q3 with a total cost of ${q3_def_cost}.
- Monterrey carried ${monterrey_def_cost} of that cost, of which ${eight_d_cost} was the bore-diameter defects on AP-3312 (8D-2026-014).
- Root cause: spindle bearing wear on MTY-LTH-04 with PM-2 running late; corrective actions closed on 18 September.
notes: Defect cost is scrap value plus rework cost; use-as-is and return-to-supplier records carry no cost.

---

# Reliability: top failure codes in Q3
!table:failure_top
- The top two codes, {top_code_1} and {top_code_2}, account for {top2_share} percent of unplanned downtime hours.
- {q3_pm2_total} PM-2 services were completed in the quarter ({dayton_pm2} at Dayton, {monterrey_pm2} at Monterrey, {brno_pm2} at Brno).
notes: Failure codes are defined in PM-MAN-001, Table 3.

---

# Supplier quality
- {q3_mat_records} material_flaw defects were logged in Q3, {q3_mat_qty} pieces; {q3_mat_rts} records were returned to the supplier.
- The Halvorsen Alloys agreement (SQA-HAL-2026) targets 500 PPM and a lot rejection rate of at most 1.0 percent.
- Supplier recovery plans are required if PPM is above twice the target in any quarter.
notes: The supplier quality agreement is version 2.1, effective 1 January 2026.

---

# Priorities for Q4
- Keep every PM-2 inside the interval in PM-MAN-001, Table 1; the CMMS blocks new work orders on machines more than 14 days past due.
- Route vibration and temperature warnings to named owners at every plant.
- Review gauge plans for valve body parts after 8D-2026-014.
- Close the Dayton downtime gap: its largest cause in Q3 was {dayton_top_code_desc} ({dayton_top_code}, {dayton_top_hours} hours).
notes: Owners and dates are tracked in the Q4 operations plan.

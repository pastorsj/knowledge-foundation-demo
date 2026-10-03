<!-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
% doc_id: 8D-2026-014
% title: 8D Report: Bore Diameter Oversize on Valve Body AP-3312
% subtitle: Monterrey Plant, CNC lathe MTY-LTH-04, customer Brightline Hydraulics
% revision: Final
% effective: Opened 14 August 2026, closed {close_date}
% owner: Quality Engineering, Monterrey Plant
% applies_to: Part AP-3312 (Proportional valve body PV-12)

## D0. Summary

On 14 August 2026 Brightline Hydraulics returned {escape_qty} proportional valve bodies (AP-3312) because the main bore was oversize. Between {cluster_first} and {cluster_last}, {n_inplant} further bore-diameter nonconformances ({inplant_qty} pieces) were recorded in process on the same part, all on CNC lathe **MTY-LTH-04** at the Monterrey Plant. The cost of the nonconforming parts, including the customer return, was ${total_cost}.

The root cause was a worn spindle bearing. The bearing's vibration rose for several weeks while the machine's PM-2 service was {days_overdue_at_failure} days overdue, and the in-process gauge plan was too sparse to catch the drift. The spindle bearing failed on {failure_date} and the spindle cartridge was replaced. No further bore-diameter nonconformances were recorded after the repair.

> This is a synthetic document written for a software demonstration. Atlas Precision Components, Brightline Hydraulics and every person named here are fictional.

## D1. Team

| Role | Name | Function |
|---|---|---|
| Champion | Elena Marsh | Plant Manager, Monterrey |
| Team leader | Rafael Ocampo | Quality Engineering Manager, Monterrey |
| Member | Dario Quintero | Maintenance Supervisor, Monterrey |
| Member | Ines Valdivia | CNC Programmer, Monterrey |
| Member | Tomas Reyes | Process Engineer, Monterrey |
| Customer contact | Helen Park | Supplier Quality Engineer, Brightline Hydraulics |

## D2. Problem description

!table:eight_d_problem

*Table 1. Problem statement (5W2H).*

The in-plant nonconformances were all recorded as defect type dimension_oob against part AP-3312 on lathe MTY-LTH-04, under work orders {wo_list}. Of the {inplant_qty} pieces affected, {inplant_scrap_qty} were scrapped and {inplant_rework_qty} were reworked.

## D3. Interim containment actions

- 15 August: all AP-3312 stock at Monterrey (finished goods and work in progress) was quarantined and 100 percent checked with an air gauge.
- 15 August: every AP-3312 piece produced on MTY-LTH-04 is gauged at the machine until further notice, instead of one in every 25 pieces.
- 17 August: Brightline Hydraulics sorted the remaining pieces from the affected lot at its own site; no further nonconforming pieces were found.
- 19 August: the operator was told to stop the machine for any bore reading above 22.015 mm and call the maintenance supervisor.

## D4. Root cause analysis

### Evidence from the machine data

Daily vibration, temperature and alarm counts for MTY-LTH-04 show the bearing wearing out. In June and July 2026 the machine averaged {vib_base} mm/s (the other CNC lathes' weekly median was {fleet_med} mm/s). From the week of 3 August vibration rose every week, to {vib_week_peak} mm/s in the week of 17 August, and the maximum temperature rose from {temp_base} to {temp_peak} degrees C. The machine logged {alarms_ramp} alarms between 3 and 21 August. The PM-MAN-001 warning level for CNC lathes ({lathe_warn} mm/s) was first exceeded on three consecutive operating days on {warn_date} and the alarm level ({lathe_alarm} mm/s) on {alarm_date}.

!chart:vibration

*Figure 1. Weekly average vibration, MTY-LTH-04 against the median of the other CNC lathes (mm/s RMS).*

### Five whys

| Why? | Answer |
|---|---|
| Why was the bore oversize? | The spindle ran out of true and the cutting position drifted as the bearing wore. |
| Why did the bearing wear out? | The bearing reached the end of its life while the lubrication and preload checks of PM-2 were not done on time. |
| Why was PM-2 late? | The last PM-2 was on {last_pm2}. The 120-day interval for CNC lathes (PM-MAN-001, Table 1) made it due on {pm2_due}. The planner deferred it twice to protect the Brightline delivery. |
| Why was the drift not caught earlier? | The vibration warning level was exceeded for {warn_days_before} days before the failure but the alert went to a shared mailbox that nobody owned. |
| Why did the nonconforming parts leave the plant? | The gauge plan checked one piece in 25, which cannot detect a drift of a few microns an hour. |

### Root causes

| Type | Root cause |
|---|---|
| Occurrence | Spindle bearing wear on MTY-LTH-04, failure code SPN-BRG. |
| Detection | Gauge frequency of one piece in 25 was insufficient for a drifting process. |
| Systemic | The CMMS allowed work orders to be released on a machine more than 14 days past its PM-2 interval, and vibration alerts had no owner. |

## D5. Permanent corrective actions

| Action | Owner | Completed |
|---|---|---|
| Replace the spindle cartridge on MTY-LTH-04 (unplanned event, failure code SPN-BRG, {failure_hours} hours of downtime). | Dario Quintero | {failure_date} |
| Complete the overdue PM-2 on MTY-LTH-04. | Dario Quintero | {pm2_done_after} |
| Route vibration and temperature warning alerts to the cell technician and the planner by name. | Ines Valdivia | 28 August 2026 |
| Gauge every fifth piece, and every piece for the first 50 after a tool change, on AP-3312. | Tomas Reyes | 31 August 2026 |

## D6. Verification of corrective actions

After the repair the machine's average vibration was {vib_post} mm/s in September, in line with its baseline. {post_defects_phrase} The first-article check after the spindle change and the PM-2 on {pm2_done_after} were both within specification.

## D7. Preventive actions

- The CMMS blocks the release of new work orders on any machine that is more than 14 days beyond its PM-2 interval, in line with the escalation rule in PM-MAN-001, section 7.
- The weekly report to the Plant Manager lists every machine with a vibration or temperature warning that has lasted three operating days.
- The same check was added to the other CNC lathes at Monterrey and to the CNC lathes at Dayton and Brno.
- Gauge plans for all valve body parts were reviewed; AP-3301 and AP-3340 moved to every tenth piece.

## D8. Closure and recognition

The team confirmed on {close_date} that the corrective actions are in place and effective. The customer accepted the report on the same day. The team thanks the Maintenance and Quality staff of the Monterrey Plant who worked through the weekend of 15 August to contain the problem.

| Approval | Name | Date |
|---|---|---|
| Team leader | Rafael Ocampo | {close_date} |
| Plant Manager | Elena Marsh | {close_date} |
| Customer | Helen Park, Brightline Hydraulics | {close_date} |

<!-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
% doc_id: PM-MAN-001
% title: Preventive Maintenance Manual
% subtitle: CNC mills and lathes, hydraulic presses, robot cells and surface grinders
% revision: Rev D
% effective: 1 March 2026
% owner: Corporate Maintenance Engineering
% applies_to: Dayton Plant, Monterrey Plant and Brno Plant

## 1. Purpose and scope

This manual sets the preventive maintenance (PM) program for the production machines of Atlas Precision Components. It defines the maintenance levels, how often each machine type must be serviced, the condition-based triggers that bring a service forward, how overdue services are escalated, and how maintenance work is recorded.

It applies to all {machine_count} machines at the Dayton Plant ({dayton_machines} machines), the Monterrey Plant ({monterrey_machines}) and the Brno Plant ({brno_machines}): CNC mills, CNC lathes, hydraulic presses, robot cells and surface grinders. Auxiliary equipment (compressors, chillers, cranes) is covered by PM-MAN-004.

> This is a synthetic document written for a software demonstration. Atlas Precision Components and everything in it are fictional.

## 2. Responsibilities

- **Plant Maintenance Manager:** owns PM compliance at the plant, approves any deferral and reports overdue machines to the Plant Manager every Monday.
- **Maintenance Planner:** schedules each PM-2 in the CMMS before it falls due and books the machine out of the production plan.
- **Maintenance Technician:** performs PM-2 work to the task list in section 5, applies lockout/tagout (SAF-SOP-007) and records the work.
- **Machine Operator:** performs the PM-1 checks on the shift round sheet (form MNT-F-012) and reports abnormal noise, heat, leaks or alarms at once.
- **Production Supervisor:** releases the machine for service and does not schedule work on a machine that has been locked out.

## 3. Maintenance levels

Atlas uses two scheduled maintenance levels and one corrective level.

| Level | Performed by | What it is | Recorded |
|---|---|---|---|
| PM-1 | Machine operator | Daily or weekly inspection and lubrication on the round sheet | Paper round sheet, kept 12 months |
| PM-2 | Maintenance technician | Full preventive service to the task list for the machine type | CMMS event, event type planned, task code PM-2 |
| Corrective (CM) | Maintenance technician | Repair after a failure or a condition alarm | CMMS event, event type unplanned, task code CM, with a failure code |

PM-1 is not entered in the CMMS. Only PM-2 services and corrective repairs appear in the maintenance event history.

## 4. Maintenance intervals by machine type

Table 1 gives the PM-2 interval for each machine type. The interval is counted in calendar days from the date of the last completed PM-2 on that machine. Weekends and plant holidays count as days.

!table:pm_intervals

*Table 1. PM-1 frequency, PM-2 interval and fluid and geometry checks by machine type.*

Rules for applying Table 1:

1. A PM-2 is **due** when the number of days since the machine's last completed PM-2 reaches the interval in Table 1, and **overdue** when it exceeds that interval.
2. A corrective repair does **not** restart the PM-2 clock, even when the repair replaces a component that PM-2 would have inspected.
3. A PM-2 started on one day and finished on the next is dated by the day it started.
4. For a new machine the clock starts at its commissioning date and runs until its first PM-2 is completed.
5. Planners should book each PM-2 two to nine days before its due date so that a short slip does not make the machine overdue.

## 5. PM-2 task lists

Every PM-2 begins with lockout/tagout under SAF-SOP-007 and ends with a recorded test run. In addition to the tasks below, technicians complete the common checks: tighten electrical terminals, inspect cables and guards, test every E-stop and interlock, clean and replace filters, and record as-found vibration and temperature readings.

### 5.1 CNC mill and CNC lathe

- Sample coolant and check concentration, pH and tramp oil; replace coolant when out of limits.
- Check spindle runout and drawbar force; inspect the spindle taper and the tool clamp.
- Lubricate linear guides and ball screws; measure backlash on each axis.
- Mills: inspect the automatic tool changer arm, timing and pocket alignment; run the ballbar circularity check.
- Lathes: inspect the chuck jaws and cylinder, check turret indexing repeatability and tailstock alignment.

### 5.2 Hydraulic press

- Take a hydraulic oil sample for particle count and water content; replace return filters.
- Inspect the ram seal, cylinder rod and hose fittings for weeping or wear.
- Check accumulator pre-charge and the pressure relief setting against the nameplate.
- Test the two-hand control and light curtain stopping time.
- Check platen parallelism at every second PM-2.

### 5.3 Robot cell

- Check joint reducer backlash and grease condition; sample grease at every PM-2.
- Inspect the dress pack, cable harness and torch or gripper for wear.
- Verify the tool center point (TCP) calibration and the safety fence interlocks.
- Back up the controller program and check the encoder battery.

### 5.4 Surface grinder

- Inspect the wheel for cracks, balance the wheel and dress it to the specified profile.
- Check spindle runout and bearing temperature; inspect the coolant nozzle and filtration.
- Verify table flatness and cross-feed accuracy with a test grind.

## 6. Condition-based triggers

Sensors on every machine record daily vibration (average velocity in mm/s RMS), maximum temperature and alarm counts. A condition trigger can bring a PM-2 forward or start a corrective inspection before the interval in Table 1 is reached. Table 2 gives the levels for each machine type.

!table:condition_thresholds

*Table 2. Vibration and temperature warning and alarm levels by machine type.*

- **Warning level:** if the daily average vibration or the maximum temperature is above the warning level on three consecutive operating days, the planner raises a condition-based inspection work request within 24 hours.
- **Alarm level:** a single day above the alarm level requires the operator to stop the machine and call maintenance. The machine returns to service only after a technician signs the inspection.
- **Alarm counts:** more than five machine alarms in one operating day, on two consecutive days, require a review by the cell technician.

Condition-based inspections do not replace PM-2 and do not restart the PM-2 clock.

## 7. Overdue services and escalation

The CMMS lists every machine whose PM-2 is overdue under rule 1 of section 4. Escalation depends on how many days beyond the Table 1 interval a machine has run:

| Days beyond the interval | Action |
|---|---|
| 1 to 14 days | The planner reschedules within 7 days and tells the Plant Maintenance Manager. |
| 15 to 30 days | The Plant Manager is told. The machine runs only on work orders approved by the Production Supervisor and the Maintenance Manager. |
| More than 30 days | The Plant Manager signs a written risk acceptance every week, or the machine is locked out until the PM-2 is complete. |

Deferral requests are limited to 14 days per PM-2 and must state the production reason.

## 8. Records and failure codes

Each maintenance event is entered in the CMMS with its machine, start time, event type (planned or unplanned), task code, downtime hours, cost and notes. Corrective events carry one failure code from Table 3. Where several apply, use the code of the component that failed first.

!table:failure_codes

*Table 3. Failure codes used on unplanned maintenance events.*

## 9. Revision history

| Revision | Date | Change |
|---|---|---|
| Rev A | 12 January 2021 | First release for the Dayton Plant. |
| Rev B | 18 October 2022 | Added Monterrey Plant; added robot cells. |
| Rev C | 5 June 2024 | Added Brno Plant; added the condition-based triggers in section 6. |
| Rev D | 1 March 2026 | Restated intervals as calendar days; added escalation table (section 7); added failure codes (section 8). |

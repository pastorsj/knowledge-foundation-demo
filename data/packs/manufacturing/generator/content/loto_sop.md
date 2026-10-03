<!-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
% doc_id: SAF-SOP-007
% title: Lockout/Tagout of Machinery Energy Sources
% subtitle: Standard operating procedure for servicing and maintenance
% revision: Rev C
% effective: 15 January 2026
% owner: Corporate Environment, Health and Safety (EHS)
% applies_to: Dayton Plant, Monterrey Plant and Brno Plant

## 1. Purpose

This procedure prevents the unexpected start-up of machinery, or the release of stored energy, while people service or maintain it. It describes how to isolate, lock, tag and verify every energy source before work begins, and how to restore the machine safely.

## 2. Scope

The procedure applies to every employee and contractor who services or maintains CNC mills, CNC lathes, hydraulic presses, robot cells, surface grinders and their auxiliary systems at all Atlas Precision Components plants. It applies to every PM-2 service (PM-MAN-001) and to every corrective repair. It does not apply to normal production operations, minor tool changes or adjustments that are part of normal operation and are made with the machine guards in place.

> This is a synthetic document written for a software demonstration. Atlas Precision Components and everything in it are fictional.

## 3. Definitions

- **Authorized employee:** a person trained and certified under section 9 who applies locks and tags to service a machine.
- **Affected employee:** a person who operates or works near the machine being serviced.
- **Energy source:** electrical, hydraulic, pneumatic, mechanical, gravity, thermal or stored (springs, accumulators, capacitors, suspended loads).
- **Personal lock:** a red padlock issued to one authorized employee with a single key held only by that person.
- **Group lockbox:** a box that holds the keys of a locked-out machine so that a crew can work under one lockout; each member adds a personal lock to the box.
- **Tag:** a danger tag fixed to each lock giving the owner's name, date and department.

## 4. Roles

| Role | Responsibility |
|---|---|
| Authorized employee | Plans the lockout, applies and removes their own locks, verifies zero energy. |
| Production Supervisor | Releases the machine for service and tells the affected employees. |
| Maintenance Supervisor | Assigns authorized employees, keeps the lockout log and checks group lockouts. |
| Plant EHS Manager | Approves lock removal in the exceptional case in section 7, audits the procedure each year. |

## 5. Lockout and tagout procedure

Follow these steps in order. Do not skip a step, even for short jobs.

1. **Prepare.** Review the machine-specific energy sources in Table 1. Identify every energy source and every isolating device. Collect personal locks, tags and a group lockbox if more than one person will work on the machine.
2. **Notify.** Tell the Production Supervisor and every affected employee that the machine will be shut down and locked out, and why.
3. **Shut down.** Stop the machine with its normal stop control. Do not use the E-stop as the normal stop.
4. **Isolate.** Switch off or close every energy isolating device in Table 1: the main electrical disconnect, hydraulic and pneumatic supply valves and any other feed.
5. **Apply locks and tags.** Fit a personal lock and tag to each isolating device. Each authorized employee applies their own lock. For a crew, put the keys of the machine locks in a group lockbox and each member adds a personal lock to the box.
6. **Release stored energy.** Bleed hydraulic accumulators and pneumatic lines, discharge capacitors, lower or block any suspended load. On a hydraulic press, lower the ram to the bed or fit the rated ram safety props before anyone enters the die area.
7. **Verify zero energy.** Return the controls to OFF after each check. Confirm that the machine cannot start by trying to start it from its main control station (the try-out). Check that gauges read zero and test electrical circuits with a meter rated for the voltage.
8. **Perform the work.** Keep the personal lock on until your own work is finished. If the job continues past the end of a shift, the next shift's authorized employee applies a lock before the previous one removes theirs.

## 6. Restoring the machine

1. Remove tools and materials; check that the machine and its guards are fully reassembled.
2. Make sure every person is clear of the machine and tell the affected employees that the machine is about to be re-energized.
3. Each authorized employee removes their own personal lock and tag. The last lock removed from the group lockbox releases the machine locks.
4. Re-energize the machine, run the test cycle listed in the maintenance task list and record the work in the CMMS.
5. Tell the Production Supervisor that the machine is released.

## 7. Removal of a lock by someone else

**Only the person who applied a lock may remove it.** Supervisors, co-workers and contractors may not cut or remove another person's lock.

In the exceptional case that the authorized employee is not on site and the machine must be returned to service, **only the Plant EHS Manager (or a deputy named in writing) may remove the lock**, and only after all of these steps:

1. The Maintenance Supervisor has verified that the employee is not on site and has made at least two documented attempts to reach them by phone.
2. The Plant EHS Manager has confirmed that the machine is safe to re-energize and that no one is working on it.
3. The employee is told that the lock was removed **before they return to work on that machine**.
4. The removal is entered in the lockout log, with the names of the people involved and the reason.

## 8. Machine-specific energy sources

!table:loto_sources

*Table 1. Energy sources and isolating devices by machine type.*

## 9. Training, audits and records

- Authorized employees are trained and certified before their first lockout, and are retrained every 12 months and whenever the procedure or the machine changes.
- Affected employees are trained to recognize a lock and tag and never to start or operate a locked-out machine.
- The Plant EHS Manager audits this procedure for each machine type once a year. The audit is carried out by an authorized employee who is not using the procedure in the audit, and findings are closed within 30 days.
- The lockout log is kept for three years. Personal locks are red and identify their owner; no lock may be used for any other purpose.

## 10. Revision history

| Revision | Date | Change |
|---|---|---|
| Rev A | 3 March 2021 | First release. |
| Rev B | 20 September 2023 | Added group lockbox and shift change rules. |
| Rev C | 15 January 2026 | Added robot cells; clarified lock removal by the Plant EHS Manager (section 7); added annual audit independence. |

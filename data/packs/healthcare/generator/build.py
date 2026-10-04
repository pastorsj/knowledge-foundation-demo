#!/usr/bin/env -S uv run --script
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "numpy",
#   "pandas",
#   "openpyxl",
#   "reportlab",
#   "python-docx",
#   "python-pptx",
#   "pillow",
#   "pypdfium2",
#   "pyyaml",
# ]
# ///
"""Build the healthcare pack: Riverside Health Network, a fictional regional provider.

    uv run data/packs/healthcare/generator/build.py [--out DIR] [--stats]

Everything is synthetic and seeded, so the same inputs always give the same bytes. The script writes

    files/clinical/*.csv, quality_scorecard.xlsx     tables (facilities, providers, patients, encounters, ...)
    files/guidelines/*.pdf, *.png                    HF discharge protocol, infection-control SOP, scanned checklist
    files/operations/*.pdf, *.docx, *.pptx           prior-authorization policy, payer contracts, quality deck
    README.md                                        data card, with an answer key computed from the tables

Prose lives in generator/content/ (Markdown and YAML). Every figure the documents quote about the tables (the
readmission rates in the quality deck, for one) is computed from the generated data, never typed in.
"""

from __future__ import annotations

import argparse
import math
import re
import zipfile
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PACK_DIR = Path(__file__).resolve().parent.parent
CONTENT_DIR = Path(__file__).resolve().parent / "content"

SEED = 1684
AS_OF = date(2026, 9, 30)
START = date(2025, 4, 1)
N_DAYS = (AS_OF - START).days + 1
LAST_ADMIT = date(2026, 9, 26)
N_PATIENTS = 1400
PROTOCOL_EFFECTIVE = date(2026, 1, 15)  # HF discharge protocol v4.2

# --------------------------------------------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------------------------------------------

FACILITIES = [
    # id, name, type, focus, city, region, beds
    ("F01", "Riverside Memorial Hospital", "hospital", "Tertiary care, cardiac surgery", "Riverton", "Central", 412),
    ("F02", "Lakeview Community Hospital", "hospital", "Community acute care", "Lakeview", "North", 248),
    ("F03", "Eastgate Regional Hospital", "hospital", "Regional acute care, emergency", "Eastgate", "East", 186),
    ("F04", "Riverton Primary Care Clinic", "clinic", "Primary care", "Riverton", "Central", None),
    ("F05", "Northgate Family Medicine", "clinic", "Primary care", "Lakeview", "North", None),
    ("F06", "Southpark Internal Medicine", "clinic", "Primary care", "Riverton", "Central", None),
    ("F07", "Elm Street Primary Care", "clinic", "Primary care", "Eastgate", "East", None),
    ("F08", "Harbor View Family Clinic", "clinic", "Primary care", "Harbor View", "East", None),
    ("F09", "Millbrook Primary Care", "clinic", "Primary care", "Millbrook", "West", None),
    ("F10", "Pine Valley Heart Failure Clinic", "clinic", "Cardiology, heart failure", "Riverton", "Central", None),
    ("F11", "Oakridge Cardiology Clinic", "clinic", "Cardiology", "Lakeview", "North", None),
    ("F12", "Summit Pulmonary & Sleep Clinic", "clinic", "Pulmonology", "Eastgate", "East", None),
    ("F13", "Westfield Diabetes & Endocrine Clinic", "clinic", "Endocrinology", "Millbrook", "West", None),
    ("F14", "Cedar Heights Primary Care", "clinic", "Primary care", "Cedar Heights", "West", None),
    ("F15", "Brookside Urgent Care", "clinic", "Urgent care", "Brookside", "North", None),
]
HOSPITALS = ["F01", "F02", "F03"]
CLINICS = [f[0] for f in FACILITIES if f[2] == "clinic"]
HF_CLINICS = ["F10", "F11"]
FACILITY_NAME = {f[0]: f[1] for f in FACILITIES}

AGE_BANDS = ["18-34", "35-49", "50-64", "65-74", "75-84", "85+"]
AGE_P = [0.12, 0.16, 0.24, 0.22, 0.17, 0.09]
PREV = {
    "heart_failure": [0.01, 0.05, 0.12, 0.20, 0.30, 0.40],
    "copd": [0.02, 0.04, 0.10, 0.15, 0.17, 0.16],
    "diabetes": [0.05, 0.10, 0.20, 0.27, 0.27, 0.22],
    "ckd": [0.01, 0.02, 0.06, 0.12, 0.20, 0.28],
    "hypertension": [0.10, 0.25, 0.45, 0.60, 0.70, 0.70],
}
PAYERS = ["Medicare", "Northstar Advantage", "Medicaid", "Evergreen Health Plan", "Cascade Mutual", "Self-pay"]
PAYER_P_OLD = [0.55, 0.30, 0.05, 0.05, 0.03, 0.02]  # age 65+
PAYER_P_YOUNG = [0.05, 0.00, 0.25, 0.33, 0.27, 0.10]  # under 65
PAYER_DENIAL = {
    "Medicare": 0.045,
    "Northstar Advantage": 0.12,
    "Medicaid": 0.10,
    "Evergreen Health Plan": 0.075,
    "Cascade Mutual": 0.13,
    "Self-pay": 0.0,
}
PAYER_ALLOWED = {
    "Medicare": 0.36,
    "Northstar Advantage": 0.39,
    "Medicaid": 0.31,
    "Evergreen Health Plan": 0.56,
    "Cascade Mutual": 0.54,
    "Self-pay": 0.14,
}
# First-level appeal window in days from the denial notice, as in content/payer_contract_summary.md.
APPEAL_WINDOW_DAYS = {
    "Medicare": 120,
    "Northstar Advantage": 60,
    "Medicaid": 90,
    "Evergreen Health Plan": 180,
    "Cascade Mutual": 60,
}

DX = {
    "I50.9": "Heart failure, unspecified",
    "I50.22": "Chronic systolic (congestive) heart failure",
    "I50.32": "Chronic diastolic (congestive) heart failure",
    "I50.23": "Acute on chronic systolic (congestive) heart failure",
    "J44.1": "Chronic obstructive pulmonary disease with (acute) exacerbation",
    "J44.9": "Chronic obstructive pulmonary disease, unspecified",
    "E11.9": "Type 2 diabetes mellitus without complications",
    "E11.65": "Type 2 diabetes mellitus with hyperglycemia",
    "I10": "Essential (primary) hypertension",
    "N18.9": "Chronic kidney disease, unspecified",
    "J18.9": "Pneumonia, unspecified organism",
    "N39.0": "Urinary tract infection, site not specified",
    "A41.9": "Sepsis, unspecified organism",
    "I21.4": "Non-ST elevation (NSTEMI) myocardial infarction",
    "I48.91": "Unspecified atrial fibrillation",
    "K92.2": "Gastrointestinal hemorrhage, unspecified",
    "N17.9": "Acute kidney failure, unspecified",
    "R55": "Syncope and collapse",
    "R07.9": "Chest pain, unspecified",
    "R10.9": "Unspecified abdominal pain",
    "J06.9": "Acute upper respiratory infection, unspecified",
    "M54.50": "Low back pain, unspecified",
    "T14.90XA": "Injury, unspecified, initial encounter",
    "J45.909": "Unspecified asthma, uncomplicated",
    "Z00.00": "Encounter for general adult medical examination without abnormal findings",
    "Z23": "Encounter for immunization",
    "E78.5": "Hyperlipidemia, unspecified",
    "K21.9": "Gastro-esophageal reflux disease without esophagitis",
}
HF_CODES = ["I50.9", "I50.22", "I50.32", "I50.23"]
HF_CODE_P = [0.40, 0.15, 0.15, 0.30]

# Inpatient diagnosis families: key -> (codes, mean length of stay in days)
IP_FAMILIES = {
    "hf": (HF_CODES, 5.0),
    "copd": (["J44.1"], 4.2),
    "dm": (["E11.65"], 3.0),
    "pna": (["J18.9"], 4.8),
    "uti": (["N39.0"], 3.2),
    "sepsis": (["A41.9"], 7.0),
    "nstemi": (["I21.4"], 3.6),
    "afib": (["I48.91"], 2.4),
    "gib": (["K92.2"], 3.8),
    "aki": (["N17.9"], 4.4),
    "syncope": (["R55"], 2.2),
    "chest": (["R07.9"], 1.8),
}
IP_FAMILY_KEYS = list(IP_FAMILIES)

DISPOSITIONS_IP = ["home", "home_health", "snf", "rehab", "hospice", "expired", "ama"]

DENIAL_REASONS = {
    "inpatient": [
        ("Medical necessity", 0.30),
        ("Prior authorization missing", 0.20),
        ("Incomplete documentation", 0.15),
        ("Coding error", 0.15),
        ("Eligibility or coverage lapse", 0.10),
        ("Duplicate claim", 0.05),
        ("Timely filing", 0.05),
    ],
    "ED": [
        ("Eligibility or coverage lapse", 0.30),
        ("Medical necessity", 0.25),
        ("Coding error", 0.20),
        ("Incomplete documentation", 0.15),
        ("Duplicate claim", 0.10),
    ],
    "outpatient": [
        ("Prior authorization missing", 0.35),
        ("Eligibility or coverage lapse", 0.20),
        ("Coding error", 0.15),
        ("Duplicate claim", 0.10),
        ("Timely filing", 0.10),
        ("Incomplete documentation", 0.10),
    ],
}

HOSPITAL_READMIT_EFFECT = {"F01": -0.20, "F02": 0.0, "F03": 0.50}
HOSPITAL_FOLLOWUP_EFFECT = {"F01": 0.03, "F02": 0.0, "F03": -0.15}


def d2s(d: date | datetime) -> str:
    return d.strftime("%Y-%m-%d")


def ts2s(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S")


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


# --------------------------------------------------------------------------------------------------------------
# Facilities, providers, patients
# --------------------------------------------------------------------------------------------------------------


def build_facilities() -> pd.DataFrame:
    return pd.DataFrame(
        FACILITIES,
        columns=["facility_id", "facility_name", "facility_type", "service_focus", "city", "region", "licensed_beds"],
    ).astype({"licensed_beds": "Int64"})


def build_providers(rng: np.random.Generator) -> pd.DataFrame:
    rows: list[tuple] = []
    plan: dict[str, list[tuple[str, int, list[str]]]] = {}
    for h in HOSPITALS:
        plan[h] = [
            ("Hospital Medicine", 7, ["MD", "MD", "DO"]),
            ("Cardiology", 3, ["MD"]),
            ("Pulmonology", 2, ["MD"]),
            ("Emergency Medicine", 6, ["MD", "DO"]),
        ]
    for c in CLINICS:
        focus = next(f[3] for f in FACILITIES if f[0] == c)
        if focus == "Primary care":
            plan[c] = [
                ("Family Medicine", 2, ["MD", "DO"]),
                ("Internal Medicine", 1, ["MD"]),
                ("Family Medicine", 1, ["NP"]),
            ]
        elif focus.startswith("Cardiology"):
            plan[c] = [("Cardiology", 3, ["MD"]), ("Cardiology", 1, ["NP"])]
        elif focus == "Pulmonology":
            plan[c] = [("Pulmonology", 2, ["MD"]), ("Pulmonology", 1, ["NP"])]
        elif focus == "Endocrinology":
            plan[c] = [("Endocrinology", 2, ["MD"]), ("Endocrinology", 1, ["NP"])]
        else:
            plan[c] = [("Urgent Care", 2, ["MD", "PA"]), ("Urgent Care", 1, ["NP"])]
    n = 1
    for fid, groups in plan.items():
        for specialty, count, creds in groups:
            for k in range(count):
                rows.append((f"PR{n:04d}", specialty, creds[k % len(creds)], fid, int(rng.integers(2, 32))))
                n += 1
    return pd.DataFrame(
        rows, columns=["provider_id", "specialty", "credential", "primary_facility_id", "years_in_practice"]
    )


def build_patients(rng: np.random.Generator) -> pd.DataFrame:
    n = N_PATIENTS
    age = rng.choice(len(AGE_BANDS), size=n, p=AGE_P)
    sex = np.where(rng.random(n) < 0.52, "female", "male")
    payer = []
    for a in age:
        p = PAYER_P_OLD if a >= 3 else PAYER_P_YOUNG
        payer.append(PAYERS[int(rng.choice(len(PAYERS), p=p))])
    flags = {k: (rng.random(n) < np.array(PREV[k])[age]).astype(int) for k in PREV}
    clinic_w = np.array(
        [1.2, 1.0, 1.0, 1.0, 0.9, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5]
    )  # F10-F13 reached by referral only
    clinic_w = clinic_w / clinic_w.sum()
    clinic = rng.choice(CLINICS, size=n, p=clinic_w)
    # Heart failure patients are often followed at an HF clinic.
    hf_clinic = rng.choice(HF_CLINICS, size=n)
    clinic = np.where((flags["heart_failure"] == 1) & (rng.random(n) < 0.5), hf_clinic, clinic)
    frailty = np.minimum(np.exp(rng.normal(-0.18, 0.6, size=n)), 3.2)
    df = pd.DataFrame(
        {
            "patient_id": [f"P{i + 1:06d}" for i in range(n)],
            "age_band": [AGE_BANDS[a] for a in age],
            "sex": sex,
            "payer": payer,
            "primary_clinic_id": clinic,
            "has_heart_failure": flags["heart_failure"],
            "has_copd": flags["copd"],
            "has_diabetes": flags["diabetes"],
            "has_ckd": flags["ckd"],
            "has_hypertension": flags["hypertension"],
        }
    )
    df.attrs["age_idx"] = age
    df.attrs["frailty"] = frailty
    return df


# --------------------------------------------------------------------------------------------------------------
# Encounter simulation
# --------------------------------------------------------------------------------------------------------------

AGE_IP = np.array([0.4, 0.5, 0.8, 1.2, 1.6, 2.0])
AGE_ED = np.array([0.9, 0.8, 0.8, 0.9, 1.1, 1.3])
AGE_OP = np.array([0.6, 0.8, 1.0, 1.2, 1.2, 1.0])
BASE_IP = 0.29
BASE_ED = 0.27
BASE_OP = 0.90
READMIT_INTERCEPT = -3.85

HOUR_P_ED = np.array(
    [2, 1.5, 1.2, 1, 1, 1, 1.5, 2, 3, 4, 4.5, 5, 5, 5, 5, 5, 5, 5.5, 5.5, 5, 4.5, 4, 3.5, 2.5], dtype=float
)
HOUR_P_ED = HOUR_P_ED / HOUR_P_ED.sum()


def _seasonal_weights() -> np.ndarray:
    doy = np.array([(START + timedelta(days=i)).timetuple().tm_yday for i in range(N_DAYS)])
    w = 1.0 + 0.20 * np.cos(2 * np.pi * (doy - 20) / 365.0)
    return w / w.sum()


class Sim:
    """One run of the encounter simulation; collects events, then numbers them chronologically."""

    def __init__(self, patients: pd.DataFrame, providers: pd.DataFrame, rng: np.random.Generator):
        self.rng = rng
        self.pat = patients
        self.age = patients.attrs["age_idx"]
        self.frailty = patients.attrs["frailty"]
        self.events: list[dict] = []
        self.season_w = _seasonal_weights()
        self.days = [START + timedelta(days=i) for i in range(N_DAYS)]
        self.weekday_idx = np.array([i for i, d in enumerate(self.days) if is_weekday(d)])
        self.prov_by_fac: dict[str, list[tuple[str, str]]] = {}
        for r in providers.itertuples():
            self.prov_by_fac.setdefault(r.primary_facility_id, []).append((r.provider_id, r.specialty))
        self.home_hospital = rng.choice(HOSPITALS, size=len(patients), p=[0.45, 0.30, 0.25])

    # -- helpers ---------------------------------------------------------------------------------------------

    def provider(self, fac: str, specialties: list[str] | None = None) -> str:
        pool = self.prov_by_fac[fac]
        if specialties:
            sub = [p for p in pool if p[1] in specialties]
            pool = sub or pool
        return pool[int(self.rng.integers(len(pool)))][0]

    def choose_hospital(self, i: int) -> str:
        if self.rng.random() < 0.85:
            return str(self.home_hospital[i])
        return str(self.rng.choice(HOSPITALS))

    def family_weights(self, i: int) -> list[float]:
        r = self.pat.iloc[i]
        a = int(self.age[i])
        hf, copd, dm, ckd, htn = r.has_heart_failure, r.has_copd, r.has_diabetes, r.has_ckd, r.has_hypertension
        return [
            0.15 + 12.0 * hf,
            0.20 + 10.0 * copd,
            0.25 + 1.8 * dm,
            0.8 * (1 + 0.2 * a) + 1.2 * copd,
            0.5 * (1 + 0.3 * a),
            0.3 * (1 + 0.3 * a) + 0.4 * ckd,
            0.3 + 0.25 * htn + 0.3 * dm + 0.4 * hf,
            0.35 + 1.0 * hf,
            0.3,
            0.15 + 1.6 * ckd + 0.3 * hf,
            0.25,
            0.4 + 0.3 * hf,
        ]

    def pick_family(self, i: int) -> str:
        w = np.array(self.family_weights(i))
        return IP_FAMILY_KEYS[int(self.rng.choice(len(w), p=w / w.sum()))]

    def pick_code(self, family: str) -> str:
        codes = IP_FAMILIES[family][0]
        if family == "hf":
            return str(self.rng.choice(codes, p=HF_CODE_P))
        return codes[0]

    def secondary_dx(self, i: int, primary: str, cap: int, scale: float) -> list[str]:
        r = self.pat.iloc[i]
        cands = [
            (r.has_heart_failure, "I50.9", 0.85),
            (r.has_copd, "J44.9", 0.80),
            (r.has_diabetes, "E11.9", 0.85),
            (r.has_ckd, "N18.9", 0.85),
            (r.has_hypertension, "I10", 0.80),
        ]
        out: list[str] = []
        for flag, code, p in cands:
            if flag and code[:3] != primary[:3] and self.rng.random() < p * scale:
                out.append(code)
        if self.rng.random() < 0.18 * scale and "E78.5" != primary:
            out.append("E78.5")
        return out[:cap]

    def day_ts(self, day: date, hour: int, minute: int = 0) -> datetime:
        return datetime(day.year, day.month, day.day, hour, minute)

    def add(self, **kw) -> None:
        self.events.append(kw)

    # -- inpatient timeline ----------------------------------------------------------------------------------

    def readmit_probability(self, i: int, family: str, *, los: int, disp: str, fac: str, discharge: date) -> float:
        r = self.pat.iloc[i]
        a = int(self.age[i])
        n_flags = int(r.has_heart_failure + r.has_copd + r.has_diabetes + r.has_ckd + r.has_hypertension)
        logit = READMIT_INTERCEPT
        logit += 1.00 if family == "hf" else 0.0
        logit += 0.95 if family == "copd" else 0.0
        logit += 0.15 if family in ("pna", "sepsis") else 0.0
        logit += [0.0, 0.0, 0.05, 0.20, 0.35, 0.50][a]
        logit += 1.10 if los <= 2 else (0.50 if los == 3 else 0.0)
        logit -= 0.04 * min(max(los - 5, 0), 8)
        logit += 0.28 * math.log(self.frailty[i])
        logit += 0.12 * max(n_flags - 1, 0)
        logit += HOSPITAL_READMIT_EFFECT.get(fac, 0.0)
        logit += {"ama": 0.8, "snf": -0.35, "home_health": 0.10, "rehab": -0.30}.get(disp, 0.0)
        if family == "hf" and discharge >= PROTOCOL_EFFECTIVE:
            logit -= 0.22
        return 1.0 / (1.0 + math.exp(-logit))

    def inpatient_timeline(self, i: int, pid: str) -> tuple[list[dict], date | None]:
        """Simulate one patient's inpatient stays (with readmissions). Returns the stays and the date of death."""
        rng = self.rng
        r = self.pat.iloc[i]
        a = int(self.age[i])
        cond = (
            (1 + 2.4 * r.has_heart_failure)
            * (1 + 1.0 * r.has_copd)
            * (1 + 0.3 * r.has_diabetes)
            * (1 + 0.5 * r.has_ckd)
        )
        lam = BASE_IP * AGE_IP[a] * cond * self.frailty[i]
        n_base = int(rng.poisson(lam))
        queue: list[dict] = []
        for d_idx in rng.choice(N_DAYS, size=n_base, p=self.season_w):
            hour = int(rng.choice(24, p=HOUR_P_ED))
            queue.append(
                {"admit": self.day_ts(self.days[int(d_idx)], hour, int(rng.integers(60))), "depth": 0, "family": None}
            )
        stays: list[dict] = []
        last_discharge: date | None = None
        death: date | None = None
        guard = 0
        while queue and guard < 200:
            guard += 1
            queue.sort(key=lambda e: e["admit"])
            ev = queue.pop(0)
            admit: datetime = ev["admit"]
            if last_discharge is not None:
                gap = (admit.date() - last_discharge).days
                if gap < 1:
                    continue  # still in hospital: the candidate is dropped
                if gap in (30, 31):  # keep every readmission window unambiguous (calendar days vs 24 h days)
                    ev["admit"] = admit + timedelta(days=3)
                    queue.append(ev)
                    continue
            if admit.date() > LAST_ADMIT:
                continue
            family = ev["family"] or self.pick_family(i)
            code = self.pick_code(family)
            mean_los = IP_FAMILIES[family][1] * (1.1 if a >= 4 else 1.0)
            los = 1 + int(rng.poisson(max(mean_los - 1, 0.3)))
            discharge_day = admit.date() + timedelta(days=los)
            if discharge_day > AS_OF:
                continue
            discharge = self.day_ts(discharge_day, int(rng.integers(10, 18)), int(rng.integers(60)))
            fac = self.choose_hospital(i)
            w = np.array(
                [
                    0.78 - 0.07 * a,
                    0.08 + 0.02 * a,
                    0.03 + 0.025 * a,
                    0.03,
                    0.01 + 0.004 * a,
                    (0.012 + 0.004 * a) * (2.0 if family == "sepsis" else 1.0),
                    0.02,
                ]
            )
            disp = DISPOSITIONS_IP[int(rng.choice(len(w), p=w / w.sum()))]
            readmission = ev["depth"] > 0
            source_roll = rng.random()
            if readmission:
                reason = "emergency_admission" if source_roll < 0.70 else "direct_admission"
            else:
                reason = (
                    "emergency_admission"
                    if source_roll < 0.58
                    else "direct_admission"
                    if source_roll < 0.80
                    else "elective_admission"
                )
            spec = (
                ["Cardiology", "Hospital Medicine"]
                if family in ("hf", "nstemi", "afib")
                else (["Pulmonology", "Hospital Medicine"] if family == "copd" else ["Hospital Medicine"])
            )
            stay = {
                "patient_idx": i,
                "type": "inpatient",
                "admit": admit,
                "discharge": discharge,
                "facility": fac,
                "provider": self.provider(fac, spec if rng.random() < 0.75 else ["Hospital Medicine"]),
                "disposition": disp,
                "reason": reason,
                "family": family,
                "depth": ev["depth"],
                "los": los,
                "dx": [(code, 1)] + [(c, 0) for c in self.secondary_dx(i, code, cap=2, scale=1.0)],
            }
            stays.append(stay)
            last_discharge = discharge_day
            if disp == "expired":
                death = discharge_day
                break
            if disp != "hospice" and ev["depth"] < 3:
                p = self.readmit_probability(i, family, los=los, disp=disp, fac=fac, discharge=discharge_day)
                if rng.random() < p:
                    delay = int(min(max(round(2 + rng.gamma(1.6, 5.0)), 2), 28))
                    nxt_day = discharge_day + timedelta(days=delay)
                    same = rng.random() < 0.55
                    queue.append(
                        {
                            "admit": self.day_ts(nxt_day, int(rng.choice(24, p=HOUR_P_ED)), int(rng.integers(60))),
                            "depth": ev["depth"] + 1,
                            "family": family if same else None,
                        }
                    )
        return stays, death

    # -- everything else -------------------------------------------------------------------------------------

    def simulate(self) -> None:
        rng = self.rng
        for i, pid in enumerate(self.pat["patient_id"]):
            r = self.pat.iloc[i]
            a = int(self.age[i])
            stays, death = self.inpatient_timeline(i, pid)
            busy = [(s["admit"].date(), s["discharge"].date()) for s in stays]
            end_day = death or AS_OF

            def free(day: date) -> bool:
                if day > end_day or day < START:
                    return False
                if any(lo <= day <= hi for lo, hi in busy):
                    return False
                # 7- and 8-day calendar gaps after a discharge are kept empty: the 7-day follow-up window is
                # then the same whether it is counted in calendar days or in 24-hour days.
                return not any((day - hi).days in (7, 8) for _, hi in busy)

            n_flags = r.has_heart_failure + r.has_copd + r.has_diabetes + r.has_ckd + r.has_hypertension

            for s in stays:
                self.add(**s)
                # ED visit just before an emergency admission
                if s["reason"] == "emergency_admission":
                    lead = min(int(rng.integers(2, 8)), s["admit"].hour + 6)
                    ed_start = s["admit"] - timedelta(hours=lead)
                    ed_code = s["dx"][0][0]
                    self.add(
                        patient_idx=i,
                        type="ED",
                        admit=ed_start,
                        discharge=s["admit"],
                        facility=s["facility"],
                        provider=self.provider(s["facility"], ["Emergency Medicine"]),
                        disposition="admitted",
                        reason="emergency",
                        los=0,
                        dx=[(ed_code, 1)],
                    )
                # post-discharge follow-up visit and ED return visit
                if s["disposition"] in ("home", "home_health", "snf", "rehab", "ama"):
                    self.follow_up(i, s, free)
                    p_ed = 0.05 + (0.07 if s["family"] == "hf" else 0.0) + (0.05 if s["family"] == "copd" else 0.0)
                    if rng.random() < p_ed:
                        d = s["discharge"].date() + timedelta(days=int(rng.integers(3, 26)))
                        if free(d):
                            self.add_ed(i, d, s["family"], s["facility"], death)

            # baseline ED-only visits
            lam_ed = (
                BASE_ED
                * AGE_ED[a]
                * (1 + 0.7 * r.has_copd + 0.3 * r.has_heart_failure + 0.4 * r.has_diabetes)
                * self.frailty[i]
            )
            for d_idx in rng.choice(N_DAYS, size=int(rng.poisson(lam_ed)), p=self.season_w):
                d = self.days[int(d_idx)]
                if free(d):
                    fam = None
                    if r.has_copd and rng.random() < 0.18:
                        fam = "copd"
                    elif r.has_heart_failure and rng.random() < 0.14:
                        fam = "hf"
                    self.add_ed(i, d, fam, self.choose_hospital(i), death)

            # baseline outpatient visits (weekdays)
            lam_op = (
                BASE_OP
                * AGE_OP[a]
                * (
                    1
                    + 0.8 * r.has_heart_failure
                    + 0.8 * r.has_copd
                    + 0.9 * r.has_diabetes
                    + 0.4 * r.has_ckd
                    + 0.5 * r.has_hypertension
                )
                * math.sqrt(self.frailty[i])
            )
            for d_idx in rng.choice(self.weekday_idx, size=int(rng.poisson(lam_op))):
                d = self.days[int(d_idx)]
                if free(d):
                    self.add_outpatient(i, d, n_flags, str(r.primary_clinic_id))

    def add_ed(self, i: int, d: date, family: str | None, fac: str, death: date | None) -> None:
        rng = self.rng
        hour = int(rng.choice(24, p=HOUR_P_ED))
        start = self.day_ts(d, hour, int(rng.integers(60)))
        end = start + timedelta(minutes=int(rng.integers(90, 520)))
        if family == "hf":
            code = str(rng.choice(["I50.9", "I50.23"], p=[0.5, 0.5]))
        elif family == "copd":
            code = "J44.1"
        else:
            r = self.pat.iloc[i]
            codes = ["R07.9", "R10.9", "J06.9", "N39.0", "M54.50", "T14.90XA", "R55", "J45.909", "E11.65"]
            w = np.array(
                [
                    1.2 + 0.6 * r.has_heart_failure,
                    1.0,
                    1.1,
                    0.6,
                    0.7,
                    1.0,
                    0.4,
                    0.4 + 0.3 * r.has_copd,
                    0.2 * r.has_diabetes + 0.05,
                ]
            )
            code = codes[int(rng.choice(len(codes), p=w / w.sum()))]
        disp = "home" if rng.random() < 0.94 else ("ama" if rng.random() < 0.6 else "transfer")
        dx = [(code, 1)]
        if rng.random() < 0.22:
            sec = self.secondary_dx(i, code, cap=1, scale=0.6)
            dx += [(c, 0) for c in sec]
        self.add(
            patient_idx=i,
            type="ED",
            admit=start,
            discharge=end,
            facility=fac,
            provider=self.provider(fac, ["Emergency Medicine"]),
            disposition=disp,
            reason="emergency",
            los=0,
            dx=dx,
        )

    def add_outpatient(
        self, i: int, d: date, n_flags: int, clinic: str, *, reason: str | None = None, code: str | None = None
    ) -> None:
        rng = self.rng
        r = self.pat.iloc[i]
        fac = clinic if rng.random() < 0.80 else str(rng.choice(CLINICS))
        if rng.random() < 0.08:
            fac = self.choose_hospital(i)
        if reason is None:
            roll = rng.random()
            chronic = n_flags > 0 and roll < 0.55
            reason = "chronic_management" if chronic else ("preventive" if roll < 0.75 else "acute_episode")
        dx: list[tuple[str, int]]
        if code is not None:
            dx = [(code, 1)]
        elif reason == "chronic_management":
            options = []
            if r.has_heart_failure:
                options += [("I50.9", 3.0), ("I50.22", 1.0), ("I50.32", 1.0)]
            if r.has_copd:
                options += [("J44.9", 3.0)]
            if r.has_diabetes:
                options += [("E11.9", 3.0), ("E11.65", 1.0)]
            if r.has_ckd:
                options += [("N18.9", 2.0)]
            if r.has_hypertension:
                options += [("I10", 2.5)]
            w = np.array([o[1] for o in options])
            code = options[int(rng.choice(len(options), p=w / w.sum()))][0]
            dx = [(code, 1)]
            if rng.random() < 0.15:
                dx += [(c, 0) for c in self.secondary_dx(i, code, cap=1, scale=0.5)]
        elif reason == "preventive":
            code = str(rng.choice(["Z00.00", "Z23", "E78.5"], p=[0.6, 0.3, 0.1]))
            dx = [(code, 1)]
        else:
            code = str(
                rng.choice(
                    ["J06.9", "M54.50", "N39.0", "R10.9", "J45.909", "K21.9"], p=[0.3, 0.25, 0.12, 0.13, 0.1, 0.1]
                )
            )
            dx = [(code, 1)]
        spec = None
        if fac in HOSPITALS:
            spec = ["Cardiology", "Pulmonology", "Hospital Medicine"]
        hour = int(rng.integers(8, 17))
        start = self.day_ts(d, hour, int(rng.choice([0, 15, 30, 45])))
        end = start + timedelta(minutes=int(rng.integers(20, 75)))
        self.add(
            patient_idx=i,
            type="outpatient",
            admit=start,
            discharge=end,
            facility=fac,
            provider=self.provider(fac, spec),
            disposition="home",
            reason=reason,
            los=0,
            dx=dx,
        )

    def follow_up(self, i: int, s: dict, free) -> None:
        rng = self.rng
        if s["disposition"] not in ("home", "home_health"):
            return
        fam = s["family"]
        dday = s["discharge"].date()
        if fam == "hf":
            p = (0.68 if dday >= PROTOCOL_EFFECTIVE else 0.58) + HOSPITAL_FOLLOWUP_EFFECT.get(s["facility"], 0.0)
        elif fam == "copd":
            p = 0.55
        else:
            p = 0.40
        r = self.pat.iloc[i]
        n_flags = int(r.has_heart_failure + r.has_copd + r.has_diabetes + r.has_ckd + r.has_hypertension)
        if rng.random() < p:
            offsets = [o for o in (1, 2, 3, 4, 5, 6) if is_weekday(dday + timedelta(days=o))]
            w = np.array([{1: 0.3, 2: 1.0, 3: 1.4, 4: 1.4, 5: 1.2, 6: 0.8}[o] for o in offsets])
            off = int(rng.choice(offsets, p=w / w.sum()))
        elif rng.random() < 0.45:
            offsets = [o for o in range(9, 22) if is_weekday(dday + timedelta(days=o))]
            off = int(rng.choice(offsets))
        else:
            return
        d = dday + timedelta(days=off)
        if d > AS_OF or not free(d):
            return
        clinic = str(r.primary_clinic_id)
        if fam == "hf" and rng.random() < 0.45:
            clinic = str(rng.choice(HF_CLINICS))
        code = s["dx"][0][0]
        if fam == "hf":
            code = str(rng.choice(["I50.9", "I50.22", "I50.32"], p=[0.5, 0.25, 0.25]))
        elif fam == "copd":
            code = "J44.9"
        self.add_outpatient(i, d, n_flags, clinic, reason="post_discharge_followup", code=code)
        # forced facility: follow-ups happen at the clinic
        self.events[-1]["facility"] = clinic
        self.events[-1]["provider"] = self.provider(clinic)


def build_clinical(seed: int = SEED) -> dict[str, pd.DataFrame]:
    fac = build_facilities()
    prov = build_providers(np.random.default_rng([seed, 1]))
    pat = build_patients(np.random.default_rng([seed, 2]))
    sim = Sim(pat, prov, np.random.default_rng([seed, 3]))
    sim.simulate()
    pids = pat["patient_id"].tolist()

    ev = sorted(sim.events, key=lambda e: (e["admit"], e["patient_idx"], e["type"]))
    enc_rows = []
    dx_rows = []
    dx_id = 1
    for n, e in enumerate(ev, start=1):
        eid = f"E{n:07d}"
        e["encounter_id"] = eid
        admit: datetime = e["admit"]
        disch: datetime = e["discharge"]
        los = (disch.date() - admit.date()).days if e["type"] == "inpatient" else 0
        enc_rows.append(
            (
                eid,
                pids[e["patient_idx"]],
                e["facility"],
                e["provider"],
                e["type"],
                e["reason"],
                ts2s(admit),
                ts2s(disch),
                los,
                e["disposition"],
            )
        )
        for code, prim in e["dx"]:
            dx_rows.append((f"D{dx_id:07d}", eid, pids[e["patient_idx"]], code, DX[code], prim, d2s(admit)))
            dx_id += 1
    enc = pd.DataFrame(
        enc_rows,
        columns=[
            "encounter_id",
            "patient_id",
            "facility_id",
            "provider_id",
            "encounter_type",
            "visit_reason",
            "admit_ts",
            "discharge_ts",
            "length_of_stay_days",
            "discharge_disposition",
        ],
    )
    dx = pd.DataFrame(
        dx_rows,
        columns=[
            "diagnosis_id",
            "encounter_id",
            "patient_id",
            "icd10_code",
            "icd10_description",
            "is_primary",
            "diagnosed_date",
        ],
    )
    claims = build_claims(enc, pat, np.random.default_rng([seed, 4]))
    return {"facilities": fac, "providers": prov, "patients": pat, "encounters": enc, "diagnoses": dx, "claims": claims}


# --------------------------------------------------------------------------------------------------------------
# Claims
# --------------------------------------------------------------------------------------------------------------


def build_claims(enc: pd.DataFrame, pat: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    payer_of = dict(zip(pat["patient_id"], pat["payer"], strict=True))
    # A persistent per-patient denial propensity (coverage quirks, history) gives Kumo something to learn.
    propensity = dict(zip(pat["patient_id"], np.exp(rng.normal(-0.32, 0.8, size=len(pat))), strict=True))
    rows = []
    n = 0
    for r in enc.itertuples():
        if r.encounter_type == "ED" and r.discharge_disposition == "admitted":
            continue  # billed on the inpatient claim
        payer = payer_of[r.patient_id]
        disch = datetime.strptime(r.discharge_ts, "%Y-%m-%d %H:%M:%S")
        lag = int(rng.integers(3, 9)) if r.encounter_type == "inpatient" else int(rng.integers(1, 5))
        submitted = disch.date() + timedelta(days=lag)
        if submitted > AS_OF:
            continue  # not billed yet
        n += 1
        if r.encounter_type == "inpatient":
            billed = (9500 + 3800 * r.length_of_stay_days) * rng.lognormal(0, 0.25)
        elif r.encounter_type == "ED":
            billed = 1900 * rng.lognormal(0, 0.35)
        else:
            billed = (480 if r.visit_reason == "post_discharge_followup" else 260) * rng.lognormal(0, 0.5)
        billed = round(float(billed), 2)
        adjudicated = submitted + timedelta(days=int(rng.integers(10, 36)))
        p_den = PAYER_DENIAL[payer]
        if payer != "Self-pay":
            if r.encounter_type == "inpatient" and r.length_of_stay_days <= 1:
                p_den += 0.05
            if r.encounter_type == "outpatient" and r.visit_reason != "preventive":
                p_den += 0.01
        p_den = min(p_den * propensity[r.patient_id], 0.55)
        denied = 0
        reason = None
        status = "paid"
        paid: float | None
        if adjudicated > AS_OF:
            status = "pending"
            paid = None
        elif rng.random() < p_den:
            denied = 1
            status = "denied"
            paid = 0.0
            opts = DENIAL_REASONS[r.encounter_type]
            reason = opts[int(rng.choice(len(opts), p=np.array([o[1] for o in opts])))][0]
            window = APPEAL_WINDOW_DAYS.get(payer)
            if window is not None and (adjudicated + timedelta(days=window)) == AS_OF:
                adjudicated += timedelta(days=1)  # never exactly at the deadline on the as-of date
        else:
            paid = round(billed * PAYER_ALLOWED[payer] * float(rng.uniform(0.92, 1.04)), 2)
        rows.append(
            [
                f"CL{n:07d}",
                r.encounter_id,
                r.patient_id,
                r.facility_id,
                payer,
                d2s(submitted),
                d2s(adjudicated) if status != "pending" else None,
                billed,
                paid,
                status,
                denied,
                reason,
                None,
            ]
        )
    df = pd.DataFrame(
        rows,
        columns=[
            "claim_id",
            "encounter_id",
            "patient_id",
            "facility_id",
            "payer",
            "submitted_date",
            "adjudicated_date",
            "billed_amount",
            "paid_amount",
            "claim_status",
            "denied",
            "denial_reason",
            "appeal_status",
        ],
    )
    # Appeal status depends on the age of the denial notice at the as-of date.
    for idx in df.index[df["denied"] == 1]:
        age = (AS_OF - datetime.strptime(df.at[idx, "adjudicated_date"], "%Y-%m-%d").date()).days
        if age <= 21:
            opts = [("none", 0.55), ("filed", 0.45)]
        elif age <= 90:
            opts = [("none", 0.35), ("filed", 0.25), ("overturned", 0.25), ("upheld", 0.15)]
        else:
            opts = [("none", 0.40), ("overturned", 0.30), ("upheld", 0.30)]
        df.at[idx, "appeal_status"] = opts[int(rng.choice(len(opts), p=np.array([o[1] for o in opts])))][0]
    df.loc[df["denied"] == 0, "appeal_status"] = None
    return df


# --------------------------------------------------------------------------------------------------------------
# Metrics (shared by the scorecard, the documents and the README answer key)
# --------------------------------------------------------------------------------------------------------------


def inpatient_index(enc: pd.DataFrame, dx: pd.DataFrame) -> pd.DataFrame:
    """Inpatient stays with the next admission, the readmission flag and the primary diagnosis."""
    ip = enc[enc["encounter_type"] == "inpatient"].copy()
    ip["admit_dt"] = pd.to_datetime(ip["admit_ts"])
    ip["discharge_dt"] = pd.to_datetime(ip["discharge_ts"])
    ip = ip.sort_values(["patient_id", "admit_dt"])
    ip["next_admit_dt"] = ip.groupby("patient_id")["admit_dt"].shift(-1)
    gap = ip["next_admit_dt"] - ip["discharge_dt"]
    ip["readmitted_30d"] = (gap <= pd.Timedelta(days=30)).astype(int)
    ip["index_eligible"] = ~ip["discharge_disposition"].isin(["expired", "hospice"])
    prim = dx[dx["is_primary"] == 1][["encounter_id", "icd10_code"]].rename(columns={"icd10_code": "primary_code"})
    ip = ip.merge(prim, on="encounter_id", how="left")
    ip["cohort"] = np.where(
        ip["primary_code"].str.startswith("I50"),
        "Heart failure",
        np.where(ip["primary_code"].str.startswith("J44"), "COPD", "Other"),
    )
    ip["discharge_date"] = ip["discharge_dt"].dt.date
    return ip.reset_index(drop=True)


def quarter_bounds(year: int, q: int) -> tuple[date, date]:
    start = date(year, 3 * q - 2, 1)
    nxt = date(year + 1, 1, 1) if q == 4 else date(year, 3 * q + 1, 1)
    return start, nxt - timedelta(days=1)


def readmission_rate(
    ip: pd.DataFrame, start: date, end: date, facility: str | None = None, cohort: str | None = None
) -> tuple[int, int, float]:
    """(index discharges, readmissions within 30 days, rate in percent) for discharges in [start, end]."""
    m = ip["index_eligible"] & (ip["discharge_date"] >= start) & (ip["discharge_date"] <= end)
    if facility:
        m &= ip["facility_id"] == facility
    if cohort:
        m &= ip["cohort"] == cohort
    sub = ip[m]
    n, r = len(sub), int(sub["readmitted_30d"].sum())
    return n, r, (100.0 * r / n if n else float("nan"))


def hf_followup(ip: pd.DataFrame, enc: pd.DataFrame) -> pd.DataFrame:
    """Heart failure stays discharged home or home health, and whether an outpatient visit followed within 7 days."""
    hf = ip[(ip["cohort"] == "Heart failure") & ip["discharge_disposition"].isin(["home", "home_health"])].copy()
    op = enc[enc["encounter_type"] == "outpatient"][["patient_id", "admit_ts"]].copy()
    op["visit_date"] = pd.to_datetime(op["admit_ts"]).dt.date
    m = hf[["encounter_id", "patient_id", "discharge_date"]].merge(op, on="patient_id", how="left")
    gap = (pd.to_datetime(m["visit_date"]) - pd.to_datetime(m["discharge_date"])).dt.days
    ok = m[(gap >= 0) & (gap <= 7)]["encounter_id"].unique()
    hf["followup_7d"] = hf["encounter_id"].isin(ok)
    return hf


def appealable_claims(claims: pd.DataFrame) -> pd.DataFrame:
    den = claims[(claims["denied"] == 1) & (claims["appeal_status"] == "none")].copy()
    den["window_days"] = den["payer"].map(APPEAL_WINDOW_DAYS)
    den["deadline"] = pd.to_datetime(den["adjudicated_date"]) + pd.to_timedelta(den["window_days"], unit="D")
    den["appealable"] = den["deadline"] >= pd.Timestamp(AS_OF)
    return den


def validate_clinical(t: dict[str, pd.DataFrame]) -> None:
    """Keys, foreign keys, timeline sanity and the unambiguous-window guarantees the answer key relies on."""
    fac, prov, pat, enc, dx, claims = (
        t[k] for k in ("facilities", "providers", "patients", "encounters", "diagnoses", "claims")
    )
    for name, df, key in [
        ("facilities", fac, "facility_id"),
        ("providers", prov, "provider_id"),
        ("patients", pat, "patient_id"),
        ("encounters", enc, "encounter_id"),
        ("diagnoses", dx, "diagnosis_id"),
        ("claims", claims, "claim_id"),
    ]:
        assert df[key].is_unique and df[key].notna().all(), f"{name}.{key} is not a primary key"
    fks = [
        (enc, "patient_id", pat, "patient_id"),
        (enc, "facility_id", fac, "facility_id"),
        (enc, "provider_id", prov, "provider_id"),
        (dx, "encounter_id", enc, "encounter_id"),
        (dx, "patient_id", pat, "patient_id"),
        (claims, "encounter_id", enc, "encounter_id"),
        (claims, "patient_id", pat, "patient_id"),
        (claims, "facility_id", fac, "facility_id"),
        (pat, "primary_clinic_id", fac, "facility_id"),
        (prov, "primary_facility_id", fac, "facility_id"),
    ]
    for child, col, parent, pcol in fks:
        assert child[col].isin(parent[pcol]).all(), f"foreign key {col} -> {pcol} broken"
    admit = pd.to_datetime(enc["admit_ts"])
    disch = pd.to_datetime(enc["discharge_ts"])
    assert (disch >= admit).all()
    assert admit.min() >= pd.Timestamp(START) and disch.max() < pd.Timestamp(AS_OF) + pd.Timedelta(days=1)
    # Nothing overlaps an inpatient stay (an ED visit may end exactly when its admission starts).
    allenc = enc.assign(a=admit, d=disch)
    stays = allenc[allenc["encounter_type"] == "inpatient"]
    pairs = allenc.merge(stays[["patient_id", "encounter_id", "a", "d"]], on="patient_id", suffixes=("", "_s"))
    pairs = pairs[pairs["encounter_id"] != pairs["encounter_id_s"]]
    overlap = (pairs["a"] < pairs["d_s"]) & (pairs["d"] > pairs["a_s"])
    assert not overlap.any(), "an encounter overlaps an inpatient stay"
    # Every encounter has exactly one primary diagnosis.
    assert (dx.groupby("encounter_id")["is_primary"].sum() == 1).all()
    # Calendar-day and 24-hour readings of the 30-day and 7-day windows agree.
    ip = enc[enc["encounter_type"] == "inpatient"].assign(a=admit, d=disch).sort_values(["patient_id", "a"])
    nxt = ip.groupby("patient_id")["a"].shift(-1)
    cal = (nxt.dt.normalize() - ip["d"].dt.normalize()).dt.days
    assert not cal.isin([30, 31]).any(), "ambiguous 30-day readmission gap"
    op = enc[enc["encounter_type"] == "outpatient"].assign(vd=admit.dt.normalize())
    m = ip[["patient_id", "d"]].assign(dd=ip["d"].dt.normalize()).merge(op[["patient_id", "vd"]], on="patient_id")
    gap = (m["vd"] - m["dd"]).dt.days
    assert not gap.isin([7, 8]).any(), "ambiguous 7-day follow-up gap"
    assert claims.loc[claims["denied"] == 1, "denial_reason"].notna().all()
    assert claims.loc[claims["denied"] == 1, "adjudicated_date"].notna().all()
    den = claims[claims["denied"] == 1].copy()
    den["win"] = den["payer"].map(APPEAL_WINDOW_DAYS)
    deadline = pd.to_datetime(den["adjudicated_date"]) + pd.to_timedelta(den["win"], unit="D")
    assert not (deadline == pd.Timestamp(AS_OF)).any(), "appeal deadline on the as-of date"


# --------------------------------------------------------------------------------------------------------------
# Quality scorecard workbook
# --------------------------------------------------------------------------------------------------------------

TARGETS = {
    "all_cause": 15.0,
    "hf_readmit": 20.0,
    "copd_readmit": 18.0,
    "hf_followup": 85.0,
    "denial": 8.0,
    "hcahps_overall": 72.0,
}
COHORTS = [
    ("All-cause", None, "ALL", "all_cause"),
    ("Heart failure", "Heart failure", "HF", "hf_readmit"),
    ("COPD", "COPD", "COPD", "copd_readmit"),
]
HCAHPS_DOMAINS = [
    ("Communication with nurses", 80.5),
    ("Communication with doctors", 81.0),
    ("Responsiveness of hospital staff", 68.5),
    ("Communication about medicines", 66.0),
    ("Discharge information", 87.5),
    ("Care transition", 53.0),
    ("Cleanliness and quietness", 62.0),
    ("Overall hospital rating (9-10)", 72.5),
]
HCAHPS_FACILITY_EFFECT = {"F01": 1.5, "F02": 0.0, "F03": -3.0}
HCAHPS_RESPONSES = {"F01": 520, "F02": 330, "F03": 240}
HCAHPS_OVERALL = "Overall hospital rating (9-10)"


def build_scorecards(t: dict[str, pd.DataFrame], seed: int = SEED) -> dict[str, pd.DataFrame]:
    ip = inpatient_index(t["encounters"], t["diagnoses"])
    rows = []
    for m in pd.period_range("2025-04", "2026-09", freq="M"):
        start, end = m.start_time.date(), m.end_time.date()
        complete = int(end + timedelta(days=30) <= AS_OF)
        for fid in HOSPITALS:
            for label, cohort, code, tkey in COHORTS:
                n, r, rate = readmission_rate(ip, start, end, fid, cohort)
                rows.append(
                    [
                        f"RA-{m.strftime('%Y%m')}-{fid}-{code}",
                        start,
                        fid,
                        FACILITY_NAME[fid],
                        label,
                        n,
                        r,
                        round(rate, 1) if n else None,
                        TARGETS[tkey],
                        complete,
                    ]
                )
    readm = pd.DataFrame(
        rows,
        columns=[
            "record_id",
            "month",
            "facility_id",
            "facility_name",
            "cohort",
            "index_discharges",
            "readmissions_30d",
            "readmission_rate_pct",
            "target_rate_pct",
            "follow_up_complete",
        ],
    )
    rng = np.random.default_rng([seed, 5])
    rows = []
    for qi, (year, q) in enumerate([(2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2), (2026, 3)]):
        qstart = quarter_bounds(year, q)[0]
        for fid in HOSPITALS:
            base_n = HCAHPS_RESPONSES[fid] * float(rng.uniform(0.9, 1.1))
            for domain, base in HCAHPS_DOMAINS:
                eff = HCAHPS_FACILITY_EFFECT[fid]
                if domain == "Discharge information" and fid == "F03":
                    eff -= 3.5
                if domain == "Care transition" and fid == "F03":
                    eff -= 2.0
                val = base + eff + 0.35 * qi + float(rng.normal(0, 1.1))
                rows.append(
                    [
                        f"HC-{year}Q{q}-{fid}-{len(rows) % len(HCAHPS_DOMAINS) + 1:02d}",
                        qstart,
                        f"{year}Q{q}",
                        fid,
                        FACILITY_NAME[fid],
                        domain,
                        round(val, 1),
                        int(base_n * float(rng.uniform(0.92, 1.0))),
                    ]
                )
    hc = pd.DataFrame(
        rows,
        columns=[
            "record_id",
            "quarter_start",
            "quarter",
            "facility_id",
            "facility_name",
            "domain",
            "top_box_pct",
            "survey_responses",
        ],
    )
    return {"Readmissions": readm, "HCAHPS": hc}


def _cell(v):
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA:
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    return v


def _normalized_zip_bytes(data: bytes) -> bytes:
    """Fixed entry timestamps and fixed core-property dates, recursing into embedded workbooks (chart data)."""
    import io

    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for zi in zin.infolist():
            blob = zin.read(zi.filename)
            if zi.filename.endswith(".xlsx"):
                blob = _normalized_zip_bytes(blob)
            elif zi.filename.endswith("core.xml"):
                blob = re.sub(
                    rb"(<dcterms:(created|modified)[^>]*>)[^<]*(</dcterms:\2>)",
                    rb"\g<1>2026-09-30T12:00:00Z\g<3>",
                    blob,
                )
            info = zipfile.ZipInfo(zi.filename, date_time=(2026, 9, 30, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zout.writestr(info, blob)
    return out.getvalue()


def normalize_zip(path: Path) -> None:
    """Rewrite a zip-based Office file so repeated builds give the same bytes."""
    path.write_bytes(_normalized_zip_bytes(path.read_bytes()))


FIXED_DT = datetime(2026, 9, 30, 12, 0, 0)


def write_xlsx(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment
    from openpyxl.styles import Font
    from openpyxl.styles import PatternFill

    wb = Workbook()
    wb.remove(wb.active)
    wb.properties.creator = "Riverside Health Network Clinical Quality (synthetic)"
    wb.properties.title = "Quality scorecard (synthetic)"
    wb.properties.created = FIXED_DT
    wb.properties.modified = FIXED_DT
    wb.properties.lastModifiedBy = "build.py"
    for name, df in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(list(df.columns))
        for row in df.itertuples(index=False):
            ws.append([_cell(v) for v in row])
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="0B3C5D")
            c.alignment = Alignment(horizontal="center")
        for j, col in enumerate(df.columns, start=1):
            width = max(len(str(col)), *(len(str(v)) for v in df[col].head(60))) + 2
            ws.column_dimensions[ws.cell(row=1, column=j).column_letter].width = min(width, 40)
            if "date" in str(col) or col in ("month", "quarter_start"):
                for r in range(2, len(df) + 2):
                    ws.cell(row=r, column=j).number_format = "yyyy-mm-dd"
        ws.freeze_panes = "A2"
    wb.save(path)
    normalize_zip(path)


# --------------------------------------------------------------------------------------------------------------
# Figures quoted by the documents (all computed from the tables)
# --------------------------------------------------------------------------------------------------------------


def pct(x: float) -> str:
    return f"{x:.1f}%"


def money(x: float) -> str:
    return f"${x:,.0f}"


def _quarter_claims(claims: pd.DataFrame, q: tuple[date, date]) -> pd.DataFrame:
    adj = claims[claims["adjudicated_date"].notna()].copy()
    d = pd.to_datetime(adj["adjudicated_date"])
    return adj[(d >= pd.Timestamp(q[0])) & (d < pd.Timestamp(q[1]) + pd.Timedelta(days=1))]


def build_context(t: dict[str, pd.DataFrame], sc: dict[str, pd.DataFrame]) -> dict:
    enc, dx, claims = t["encounters"], t["diagnoses"], t["claims"]
    ip = inpatient_index(enc, dx)
    fu = hf_followup(ip, enc)
    ctx: dict = {}
    quarters = {"q1": quarter_bounds(2026, 1), "q2": quarter_bounds(2026, 2)}
    scopes = [("all", None, None), ("hf", None, "Heart failure"), ("copd", None, "COPD")]
    scopes += [(h, h, None) for h in HOSPITALS] + [(f"hf_{h}", h, "Heart failure") for h in HOSPITALS]
    for per, (s, e) in quarters.items():
        for scope, fac, coh in scopes:
            n, r, rate = readmission_rate(ip, s, e, fac, coh)
            ctx[f"{per}_{scope}_n"], ctx[f"{per}_{scope}_r"] = n, r
            ctx[f"{per}_{scope}_rate"], ctx[f"{per}_{scope}_num"] = pct(rate), rate
        sub = fu[(fu["discharge_date"] >= s) & (fu["discharge_date"] <= e)]
        for scope, fac in [("all", None)] + [(h, h) for h in HOSPITALS]:
            x = sub if fac is None else sub[sub["facility_id"] == fac]
            n, ok = len(x), int(x["followup_7d"].sum())
            ctx[f"{per}_fu_{scope}_n"], ctx[f"{per}_fu_{scope}_ok"] = n, ok
            ctx[f"{per}_fu_{scope}_rate"], ctx[f"{per}_fu_{scope}_num"] = pct(100.0 * ok / n), 100.0 * ok / n
        qc = _quarter_claims(claims, (s, e))
        ctx[f"{per}_den_all_total"], ctx[f"{per}_den_all_n"] = len(qc), int(qc["denied"].sum())
        ctx[f"{per}_den_all_rate"] = pct(100.0 * qc["denied"].sum() / len(qc))
        ctx[f"{per}_den_all_num"] = 100.0 * qc["denied"].sum() / len(qc)
        for payer in PAYERS:
            p = qc[qc["payer"] == payer]
            ctx[f"{per}_den_{payer}_total"], ctx[f"{per}_den_{payer}_n"] = len(p), int(p["denied"].sum())
            ctx[f"{per}_den_{payer}_rate"] = pct(100.0 * p["denied"].sum() / len(p))
        reasons = qc[qc["denied"] == 1]["denial_reason"].value_counts()
        reasons = reasons.reset_index().sort_values(["count", "denial_reason"], ascending=[False, True])
        ctx[f"{per}_den_top_reason"], ctx[f"{per}_den_top_n"] = (
            reasons.iloc[0]["denial_reason"],
            int(reasons.iloc[0]["count"]),
        )
        pa = reasons[reasons["denial_reason"] == "Prior authorization missing"]
        ctx[f"{per}_den_pa_n"] = int(pa["count"].iloc[0]) if len(pa) else 0
        hc = sc["HCAHPS"]
        hq = hc[hc["quarter"] == f"2026Q{per[1]}"]
        ov = hq[hq["domain"] == HCAHPS_OVERALL]
        ctx[f"{per}_hc_overall_num"] = float(
            (ov["top_box_pct"] * ov["survey_responses"]).sum() / ov["survey_responses"].sum()
        )
        ctx[f"{per}_hc_overall_rate"] = pct(ctx[f"{per}_hc_overall_num"])
    hosp_rates = {h: ctx[f"q2_{h}_num"] for h in HOSPITALS}
    worst, best = max(hosp_rates, key=hosp_rates.get), min(hosp_rates, key=hosp_rates.get)
    ctx["q2_worst_name"], ctx["q2_worst_rate"] = FACILITY_NAME[worst], pct(hosp_rates[worst])
    ctx["q2_best_name"], ctx["q2_best_rate"] = FACILITY_NAME[best], pct(hosp_rates[best])
    ctx["q2_worst_id"] = worst
    fu_rates = {h: ctx[f"q2_fu_{h}_num"] for h in HOSPITALS}
    low = min(fu_rates, key=fu_rates.get)
    ctx["q2_fu_low_name"], ctx["q2_fu_low_rate"] = FACILITY_NAME[low], pct(fu_rates[low])
    hq = sc["HCAHPS"]
    hq = hq[(hq["quarter"] == "2026Q2") & (hq["domain"] == "Discharge information")].sort_values("top_box_pct")
    ctx["q2_hc_low_name"], ctx["q2_hc_low_val"] = hq.iloc[0]["facility_name"], pct(float(hq.iloc[0]["top_box_pct"]))
    ctx["target.all_cause"] = pct(TARGETS["all_cause"])
    ctx["target.hf_readmit"] = pct(TARGETS["hf_readmit"])
    ctx["target.copd_readmit"] = pct(TARGETS["copd_readmit"])
    ctx["target.hf_followup"] = f"{TARGETS['hf_followup']:.0f}%"
    for payer, days in APPEAL_WINDOW_DAYS.items():
        ctx[f"appeal.{payer}"] = str(days)
    return ctx


def fill(text: str, ctx: dict) -> str:
    def rep(m: re.Match) -> str:
        key = m.group(1)
        if key not in ctx:
            raise KeyError(f"unknown placeholder [[{key}]]")
        return str(ctx[key])

    return re.sub(r"\[\[([^\[\]]+)\]\]", rep, text)


# --------------------------------------------------------------------------------------------------------------
# Markdown subset -> blocks
# --------------------------------------------------------------------------------------------------------------


def parse_doc(text: str) -> tuple[dict[str, str], list[tuple]]:
    meta: dict[str, str] = {}
    text = re.sub(r"\A(?:<!--.*?-->[ \t]*\n)+", "", text, flags=re.DOTALL)  # the SPDX header is not content
    body = text
    if text.startswith("---\n"):
        head, _, body = text[4:].partition("\n---\n")
        for line in head.splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"')
    blocks: list[tuple] = []
    lines = body.splitlines()
    i = 0
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(("p", " ".join(para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush()
            i += 1
        elif line.startswith("# "):
            flush()
            blocks.append(("h1", line[2:].strip()))
            i += 1
        elif line.startswith("## "):
            flush()
            blocks.append(("h2", line[3:].strip()))
            i += 1
        elif line.startswith(">"):
            flush()
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i][1:].strip())
                i += 1
            blocks.append(("note", " ".join(buf)))
        elif line.startswith("- ") or re.match(r"^\d+\. ", line):
            flush()
            ordered = not line.startswith("- ")
            items: list[str] = []
            while i < len(lines) and lines[i].strip():
                cur = lines[i]
                if cur.startswith("- ") or re.match(r"^\d+\. ", cur):
                    items.append(re.sub(r"^(- |\d+\. )", "", cur).strip())
                elif cur.startswith("  "):
                    items[-1] += " " + cur.strip()
                else:
                    break
                i += 1
            blocks.append(("ol" if ordered else "ul", items))
        elif line.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            header, body_rows = rows[0], [r for r in rows[2:]]
            blocks.append(("table", header, body_rows))
        else:
            para.append(line.strip())
            i += 1
    flush()
    return meta, blocks


def col_widths(header: list[str], rows: list[list[str]], total: float, char_w: float = 4.6) -> list[float]:
    """Column widths in points that sum to `total`: proportional to content, never narrower than the longest word."""
    weights, floors = [], []
    for j in range(len(header)):
        cells = [header[j]] + [r[j] for r in rows]
        lens = [len(c) for c in cells]
        w = 0.5 * max(lens) ** 0.85 + 0.5 * (sum(lens) / len(lens))
        weights.append(min(max(w, 7.0), 55.0))
        longest = max(len(word) for c in cells for word in (c.split() or [""]))
        floors.append(longest * char_w + 12)
    if sum(floors) > total:
        floors = [f * total / sum(floors) for f in floors]
    spare = total - sum(floors)
    wsum = sum(weights)
    return [f + spare * w / wsum for f, w in zip(floors, weights, strict=True)]


# --------------------------------------------------------------------------------------------------------------
# PDF (reportlab platypus)
# --------------------------------------------------------------------------------------------------------------

NAVY = "#0B3C5D"
TEAL = "#1B7F8C"


def inline_rl(text: str) -> str:
    import html

    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    return t


def build_pdf(path: Path, meta: dict[str, str], blocks: list[tuple]) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
    from reportlab.platypus import ListFlowable
    from reportlab.platypus import ListItem
    from reportlab.platypus import Paragraph
    from reportlab.platypus import SimpleDocTemplate
    from reportlab.platypus import Spacer
    from reportlab.platypus import Table
    from reportlab.platypus import TableStyle

    navy, teal = colors.HexColor(NAVY), colors.HexColor(TEAL)
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.6, leading=13.4, spaceAfter=6, alignment=TA_LEFT)
    title = ParagraphStyle(
        "title", parent=body, fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=navy, spaceAfter=10
    )
    h1 = ParagraphStyle(
        "h1",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=13.5,
        leading=17,
        textColor=navy,
        spaceBefore=12,
        spaceAfter=5,
        keepWithNext=1,
    )
    h2 = ParagraphStyle(
        "h2",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=teal,
        spaceBefore=8,
        spaceAfter=4,
        keepWithNext=1,
    )
    cell = ParagraphStyle("cell", parent=body, fontSize=8.4, leading=10.6, spaceAfter=0)
    cell_h = ParagraphStyle("cell_h", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)
    meta_k = ParagraphStyle("meta_k", parent=cell, fontName="Helvetica-Bold", textColor=navy)
    left, right = 0.8 * inch, 0.8 * inch
    avail = letter[0] - left - right

    class Chrome(canvas.Canvas):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._pages: list[dict] = []

        def showPage(self):  # noqa: N802 (reportlab API)
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            n = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self.setFont("Helvetica-Bold", 8)
                self.setFillColor(navy)
                self.drawString(left, letter[1] - 0.55 * inch, "RIVERSIDE HEALTH NETWORK")
                self.setFont("Helvetica", 8)
                self.setFillColor(colors.HexColor("#4A5560"))
                self.drawRightString(
                    letter[0] - right, letter[1] - 0.55 * inch, f"{meta['doc_id']}  |  Version {meta['version']}"
                )
                self.setStrokeColor(teal)
                self.setLineWidth(1.2)
                self.line(left, letter[1] - 0.62 * inch, letter[0] - right, letter[1] - 0.62 * inch)
                self.setLineWidth(0.4)
                self.setStrokeColor(colors.HexColor("#9AA5B1"))
                self.line(left, 0.62 * inch, letter[0] - right, 0.62 * inch)
                self.setFont("Helvetica", 7.5)
                self.drawString(
                    left,
                    0.45 * inch,
                    "Synthetic document for demonstration only. Riverside Health Network is fictional.",
                )
                self.drawRightString(letter[0] - right, 0.45 * inch, f"Page {self._pageNumber} of {n}")
                super().showPage()
            super().save()

    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=left,
        rightMargin=right,
        topMargin=0.95 * inch,
        bottomMargin=0.85 * inch,
        title=meta["title"],
        author="Riverside Health Network (synthetic)",
        subject=f"{meta['doc_id']} version {meta['version']}",
        creator="healthcare pack build.py",
        invariant=1,
    )
    flow: list = [Paragraph(inline_rl(meta["title"]), title)]
    rows = [
        [
            Paragraph("Document ID", meta_k),
            Paragraph(inline_rl(meta["doc_id"]), cell),
            Paragraph("Version", meta_k),
            Paragraph(inline_rl(meta["version"]), cell),
        ],
        [
            Paragraph("Effective", meta_k),
            Paragraph(inline_rl(meta["effective"]), cell),
            Paragraph("Review by", meta_k),
            Paragraph(inline_rl(meta["review_by"]), cell),
        ],
        [Paragraph("Owner", meta_k), Paragraph(inline_rl(meta["owner"]), cell), "", ""],
        [Paragraph("Approved by", meta_k), Paragraph(inline_rl(meta["approved_by"]), cell), "", ""],
        [Paragraph("Applies to", meta_k), Paragraph(inline_rl(meta["applies_to"]), cell), "", ""],
    ]
    mt = Table(rows, colWidths=[0.95 * inch, 2.6 * inch, 0.85 * inch, avail - 4.4 * inch])
    mt.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9AA5B1")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E8F0F5")),
                ("BACKGROUND", (2, 0), (2, 1), colors.HexColor("#E8F0F5")),
                ("SPAN", (1, 2), (3, 2)),
                ("SPAN", (1, 3), (3, 3)),
                ("SPAN", (1, 4), (3, 4)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    flow += [mt, Spacer(1, 6)]
    for blk in blocks:
        kind = blk[0]
        if kind == "h1":
            flow.append(Paragraph(inline_rl(blk[1]), h1))
        elif kind == "h2":
            flow.append(Paragraph(inline_rl(blk[1]), h2))
        elif kind == "p":
            flow.append(Paragraph(inline_rl(blk[1]), body))
        elif kind in ("ul", "ol"):
            items = [ListItem(Paragraph(inline_rl(x), body), leftIndent=16) for x in blk[1]]
            if kind == "ul":
                flow.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=16, bulletFontSize=9))
            else:
                flow.append(
                    ListFlowable(
                        items,
                        bulletType="1",
                        bulletFormat="%s.",
                        leftIndent=16,
                        bulletFontName="Helvetica",
                        bulletFontSize=9.6,
                    )
                )
            flow.append(Spacer(1, 4))
        elif kind == "note":
            t = Table([[Paragraph(inline_rl(blk[1]), body)]], colWidths=[avail])
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EAF4F4")),
                        ("LINEBEFORE", (0, 0), (0, -1), 3, teal),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ]
                )
            )
            flow += [t, Spacer(1, 8)]
        elif kind == "table":
            header, trows = blk[1], blk[2]
            data = [[Paragraph(inline_rl(c), cell_h) for c in header]]
            data += [[Paragraph(inline_rl(c), cell) for c in r] for r in trows]
            t = Table(data, colWidths=col_widths(header, trows, avail), repeatRows=1)
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), navy),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F6F9")]),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9AA5B1")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            flow += [t, Spacer(1, 9)]
    doc.build(flow, canvasmaker=Chrome)


# --------------------------------------------------------------------------------------------------------------
# DOCX (python-docx)
# --------------------------------------------------------------------------------------------------------------


def _runs(par, text: str, size: float | None = None, color: str | None = None, bold: bool = False) -> None:
    from docx.shared import Pt
    from docx.shared import RGBColor

    for tok in re.split(r"(\*\*.+?\*\*|`[^`]+`|(?<![\w*])\*(?!\s).+?(?<!\s)\*(?![\w*]))", text):
        if not tok:
            continue
        if tok.startswith("**"):
            run, run.bold = par.add_run(tok[2:-2]), True
        elif tok.startswith("`"):
            run = par.add_run(tok[1:-1])
            run.font.name = "Courier New"
        elif tok.startswith("*") and tok.endswith("*") and len(tok) > 2:
            run, run.italic = par.add_run(tok[1:-1]), True
        else:
            run = par.add_run(tok)
        if bold:
            run.bold = True
        if size:
            run.font.size = Pt(size)
        if color:
            run.font.color.rgb = RGBColor.from_string(color)


def _shade(cell, fill: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def build_docx(path: Path, meta: dict[str, str], blocks: list[tuple]) -> None:
    import docx
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches
    from docx.shared import Pt
    from docx.shared import RGBColor

    d = docx.Document()
    sec = d.sections[0]
    sec.left_margin = sec.right_margin = Inches(0.9)
    sec.top_margin = sec.bottom_margin = Inches(0.9)
    normal = d.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    for name, size, color in (("Title", 22, NAVY), ("Heading 1", 14, NAVY), ("Heading 2", 12, TEAL)):
        st = d.styles[name]
        st.font.name = "Calibri"
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = RGBColor.from_string(color[1:])
    hp = sec.header.paragraphs[0]
    hp.text = f"RIVERSIDE HEALTH NETWORK    {meta['doc_id']}  |  Version {meta['version']}"
    fp = sec.footer.paragraphs[0]
    fp.text = "Synthetic document for demonstration only. Riverside Health Network is fictional."
    for p in (hp, fp):
        for r in p.runs:
            r.font.size = Pt(8)
            r.font.color.rgb = RGBColor(0x4A, 0x55, 0x60)
    d.add_heading(meta["title"], 0)
    mt = d.add_table(rows=0, cols=4)
    mt.style = "Table Grid"
    mt.alignment = WD_TABLE_ALIGNMENT.CENTER
    for row in (
        ("Document ID", meta["doc_id"], "Version", meta["version"]),
        ("Effective", meta["effective"], "Review by", meta["review_by"]),
        ("Owner", meta["owner"], None, None),
        ("Approved by", meta["approved_by"], None, None),
        ("Applies to", meta["applies_to"], None, None),
    ):
        cells = mt.add_row().cells
        for j, v in enumerate(row):
            if v is None:
                continue
            cells[j].text = ""
            _runs(cells[j].paragraphs[0], v, size=9.5, bold=j in (0, 2))
            if j in (0, 2):
                _shade(cells[j], "E8F0F5")
        if row[2] is None:
            cells[1].merge(cells[3])
    d.add_paragraph()
    for blk in blocks:
        kind = blk[0]
        if kind == "h1":
            d.add_heading(blk[1], 1)
        elif kind == "h2":
            d.add_heading(blk[1], 2)
        elif kind == "p":
            _runs(d.add_paragraph(), blk[1])
        elif kind == "ul":
            for item in blk[1]:
                _runs(d.add_paragraph(style="List Bullet"), item)
        elif kind == "ol":
            for n, item in enumerate(blk[1], start=1):
                par = d.add_paragraph()
                par.paragraph_format.left_indent = Inches(0.3)
                par.paragraph_format.first_line_indent = Inches(-0.25)
                _runs(par, f"{n}. {item}")
        elif kind == "note":
            par = d.add_paragraph()
            par.paragraph_format.left_indent = Inches(0.15)
            ppr = par._p.get_or_add_pPr()
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), "EAF4F4")
            ppr.append(shd)
            _runs(par, blk[1])
        elif kind == "table":
            header, trows = blk[1], blk[2]
            tbl = d.add_table(rows=1, cols=len(header))
            tbl.style = "Table Grid"
            tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
            widths = [w / 72 for w in col_widths(header, trows, 6.7 * 72, 5.4)]
            for j, h in enumerate(header):
                c = tbl.rows[0].cells[j]
                c.text = ""
                _runs(c.paragraphs[0], h, size=8.5, color="FFFFFF", bold=True)
                _shade(c, NAVY[1:])
                c.width = Inches(widths[j])
            for ri, r in enumerate(trows):
                cells = tbl.add_row().cells
                for j, v in enumerate(r):
                    cells[j].text = ""
                    _runs(cells[j].paragraphs[0], v, size=8.5)
                    cells[j].width = Inches(widths[j])
                    if ri % 2 == 1:
                        _shade(cells[j], "F1F6F9")
            d.add_paragraph()
    cp = d.core_properties
    cp.author = "Riverside Health Network (synthetic)"
    cp.last_modified_by = "build.py"
    cp.title = meta["title"]
    cp.subject = f"{meta['doc_id']} version {meta['version']}"
    cp.comments = "Synthetic document for demonstration only."
    cp.created = cp.modified = FIXED_DT
    cp.revision = 1
    d.save(path)
    normalize_zip(path)


# --------------------------------------------------------------------------------------------------------------
# PPTX (python-pptx)
# --------------------------------------------------------------------------------------------------------------


def deck_tables(ctx: dict, sc: dict[str, pd.DataFrame]) -> dict[str, tuple[list[str], list[list[str]]]]:
    def status(v: float, target: float, lower_is_better: bool) -> str:
        return "Met" if (v <= target if lower_is_better else v >= target) else "Not met"

    tabs: dict[str, tuple[list[str], list[list[str]]]] = {}
    tabs["scorecard"] = (
        ["Measure", "Q1 2026", "Q2 2026", "Target", "Status"],
        [
            [
                "30-day all-cause readmission rate",
                ctx["q1_all_rate"],
                ctx["q2_all_rate"],
                "at most " + ctx["target.all_cause"],
                status(ctx["q2_all_num"], TARGETS["all_cause"], True),
            ],
            [
                "30-day heart failure readmission rate",
                ctx["q1_hf_rate"],
                ctx["q2_hf_rate"],
                "at most " + ctx["target.hf_readmit"],
                status(ctx["q2_hf_num"], TARGETS["hf_readmit"], True),
            ],
            [
                "30-day COPD readmission rate",
                ctx["q1_copd_rate"],
                ctx["q2_copd_rate"],
                "at most " + ctx["target.copd_readmit"],
                status(ctx["q2_copd_num"], TARGETS["copd_readmit"], True),
            ],
            [
                "Heart failure 7-day follow-up completion",
                ctx["q1_fu_all_rate"],
                ctx["q2_fu_all_rate"],
                "at least " + ctx["target.hf_followup"],
                status(ctx["q2_fu_all_num"], TARGETS["hf_followup"], False),
            ],
            [
                "Claim denial rate (adjudicated claims)",
                ctx["q1_den_all_rate"],
                ctx["q2_den_all_rate"],
                "at most " + pct(TARGETS["denial"]),
                status(ctx["q2_den_all_num"], TARGETS["denial"], True),
            ],
            [
                "HCAHPS overall hospital rating (9-10)",
                ctx["q1_hc_overall_rate"],
                ctx["q2_hc_overall_rate"],
                "at least " + pct(TARGETS["hcahps_overall"]),
                status(ctx["q2_hc_overall_num"], TARGETS["hcahps_overall"], False),
            ],
        ],
    )
    rows = []
    for h in HOSPITALS:
        chg = ctx[f"q2_{h}_num"] - ctx[f"q1_{h}_num"]
        rows.append(
            [
                FACILITY_NAME[h],
                ctx[f"q1_{h}_rate"],
                ctx[f"q2_{h}_rate"],
                f"{chg:+.1f}",
                str(ctx[f"q2_{h}_n"]),
                "at most " + ctx["target.all_cause"],
            ]
        )
    chg = ctx["q2_all_num"] - ctx["q1_all_num"]
    rows.append(
        [
            "System",
            ctx["q1_all_rate"],
            ctx["q2_all_rate"],
            f"{chg:+.1f}",
            str(ctx["q2_all_n"]),
            "at most " + ctx["target.all_cause"],
        ]
    )
    tabs["readmit_hospital"] = (
        ["Hospital", "Q1 2026", "Q2 2026", "Change (pts)", "Q2 index discharges", "Target"],
        rows,
    )
    rows = []
    for h in HOSPITALS:
        rows.append(
            [
                FACILITY_NAME[h],
                str(ctx[f"q2_hf_{h}_n"]),
                str(ctx[f"q2_hf_{h}_r"]),
                ctx[f"q2_hf_{h}_rate"],
                ctx[f"q1_hf_{h}_rate"],
            ]
        )
    rows.append(["System", str(ctx["q2_hf_n"]), str(ctx["q2_hf_r"]), ctx["q2_hf_rate"], ctx["q1_hf_rate"]])
    tabs["hf_cohort"] = (["Hospital", "Q2 HF index discharges", "Q2 readmissions", "Q2 rate", "Q1 rate"], rows)
    rows = []
    for h in HOSPITALS:
        rows.append(
            [
                FACILITY_NAME[h],
                str(ctx[f"q2_fu_{h}_n"]),
                str(ctx[f"q2_fu_{h}_ok"]),
                ctx[f"q2_fu_{h}_rate"],
                ctx[f"q1_fu_{h}_rate"],
            ]
        )
    rows.append(
        ["System", str(ctx["q2_fu_all_n"]), str(ctx["q2_fu_all_ok"]), ctx["q2_fu_all_rate"], ctx["q1_fu_all_rate"]]
    )
    tabs["followup"] = (
        ["Hospital", "Q2 eligible discharges", "Visit within 7 days", "Q2 completion", "Q1 completion"],
        rows,
    )
    rows = []
    for p in PAYERS:
        rows.append([p, str(ctx[f"q2_den_{p}_total"]), str(ctx[f"q2_den_{p}_n"]), ctx[f"q2_den_{p}_rate"]])
    rows.append(["All payers", str(ctx["q2_den_all_total"]), str(ctx["q2_den_all_n"]), ctx["q2_den_all_rate"]])
    tabs["denials"] = (["Payer", "Claims adjudicated in Q2", "Denied", "Denial rate"], rows)
    hc = sc["HCAHPS"]
    hq = hc[hc["quarter"] == "2026Q2"]
    rows = []
    for h in HOSPITALS:
        r = hq[hq["facility_id"] == h].set_index("domain")
        rows.append(
            [
                FACILITY_NAME[h],
                pct(float(r.loc[HCAHPS_OVERALL, "top_box_pct"])),
                pct(float(r.loc["Discharge information", "top_box_pct"])),
                pct(float(r.loc["Care transition", "top_box_pct"])),
                str(int(r.loc[HCAHPS_OVERALL, "survey_responses"])),
            ]
        )
    tabs["hcahps"] = (
        ["Hospital", "Overall rating (9-10)", "Discharge information", "Care transition", "Responses"],
        rows,
    )
    return tabs


def build_pptx(path: Path, spec: dict, ctx: dict, sc: dict[str, pd.DataFrame]) -> None:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.chart import XL_LEGEND_POSITION
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Emu
    from pptx.util import Inches
    from pptx.util import Pt

    navy, teal = RGBColor(0x0B, 0x3C, 0x5D), RGBColor(0x1B, 0x7F, 0x8C)
    tables = deck_tables(ctx, sc)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    W = prs.slide_width

    def style_title(shape, text: str, size: int = 28) -> None:
        shape.left, shape.top, shape.width, shape.height = Inches(0.6), Inches(0.35), Inches(12.1), Inches(0.9)
        tf = shape.text_frame
        tf.text = text
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT
        for r in p.runs:
            r.font.size, r.font.bold, r.font.color.rgb, r.font.name = Pt(size), True, navy, "Calibri"

    def add_text(slide, box: tuple, lines: list[str], size: int = 15, bullet: bool = True) -> None:
        x, y, w, h = box
        tb = slide.shapes.add_textbox(x, y, w, h)
        tf = tb.text_frame
        tf.word_wrap = True
        for k, line in enumerate(lines):
            p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
            p.text = ("• " if bullet else "") + line
            p.space_after = Pt(7)
            for r in p.runs:
                r.font.size, r.font.name, r.font.color.rgb = Pt(size), "Calibri", RGBColor(0x22, 0x2B, 0x33)

    def add_table(slide, box: tuple, header, rows, font: int = 13):
        x, y, w = box
        shape = slide.shapes.add_table(len(rows) + 1, len(header), x, y, w, Inches(0.42) * (len(rows) + 1))
        tbl = shape.table
        widths = col_widths(header, rows, w / 12700, 6.6)
        for j, pts in enumerate(widths):
            tbl.columns[j].width = Emu(int(pts * 12700))
        for j, h in enumerate(header):
            c = tbl.cell(0, j)
            c.text = h
            c.fill.solid()
            c.fill.fore_color.rgb = navy
            for r in c.text_frame.paragraphs[0].runs:
                r.font.size, r.font.bold, r.font.color.rgb, r.font.name = (
                    Pt(font),
                    True,
                    RGBColor(255, 255, 255),
                    "Calibri",
                )
        for i, row in enumerate(rows, start=1):
            last = row[0] in ("System", "All payers")
            for j, v in enumerate(row):
                c = tbl.cell(i, j)
                c.text = v
                c.fill.solid()
                c.fill.fore_color.rgb = (
                    RGBColor(0xDD, 0xEA, 0xF1)
                    if last
                    else (RGBColor(0xF1, 0xF6, 0xF9) if i % 2 == 0 else RGBColor(255, 255, 255))
                )
                for r in c.text_frame.paragraphs[0].runs:
                    r.font.size, r.font.bold, r.font.name = Pt(font), last, "Calibri"
                    r.font.color.rgb = RGBColor(0x22, 0x2B, 0x33)
        return shape

    def footer(slide, n: int) -> None:
        add_text(
            slide,
            (Inches(0.6), Inches(7.0), Inches(10), Inches(0.35)),
            ["Riverside Health Network | Clinical Quality Committee | Synthetic data for demonstration"],
            size=10,
            bullet=False,
        )
        add_text(slide, (Inches(12.0), Inches(7.0), Inches(0.8), Inches(0.35)), [str(n)], size=10, bullet=False)

    for n, s in enumerate(spec["slides"], start=1):
        kind = s["kind"]
        if kind == "title":
            slide = prs.slides.add_slide(prs.slide_layouts[0])
            bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, W, prs.slide_height)
            bg.fill.solid()
            bg.fill.fore_color.rgb = navy
            bg.line.fill.background()
            slide.shapes._spTree.remove(bg._element)
            slide.shapes._spTree.insert(2, bg._element)
            t, sub = slide.shapes.title, slide.placeholders[1]
            t.left, t.top, t.width, t.height = Inches(0.9), Inches(2.3), Inches(11.5), Inches(1.4)
            t.text_frame.text = s["title"]
            sub.left, sub.top, sub.width, sub.height = Inches(0.9), Inches(3.8), Inches(11.5), Inches(1.0)
            sub.text_frame.text = s["subtitle"]
            for shp, size, bold in ((t, 44, True), (sub, 24, False)):
                p = shp.text_frame.paragraphs[0]
                p.alignment = PP_ALIGN.LEFT
                for r in p.runs:
                    r.font.size, r.font.bold, r.font.name, r.font.color.rgb = (
                        Pt(size),
                        bold,
                        "Calibri",
                        RGBColor(255, 255, 255),
                    )
            add_text(slide, (Inches(0.9), Inches(5.3), Inches(11.5), Inches(0.5)), [s["detail"]], size=16, bullet=False)
            for p in slide.shapes[-1].text_frame.paragraphs:
                for r in p.runs:
                    r.font.color.rgb = RGBColor(0xCF, 0xE3, 0xEC)
        else:
            slide = prs.slides.add_slide(prs.slide_layouts[5])
            style_title(slide.shapes.title, fill(s["title"], ctx))
            ln = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(1.2), Inches(1.6), Emu(38100))
            ln.fill.solid()
            ln.fill.fore_color.rgb = teal
            ln.line.fill.background()
            bullets = [fill(b, ctx) for b in s.get("bullets", [])]
            if kind == "bullets":
                add_text(slide, (Inches(0.6), Inches(1.5), Inches(12.0), Inches(5.2)), bullets, size=18)
            else:
                header, rows = tables[s["table"]]
                if kind == "table_chart":
                    add_table(slide, (Inches(0.6), Inches(1.5), Inches(7.3)), header, rows, font=12)
                    cd = CategoryChartData()
                    cd.categories = [r[0].replace(" Hospital", "") for r in rows[:-1]] + ["System"]
                    cd.add_series("Q1 2026", [round(float(r[1].rstrip("%")), 1) for r in rows])
                    cd.add_series("Q2 2026", [round(float(r[2].rstrip("%")), 1) for r in rows])
                    gf = slide.shapes.add_chart(
                        XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(8.1), Inches(1.4), Inches(4.8), Inches(3.5), cd
                    )
                    ch = gf.chart
                    ch.has_legend, ch.legend.position, ch.legend.include_in_layout = (
                        True,
                        XL_LEGEND_POSITION.BOTTOM,
                        False,
                    )
                    ch.legend.font.size = Pt(11)
                    ch.has_title = True
                    ch.chart_title.text_frame.text = "30-day readmission rate (%)"
                    ch.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(12)
                    plot = ch.plots[0]
                    plot.has_data_labels = True
                    plot.data_labels.font.size = Pt(10)
                    plot.data_labels.number_format, plot.data_labels.number_format_is_linked = "0.0", False
                    for series, color in zip(plot.series, (RGBColor(0x9A, 0xA5, 0xB1), teal), strict=True):
                        series.format.fill.solid()
                        series.format.fill.fore_color.rgb = color
                    ch.category_axis.tick_labels.font.size = Pt(10)
                    ch.value_axis.tick_labels.font.size = Pt(10)
                    ch.value_axis.has_major_gridlines = False
                    add_text(slide, (Inches(0.6), Inches(5.0), Inches(12.0), Inches(1.9)), bullets, size=15)
                else:
                    shape = add_table(slide, (Inches(0.6), Inches(1.5), Inches(12.1)), header, rows)
                    y = Inches(1.5) + shape.height + Inches(0.3)
                    add_text(slide, (Inches(0.6), y, Inches(12.0), Inches(7.0) - y), bullets, size=15)
        if s.get("notes"):
            slide.notes_slide.notes_text_frame.text = fill(s["notes"], ctx)
        if kind != "title":
            footer(slide, n)
    cp = prs.core_properties
    cp.author = spec["author"]
    cp.last_modified_by = "build.py"
    cp.title = spec["title"]
    cp.subject = "Quarterly clinical quality review (synthetic)"
    cp.comments = "Synthetic document for demonstration only."
    cp.created = cp.modified = FIXED_DT
    cp.revision = 1
    prs.save(path)
    normalize_zip(path)


# --------------------------------------------------------------------------------------------------------------
# Scanned discharge checklist (PNG)
# --------------------------------------------------------------------------------------------------------------

FORM_SECTIONS = [
    (
        "A. CLINICAL READINESS",
        [
            ("Weight at dry weight or within 3 lb; discharge weight recorded", True),
            ("Oral diuretic regimen stable for 24 hours, no IV diuretic", True),
            ("Potassium and creatinine from the last 24 hours reviewed", True),
        ],
    ),
    (
        "B. MEDICINES AND EDUCATION",
        [
            ("Medication reconciliation completed by pharmacist or RN", True),
            ("Discharge medication list reviewed with patient", True),
            ("Teach-back completed with patient and caregiver", False),
            ("Daily weight log and 2,000 mg sodium handout given", True),
            ("Working home scale confirmed", False),
        ],
    ),
    (
        "C. FOLLOW-UP",
        [
            ("48-hour Care Transitions call scheduled", True),
            ("7-day follow-up visit scheduled (enter date below)", False),
            ("14-day basic metabolic panel ordered", True),
            ("Home health referral sent (if eligible)", True),
            ("Discharge summary sent to primary care clinic", True),
        ],
    ),
]
FORM_WEIGHT_LB = "183.4"
FORM_DRY_WEIGHT_LB = "181"
FORM_NOTE = "Pt lives alone, daughter drives. Clinic line busy, appt NOT booked. RN to call pt 48 h."


def pick_form_case(t: dict[str, pd.DataFrame]) -> dict:
    """The scanned form belongs to a heart failure patient from August 2026 who missed the 7-day visit."""
    ip = inpatient_index(t["encounters"], t["diagnoses"])
    fu = hf_followup(ip, t["encounters"])
    aug = fu[
        (fu["discharge_date"] >= date(2026, 8, 1)) & (fu["discharge_date"] <= date(2026, 8, 31)) & ~fu["followup_7d"]
    ]
    pref = aug[aug["facility_id"] == "F03"]
    row = (pref if len(pref) else aug).sort_values(["discharge_date", "encounter_id"]).iloc[0]
    return {
        "patient_id": row["patient_id"],
        "encounter_id": row["encounter_id"],
        "facility_id": row["facility_id"],
        "facility_name": FACILITY_NAME[row["facility_id"]],
        "admit_date": d2s(row["admit_dt"]),
        "discharge_date": d2s(row["discharge_dt"]),
        "primary_code": row["primary_code"],
        "primary_text": DX[row["primary_code"]],
        "disposition": row["discharge_disposition"],
    }


def build_form_png(path: Path, case: dict, seed: int = SEED) -> None:
    import io

    import pypdfium2 as pdfium
    from PIL import Image
    from PIL import ImageFilter
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas

    rng = np.random.default_rng([seed, 6])
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792), invariant=1)
    ink = Color(0.05, 0.05, 0.12)
    pen = Color(0.06, 0.07, 0.30)
    gray = Color(0.38, 0.38, 0.38)

    def hand(x: float, y: float, text: str, size: float = 12.0) -> None:
        c.setFillColor(pen)
        x0 = x
        for ch in text:
            c.saveState()
            c.translate(x0, y + float(rng.uniform(-1.3, 1.3)))
            c.rotate(float(rng.uniform(-7, 7)))
            c.setFont("Helvetica-Oblique", size)
            c.drawString(0, 0, ch)
            c.restoreState()
            x0 += c.stringWidth(ch, "Helvetica-Oblique", size) * float(rng.uniform(0.93, 1.07))

    def tick(x: float, y: float, s: float = 11.0) -> None:
        c.setStrokeColor(pen)
        c.setLineWidth(1.9)
        j = lambda: float(rng.uniform(-0.7, 0.7))  # noqa: E731
        p = c.beginPath()
        p.moveTo(x + 1.5 + j(), y + s * 0.55 + j())
        p.lineTo(x + s * 0.40 + j(), y + 0.8 + j())
        p.lineTo(x + s * 1.18 + j(), y + s * 1.05 + j())
        c.drawPath(p, stroke=1, fill=0)

    def field(x: float, y: float, label: str, value: str, size: float = 11.0) -> None:
        c.setFillColor(gray)
        c.setFont("Helvetica", 7.2)
        c.drawString(x, y + 12, label.upper())
        c.setFillColor(ink)
        c.setFont("Courier-Bold", size)
        c.drawString(x, y, value)

    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, 746, "RIVERSIDE HEALTH NETWORK")
    c.setFont("Helvetica-Bold", 12.5)
    c.drawString(40, 728, "Heart Failure Discharge & Transition Checklist")
    c.setFont("Helvetica", 8.5)
    c.drawRightString(572, 746, "Form RHN-F-0231  (rev 06/2026)")
    c.drawRightString(572, 734, "Protocol RHN-CLN-HF-014 v4.2")
    c.setLineWidth(2)
    c.setStrokeColor(ink)
    c.line(40, 718, 572, 718)

    c.setLineWidth(0.8)
    c.rect(40, 590, 532, 120)
    field(50, 688, "Patient ID", case["patient_id"])
    field(310, 688, "Facility", f"{case['facility_name']} ({case['facility_id']})", 9.5)
    field(50, 658, "Admission date", case["admit_date"])
    field(310, 658, "Discharge date", case["discharge_date"])
    field(
        50,
        628,
        "Primary diagnosis",
        f"{case['primary_code']} {case['primary_text'].replace(' (congestive) heart failure', ' HF')}",
        9.5,
    )
    field(310, 628, "Discharge disposition", "Home" if case["disposition"] == "home" else "Home health")
    field(50, 600, "Discharge weight", f"{FORM_WEIGHT_LB} lb")
    field(190, 600, "Dry weight target", f"{FORM_DRY_WEIGHT_LB} lb")
    field(340, 600, "BP / HR", "118/72, 74")
    field(460, 600, "LVEF", "32 %")

    y = 568.0
    for heading, items in FORM_SECTIONS:
        c.setFillColor(Color(0.86, 0.86, 0.86))
        c.rect(40, y - 5, 532, 17, stroke=0, fill=1)
        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(46, y, heading)
        y -= 24
        for text, checked in items:
            c.setStrokeColor(ink)
            c.setLineWidth(1.1)
            c.rect(50, y - 2, 11, 11)
            if checked:
                tick(50, y - 2)
            c.setFillColor(ink)
            c.setFont("Helvetica", 10)
            c.drawString(72, y, text)
            c.setStrokeColor(Color(0.7, 0.7, 0.7))
            c.setLineWidth(0.4)
            c.line(72, y - 6, 560, y - 6)
            y -= 22
        y -= 4
    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "7-day follow-up appointment date:")
    c.setStrokeColor(ink)
    c.setLineWidth(0.8)
    c.line(235, y - 2, 380, y - 2)
    hand(395, y, "NOT BOOKED", 13)
    y -= 26
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(50, y, "Nurse notes")
    c.setLineWidth(0.8)
    c.rect(40, y - 52, 532, 62)
    hand(50, y - 14, FORM_NOTE[:60], 11.5)
    hand(50, y - 32, FORM_NOTE[60:], 11.5)
    y -= 78
    c.setFillColor(ink)
    c.setFont("Helvetica", 9.5)
    c.drawString(50, y, "Discharge RN initials:")
    c.line(150, y - 2, 195, y - 2)
    hand(158, y, "JT", 13)
    c.drawString(215, y, "Date:")
    c.line(245, y - 2, 330, y - 2)
    hand(250, y, case["discharge_date"][5:].replace("-", "/") + "/26", 11.5)
    c.drawString(360, y, "Physician initials:")
    c.line(445, y - 2, 490, y - 2)
    hand(453, y, "KR", 13)
    c.setFillColor(gray)
    c.setFont("Helvetica", 7.2)
    c.drawString(
        40,
        34,
        "RHN-F-0231 rev 06/2026  |  File a scanned copy in the chart  |  Synthetic form: fictional patient",
    )
    c.showPage()
    c.save()

    page = pdfium.PdfDocument(buf.getvalue())[0]
    img = page.render(scale=1.8).to_pil().convert("L").filter(ImageFilter.GaussianBlur(0.75))
    arr = np.asarray(img, dtype=np.float32)
    h, w = arr.shape
    yy, xx = np.mgrid[0:h, 0:w]
    light = 1.0 - 0.06 * (xx / w) - 0.05 * (yy / h) - 0.10 * (((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2) ** 1.0)
    arr = arr * 0.93 * light
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).rotate(
        -1.3, resample=Image.Resampling.BICUBIC, fillcolor=186
    )
    arr = np.asarray(img, dtype=np.float32) + rng.normal(0, 5.5, (h, w)).astype(np.float32)
    for _ in range(260):
        x, y0, r = int(rng.integers(0, w - 3)), int(rng.integers(0, h - 3)), int(rng.integers(1, 3))
        arr[y0 : y0 + r, x : x + r] -= float(rng.uniform(40, 120))
    arr += 2.0 * np.sin(np.arange(h, dtype=np.float32) / 5.0)[:, None]
    arr = np.round(np.clip(arr, 0, 255) / 3.0) * 3.0
    Image.fromarray(arr.astype(np.uint8), "L").save(path, optimize=True)


# --------------------------------------------------------------------------------------------------------------
# README: data card and answer key (every number comes from the generated tables)
# --------------------------------------------------------------------------------------------------------------


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


Q10_PAYER = "Northstar Advantage"  # the payer the claim-denial-risk question asks about (highest denial rate)


def answer_key(res: dict) -> dict[str, str]:
    t, ctx, case, sc = res["tables"], res["ctx"], res["case"], res["scorecards"]
    enc, dx, claims, pat = t["encounters"], t["diagnoses"], t["claims"], t["patients"]
    ip = inpatient_index(enc, dx)
    ak: dict[str, str] = {}

    # Row counts
    counts = {k: len(v) for k, v in t.items()}
    counts["quality_scorecard_readmissions"] = len(sc["Readmissions"])
    counts["quality_scorecard_hcahps"] = len(sc["HCAHPS"])
    ak["rows_total"] = f"{sum(counts.values()):,}"
    ak["n_patients"] = f"{counts['patients']:,}"
    ak["n_encounters"] = f"{counts['encounters']:,}"
    for k, v in counts.items():
        ak[f"rows.{k}"] = f"{v:,}"
    ak["n_inpatient"] = f"{int((enc['encounter_type'] == 'inpatient').sum()):,}"
    ak["n_ed"] = f"{int((enc['encounter_type'] == 'ED').sum()):,}"
    ak["n_outpatient"] = f"{int((enc['encounter_type'] == 'outpatient').sum()):,}"
    ak["n_denied"] = f"{int(claims['denied'].sum()):,}"

    # Q1 scanned checklist
    unchecked = [text for _, items in FORM_SECTIONS for text, ok in items if not ok]
    items = "; ".join(f'"{u}"' for u in unchecked)
    ak["q1"] = (
        f"Discharge weight **{FORM_WEIGHT_LB} lb** (dry weight target {FORM_DRY_WEIGHT_LB} lb). "
        f"Three items are unchecked: {items}. "
        'The 7-day appointment date is blank and handwritten "NOT BOOKED". '
        f"The form is for patient {case['patient_id']} ({case['facility_name']}, discharged {case['discharge_date']}), "
        "who also appears in the answer to question 7."
    )

    # Q4 readmissions by hospital, Q2 2026
    rows = []
    for h in HOSPITALS:
        rows.append([FACILITY_NAME[h], ctx[f"q2_{h}_n"], ctx[f"q2_{h}_r"], ctx[f"q2_{h}_rate"]])
    rows.append(["All three hospitals", ctx["q2_all_n"], ctx["q2_all_r"], ctx["q2_all_rate"]])
    ak["q4"] = _md_table(["Hospital", "Index discharges", "Readmitted within 30 days", "Rate"], rows)

    # Q5 denial reasons by billed dollars, adjudicated in 2026
    den = claims[(claims["denied"] == 1) & (pd.to_datetime(claims["adjudicated_date"]) >= "2026-01-01")]
    by = den.groupby("denial_reason").agg(billed=("billed_amount", "sum"), n=("claim_id", "count")).reset_index()
    by = by.sort_values(["billed", "denial_reason"], ascending=[False, True])
    top3 = by.head(3)
    share = 100.0 * top3["billed"].sum() / by["billed"].sum()
    ak["q5"] = _md_table(
        ["Rank", "Denial reason", "Denied claims", "Billed amount"],
        [[i + 1, r.denial_reason, r.n, f"${r.billed:,.2f}"] for i, r in enumerate(top3.itertuples())],
    )
    ak["q5_total"] = f"${by['billed'].sum():,.2f}"
    ak["q5_n"] = str(int(by["n"].sum()))
    ak["q5_share"] = pct(share)
    ak["q5_top3_sum"] = f"${top3['billed'].sum():,.2f}"

    # Q6 cohort comparison
    full = (date(2025, 4, 1), date(2026, 8, 31))
    rows = []
    for label, coh in (
        ("Heart failure (I50.x)", "Heart failure"),
        ("COPD (J44.x)", "COPD"),
        ("All other inpatient discharges", "Other"),
    ):
        n, r, rate = readmission_rate(ip, *full, cohort=coh)
        rows.append([label, n, r, pct(rate)])
    n, r, rate = readmission_rate(ip, *full)
    rows.append(["All inpatient discharges", n, r, pct(rate)])
    ak["q6"] = _md_table(["Cohort", "Index discharges", "Readmitted within 30 days", "Rate"], rows)

    # Q7 missed 7-day follow-up, August 2026
    fu = hf_followup(ip, enc)
    aug = fu[(fu["discharge_date"] >= date(2026, 8, 1)) & (fu["discharge_date"] <= date(2026, 8, 31))].sort_values(
        ["discharge_date", "patient_id"]
    )
    missed = aug[~aug["followup_7d"]]
    ak["q7_eligible"], ak["q7_missed_n"] = str(len(aug)), str(len(missed))
    ak["q7_completed"] = str(len(aug) - len(missed))
    ak["q7_missed_patients"] = str(missed["patient_id"].nunique())
    ak["q7"] = _md_table(
        ["Patient", "Hospital", "Discharged", "Disposition"],
        [
            [r.patient_id, FACILITY_NAME[r.facility_id], d2s(r.discharge_dt), r.discharge_disposition]
            for r in missed.itertuples()
        ],
    )
    hf_aug_all = ip[
        (ip["cohort"] == "Heart failure")
        & (ip["discharge_date"] >= date(2026, 8, 1))
        & (ip["discharge_date"] <= date(2026, 8, 31))
    ]
    other = hf_aug_all[~hf_aug_all["encounter_id"].isin(aug["encounter_id"])]
    ak["q7_other_n"] = str(len(other))
    ak["q7_other_dispositions"] = (
        ", ".join(f"{k} ({v})" for k, v in other["discharge_disposition"].value_counts().sort_index().items()) or "none"
    )
    ak["q7_all_n"] = str(len(hf_aug_all))

    # Q8 appealable denied claims
    ap = appealable_claims(claims)
    ap = ap[ap["appealable"]]
    rows = []
    for payer, days in APPEAL_WINDOW_DAYS.items():
        x = ap[ap["payer"] == payer]
        if len(x):
            rows.append([payer, days, len(x), f"${x['billed_amount'].sum():,.2f}"])
    rows.append(["All payers", "", len(ap), f"${ap['billed_amount'].sum():,.2f}"])
    ak["q8"] = _md_table(["Payer", "Window (days)", "Denied, not appealed, still open", "Billed amount"], rows)
    ak["q8_n"] = str(len(ap))
    ak["q8_total"] = f"${ap['billed_amount'].sum():,.2f}"
    all_none = claims[(claims["denied"] == 1) & (claims["appeal_status"] == "none")]
    ak["q8_none_n"] = str(len(all_none))
    ak["q8_closed_n"] = str(len(all_none) - len(ap))
    nxt = ap.sort_values("deadline").iloc[0]
    ak["q8_next"] = f"{nxt['claim_id']} ({nxt['payer']}, deadline {nxt['deadline'].date()})"

    # Q9 and Q10 reference rates, averaged over rolling windows whose outcomes are fully known
    ip_all = enc[enc["encounter_type"] == "inpatient"].copy()
    ip_all["admit_date"] = pd.to_datetime(ip_all["admit_ts"]).dt.normalize()
    hf_ids = set(pat.loc[pat["has_heart_failure"] == 1, "patient_id"])
    starts = [pd.Timestamp(START) + pd.Timedelta(days=30 * k) for k in range(17)]  # 2025-04-01 ... 2026-07-25
    all_rates, hf_rates = [], []
    for st in starts:
        w = ip_all[(ip_all["admit_date"] >= st) & (ip_all["admit_date"] < st + pd.Timedelta(days=30))]
        who = set(w["patient_id"])
        all_rates.append(100.0 * len(who) / len(pat))
        hf_rates.append(100.0 * len(who & hf_ids) / len(hf_ids))
    ak["q9_hf_n"] = str(len(hf_ids))
    ak["q9_hf"], ak["q9_all"] = pct(float(np.mean(hf_rates))), pct(float(np.mean(all_rates)))
    ak["q9_windows"] = str(len(starts))
    sep_hf = ip[(ip["cohort"] == "Heart failure") & ip["index_eligible"] & (ip["discharge_date"] >= date(2026, 9, 1))]
    ak["q9_sep_hf_n"] = str(sep_hf["patient_id"].nunique())
    ak["q9_sep_hf_ids"] = ", ".join(sorted(sep_hf["patient_id"].unique()))
    hf_rate = readmission_rate(ip, *full, cohort="Heart failure")[2]
    ak["q9_hf_readmit"], ak["q9_all_readmit"] = pct(hf_rate), pct(readmission_rate(ip, *full)[2])
    cl = claims.copy()
    cl["sub"] = pd.to_datetime(cl["submitted_date"])
    starts = [pd.Timestamp("2026-05-31") - pd.Timedelta(days=60 * k) for k in range(6)]  # latest window ends 2026-07-30
    by_payer: dict[str, list[float]] = {p: [] for p in PAYERS if p != "Self-pay"}
    overall: list[float] = []
    ns_ids = set(pat.loc[pat["payer"] == Q10_PAYER, "patient_id"])
    for st in starts:
        w = cl[(cl["sub"] > st) & (cl["sub"] <= st + pd.Timedelta(days=60)) & (cl["denied"] == 1)]
        who = set(w["patient_id"])
        overall.append(100.0 * len(who) / len(pat))
        for payer, series in by_payer.items():
            ids = set(pat.loc[pat["payer"] == payer, "patient_id"])
            series.append(100.0 * len(who & ids) / len(ids))
    ak["q10_windows"] = str(len(starts))
    ak["q10_all"] = pct(float(np.mean(overall)))
    ak["q10_first"] = d2s(starts[-1] + pd.Timedelta(days=1))
    ak["q10"] = _md_table(
        ["Payer", "Patients", "Share with a denied claim in a 60-day window"],
        [[p, int((pat["payer"] == p).sum()), pct(float(np.mean(v)))] for p, v in by_payer.items()],
    )
    # The question asks about one payer's patients (226 of 1,400): Kumo scores at most 1,000 entities per request
    ak["q10_payer_n"] = str(len(ns_ids))
    ak["q10_payer_rate"] = pct(float(np.mean(by_payer[Q10_PAYER])))
    prior = set(cl.loc[(cl["denied"] == 1) & (cl["sub"] <= "2026-05-31") & cl["patient_id"].isin(ns_ids), "patient_id"])
    later = set(
        cl.loc[
            (cl["denied"] == 1)
            & (cl["sub"] >= "2026-06-01")
            & (cl["sub"] <= "2026-07-30")
            & cl["patient_id"].isin(ns_ids),
            "patient_id",
        ]
    )
    ak["q10_prior_n"] = str(len(prior))
    ak["q10_prior_rate"] = pct(100.0 * len(prior & later) / len(prior))
    ak["q10_never_rate"] = pct(100.0 * len(later - prior) / (len(ns_ids) - len(prior)))
    recent = sorted(
        set(cl.loc[(cl["denied"] == 1) & (cl["sub"] >= "2026-07-01") & cl["patient_id"].isin(ns_ids), "patient_id"])
    )
    ak["q10_recent_n"] = str(len(recent))
    ak["q10_recent_ids"] = ", ".join(recent)
    return ak


def write_readme(path: Path, result: dict) -> None:
    ak = answer_key(result)
    ctx = dict(result["ctx"])
    ctx.update({f"ak.{k}": v for k, v in ak.items()})
    ctx["seed"] = str(SEED)
    ctx["case_patient"] = result["case"]["patient_id"]
    text = fill((CONTENT_DIR / "README.template.md").read_text(encoding="utf-8"), ctx)
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------------------------------

CSV_TABLES = ["facilities", "providers", "patients", "encounters", "diagnoses", "claims"]


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, lineterminator="\n", float_format="%.2f")


def build_all(out: Path, seed: int = SEED, readme: bool = True) -> dict:
    import yaml

    t = build_clinical(seed)
    validate_clinical(t)
    sc = build_scorecards(t, seed)
    ctx = build_context(t, sc)
    case = pick_form_case(t)
    for sub in ("clinical", "guidelines", "operations"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    for name in CSV_TABLES:
        write_csv(t[name], out / "clinical" / f"{name}.csv")
    write_xlsx(out / "clinical" / "quality_scorecard.xlsx", sc)
    for src, dst in (
        ("hf_discharge_protocol.md", out / "guidelines" / "hf_discharge_transitional_care_protocol.pdf"),
        ("infection_control_sop.md", out / "guidelines" / "infection_control_sop.pdf"),
        ("prior_authorization_policy.md", out / "operations" / "prior_authorization_policy.pdf"),
    ):
        meta, blocks = parse_doc(fill((CONTENT_DIR / src).read_text(encoding="utf-8"), ctx))
        build_pdf(dst, meta, blocks)
    meta, blocks = parse_doc(fill((CONTENT_DIR / "payer_contract_summary.md").read_text(encoding="utf-8"), ctx))
    build_docx(out / "operations" / "payer_contract_summary.docx", meta, blocks)
    spec = yaml.safe_load((CONTENT_DIR / "quality_committee_q2_2026.yaml").read_text(encoding="utf-8"))
    build_pptx(out / "operations" / "quality_committee_q2_2026.pptx", spec, ctx, sc)
    build_form_png(out / "guidelines" / "hf_discharge_checklist_scan.png", case, seed)
    result = {"tables": t, "scorecards": sc, "ctx": ctx, "case": case}
    if readme:
        write_readme(PACK_DIR / "README.md", result)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=PACK_DIR / "files", help="output directory (default: ../files)")
    ap.add_argument("--no-readme", action="store_true", help="do not write README.md")
    args = ap.parse_args()
    res = build_all(args.out, readme=not args.no_readme)
    for k, v in res["tables"].items():
        print(f"{k}: {len(v)} rows")
    case = res["case"]
    print(f"scanned form: {case['patient_id']} ({case['facility_name']}, discharged {case['discharge_date']})")


if __name__ == "__main__":
    main()

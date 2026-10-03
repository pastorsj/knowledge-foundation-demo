# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The cybersecurity-disclosure check: does an answer state Form 8-K Item 1.05's deadline correctly, or say plainly
that the evidence does not give it?

The eCFR's Title 17 holds Form 8-K (17 CFR 249.308) but not the form's item instructions; the regulations corpus
also holds them, from the SEC's 2023 cybersecurity rule (88 FR 51896, Appendix C), but a search may not return that
passage. Either answer is right:

- the deadline itself: four business days (after the company determines the incident is material);
- a flag that the evidence does not give the deadline, in any wording: "the deadline is not stated in the retrieved
  text", "I can't cite the filing deadline from these sources", "the deadline is set in the Form 8-K instructions,
  which aren't in this source", "neither is stated here".

Wrong, whatever else the answer says:

- another deadline: "within five business days", "no later than 30 days" (the Attorney General's delay of up to 30
  days, which the rule allows, is not one);
- denying that there is one: "Item 1.05 has no deadline".

The flag must be about the deadline: a negation in a sentence that names the deadline and the evidence. A negation
anywhere else in the answer ("the filing does not include the attacker's identity") is not a flag.
"""

from __future__ import annotations

import re

NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "fifteen": 15,
    "thirty": 30,
    "sixty": 60,
    "ninety": 90,
}
_N = r"(?P<n>\d+|" + "|".join(NUMBERS) + r")"
# "four business days", "4-business-day deadline", "four (4) business days"
BUSINESS_DAYS = re.compile(_N + r"(?:\s*\(\d+\))?[\s-]+business[\s-]+days?\b", re.I)
# A deadline in days without "business": "within 30 days", "no later than five calendar days"
DAYS_DEADLINE = re.compile(
    r"\b(?:within|no later than|not later than|up to|at most|deadline (?:is|of))\s+(?:the\s+)?"
    + _N
    + r"(?:\s*\(\d+\))?[\s-]+(?:calendar[\s-]+|trading[\s-]+)?days?\b",
    re.I,
)
DEADLINE = re.compile(
    r"deadline|by when|due date|time ?(?:limit|frame)|filing (?:period|window|timing)|how (?:soon|quickly|long)"
    r"|within the period|period specified|business[\s-]+days?|when (?:it|they|a company|companies|the registrant)"
    r" (?:must|has to|have to)",
    re.I,
)
NEGATION = re.compile(
    r"\bnot\b|n['’]t\b|\bno\b|\bnone\b|\bneither\b|\bnor\b|\bcannot\b|\bunable\b|\babsent\b|\bmissing\b"
    r"|\black(?:s|ing)?\b|\bsilent\b|\bunverif|\bunconfirm|\bunsupported|\bomit|\bwithout\b",
    re.I,
)
EVIDENCE = re.compile(
    r"source|evidence|retriev|corpus|\btext\b|document|excerpt|passage|chunk|snapshot|instruction|citation|\bcite"
    r"|quot|verif|confirm|\bfind\b|\bfound\b|\bshow|provided|available|\bhere\b|these (?:rules|sections|results)",
    re.I,
)
# "has no deadline", "there is no deadline", "no specific deadline applies": a denial, not a flag about the evidence
DENIAL = re.compile(
    r"\b(?:has|have|is|are|there is|there's|with|sets?|imposes?)\s+no\s+(?:specific\s+|fixed\s+|filing\s+)?deadline"
    r"|\bno\s+(?:specific\s+|fixed\s+|filing\s+)?deadline\s+(?:applies|exists|is (?:set|imposed|required))",
    re.I,
)
REQUIREMENT = re.compile(
    r"\bmust\b|requir|\bshall\b|deadline|has to|have to|obligat|\bdue\b|\bto (?:file|disclose|report)\b", re.I
)
# What a company did, not what the rule says
OBSERVED = re.compile(r"\bfiled\b|\breported\b|\bdisclosed\b|\bafter (?:its|the|their) (?:incident|attack|event)", re.I)
# The Attorney General's delay of the disclosure (Item 1.05(c)) and its extensions, not the filing deadline: "can be
# delayed by up to 30 days", "for an additional period of up to 60 days"
DELAY = re.compile(r"\bdelay|\bdefer|\bpostpone|\bextend|\bextension|\badditional\b|attorney general", re.I)
DELAY_REACH = 60  # characters before a day count that can make it a delay
DASHES = re.compile("[‐-–−]")


def _number(token: str) -> int:
    return int(token) if token.isdigit() else NUMBERS[token.lower()]


def sentences(text: str) -> list[str]:
    """Split at a sentence end followed by space, and at line breaks; "Item 1.05" and "§ 240.13a-11" stay whole."""
    return [part for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]


def states_four_business_days(text: str) -> bool:
    return any(_number(m["n"]) == 4 for m in BUSINESS_DAYS.finditer(text))


def wrong_deadlines(text: str) -> list[str]:
    """Deadlines other than four business days, or a denial that there is one.

    Only in a sentence that states a requirement and does not report what a company did: "Data I/O filed five days
    after its ransomware event" describes a filing, not the rule. A day count right after a delay or an extension
    ("the Attorney General can delay it by up to 30 days") is part of the rule, not another deadline.
    """
    wrong = [m.group(0) for m in DENIAL.finditer(text)]
    for sentence in sentences(text):
        if not REQUIREMENT.search(sentence) or OBSERVED.search(sentence):
            continue
        for pattern in (BUSINESS_DAYS, DAYS_DEADLINE):
            wrong += [
                m.group(0)
                for m in pattern.finditer(sentence)
                if _number(m["n"]) != 4 and not DELAY.search(sentence[max(0, m.start() - DELAY_REACH) : m.start()])
            ]
    return wrong


def flags_missing_deadline(text: str) -> bool:
    """A sentence that names the deadline, negates it and points at the evidence: "not in the retrieved text"."""
    return any(
        DEADLINE.search(s) and NEGATION.search(s) and EVIDENCE.search(s) and not DENIAL.search(s)
        for s in sentences(text)
    )


def deadline_ok(text: str) -> bool:
    """The answer gives the four-business-day deadline or flags that the evidence lacks it, and states no other."""
    text = DASHES.sub("-", text)
    return (states_four_business_days(text) or flags_missing_deadline(text)) and not wrong_deadlines(text)

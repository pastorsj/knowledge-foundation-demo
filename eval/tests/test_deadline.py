# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The cybersecurity-disclosure deadline check: equivalent wordings pass, wrong deadlines and denials fail."""

import pytest

from demo_eval.deadline import deadline_ok


@pytest.mark.parametrize(
    "text",
    [
        # The deadline itself, in the usual forms
        "A registrant must file within four business days after it determines the incident is material.",
        "Item 1.05 has a 4 business day deadline from the materiality determination.",
        "The four-business-day clock starts at the materiality determination.",
        "Companies must file within four (4) business days of determining materiality.",
        # Flags that the evidence does not give it, as frontier models word them
        "The regulation snapshot I searched doesn't contain the text of Form 8-K Item 1.05, so I can't give you its "
        "disclosure items or its deadline from these sources.",
        "The deadline itself is set in the Form 8-K instructions, which aren't in this source.",
        "Four searches never surfaced the Form 8-K Item 1.05 text or its deadline, so neither is stated here.",
        "So I can't quote, from this evidence, the list of required disclosures or the exact filing deadline.",
        "The text of Form 8-K Item 1.05 isn't in the selected regulation source, so the required disclosures and the "
        "exact deadline can't be cited from it.",
        # ...and plainer wordings
        "The retrieved regulatory text does not state the filing deadline for Item 1.05.",
        "The precise disclosure requirements and deadline remain unconfirmed here.",
        "The retrieved passages do not establish the precise Item 1.05 deadline.",
        "I could not verify the filing deadline from the retrieved evidence.",
        # The deadline stated while flagging that the corpus lacks it (acceptable)
        "Item 1.05 is generally due within four business days, though the retrieved text does not state it.",
        # The Attorney General's delay (Item 1.05(c)) is part of the rule, not another deadline
        "Item 1.05 must be filed within four business days after the registrant determines the incident is material. "
        "The deadline can be delayed by up to 30 days if the U.S. Attorney General determines that disclosure poses "
        "a substantial risk to national security.",
        "Companies must file within four business days; the Attorney General may extend the delay for an additional "
        "period of up to 60 days.",
        # A company's own filing timeline is not the rule
        "Data I/O filed five days after its ransomware event. The deadline is not in the retrieved text.",
        "Conduent filed within 90 days of the incident; the evidence does not show the filing deadline.",
    ],
)
def test_right_answers_pass(text):
    assert deadline_ok(text)


@pytest.mark.parametrize(
    "text",
    [
        # Another deadline, alone or next to a flag
        "Companies must file within five business days of the incident.",
        "The deadline is 30 days after discovery.",
        "Registrants have two business days to file the 8-K.",
        "Item 1.05 requires a filing no later than 10 days after the determination.",
        "The retrieved text does not state the deadline, but it is due within two business days.",
        "Item 1.05 must be filed within 30 days, unless the Attorney General delays it.",
        # Denying that there is a deadline
        "Item 1.05 has no deadline; companies disclose when ready.",
        "There is no filing deadline for Item 1.05 disclosures.",
        # Neither the deadline nor a flag about it
        "Item 1.05 requires disclosure of the nature, scope and timing of the incident and its material impact.",
        # A negation elsewhere in the answer is not a flag about the deadline
        "The filing does not include the attacker's identity. Item 1.05 requires disclosure of the incident's scope.",
        "Coinbase was not verified as the only filer. Item 1.05 covers material cybersecurity incidents.",
    ],
)
def test_wrong_or_silent_answers_fail(text):
    assert not deadline_ok(text)


def test_typographic_dashes_count_as_hyphens():
    assert deadline_ok(
        "Companies must file within four\u2011business\u2011day windows; the four-business-day rule applies."
    )

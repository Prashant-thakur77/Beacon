"""One definition of when spoken words are the phrase that authorises a change.

Two places compare a transcript to a confirmation phrase, and they must agree:

* ``voice_tools.approve_fix`` and its siblings, which decide at the time whether a
  change may be applied at all;
* ``phone.attest``, which afterwards asks the two-channel recording whether the
  caller -- not the agent -- actually said it, and how clearly.

They were written separately and drifted, which is worse than either being wrong
alone. A live Hinglish call to the deployed stack had its approval applied by the
gate and then reported by the audit as "approve_fix ran but its phrase is not on the
caller's channel": the caller's channel re-transcribed as ``अप्रूव फिक्स थ्री।`` and
only the gate knew how to read it. A false alarm on the safety artifact is nearly as
bad as a miss, so both now call in here.
"""

from __future__ import annotations

import re

# English number words only. Hindi "do" (give) and "saat" (seven) must not be
# rewritten, or "isko fix kar do" -- please fix this -- starts reading as a number.
NUMBER_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}

# AssemblyAI transcribes multilingually, and an English consent phrase spoken in an
# Indian accent comes back in Devanagari often enough to matter: live calls to the
# deployed stack produced "अप्रूव फिक्स टू" and "अप्रूव फिक्स थ्री।". Only the consent
# vocabulary is mapped -- the words that can appear in a confirmation phrase -- and
# the exact phrase must still be present afterwards, so an unmapped spelling refuses
# exactly as it would today. This can turn a wrong refusal into a correct
# acceptance; it cannot accept anything that is not the phrase.
#
# Hindi cardinals are deliberately absent. "दो" is both "two" and the imperative
# "give", so "इसको फिक्स कर दो" is *please fix this*, not consent for fix 2.
DEVANAGARI = {
    "अप्रूव": "approve",
    "एप्रूव": "approve",
    "अप्प्रूव": "approve",
    "फिक्स": "fix",
    "फ़िक्स": "fix",
    "वन": "one",
    "टू": "two",
    "थ्री": "three",
    "फोर": "four",
    "फ़ोर": "four",
    "फाइव": "five",
    "सिक्स": "six",
    "सेवन": "seven",
    "एट": "eight",
    "नाइन": "nine",
    "टेन": "ten",
    "ग्रांट": "grant",
    "ग्रान्ट": "grant",
    "कॉन्ट्रैक्ट": "contract",
    "कान्ट्रैक्ट": "contract",
    "कॉन्ट्रेक्ट": "contract",
    "फॉर": "for",
    "डेज": "days",
    "डेज़": "days",
    "ओपन": "open",
    "द": "the",
    "पुल": "pull",
    "रिक्वेस्ट": "request",
    "अंडू": "undo",
    "अन्डू": "undo",
}

_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
# The Devanagari full stop as well as the usual punctuation, so "टू।" still matches.
EDGE = "।॥.,!?;:\"'()-—… "


def words(text: str) -> list[str]:
    """The comparable words of *text*: one script, digits for numbers.

    Unmapped non-ASCII is dropped rather than guessed at, so a phrase that cannot
    be read confidently simply does not match.
    """
    latin = " ".join(
        DEVANAGARI.get(word.strip(EDGE), word)
        for word in text.translate(_DIGITS).split()
    )
    return [
        NUMBER_WORDS.get(w, w)
        for w in re.sub(r"[^a-z0-9\s]", " ", latin.lower()).split()
    ]


def normalise(text: str) -> str:
    """*text* as a single comparable string."""
    return " ".join(words(text))

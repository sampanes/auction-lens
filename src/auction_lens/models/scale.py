"""The one scale every score in this project lives on.

Numbers only, and no dependencies at all, because everything else reads them:
the scorer to clamp to them, configuration to check bars against them, and the
profile readback to explain to an operator what a bar they typed will actually
admit. Written down once so those three cannot disagree.
"""

from __future__ import annotations

# The scale every score lives on. Scoring clamps to it and configuration is
# checked against it, so both read it from the record they are talking about.
LOWEST_SCORE = 0
HIGHEST_SCORE = 100

# What a *wanted* match can actually reach on that scale, which is not all of
# it. An interest starts at the base and gains the bonus when it is also about
# to close; condition penalties only ever take it down. A bargain found on
# price alone has the full scale and can reach 100.
#
# This matters because the operator sets bars in plain numbers. A bar of 85 is
# not "a bit stricter than 80", it is "only wants that are also ending soon",
# and every bar from 88 up means the same thing to a want: nothing. That is
# worth a derived constant rather than a sentence in a comment somewhere,
# because both the scorer and the readback need to agree about it.
BASE_INTEREST_SCORE = 80
ENDING_SOON_BONUS = 7
HIGHEST_INTEREST_SCORE = BASE_INTEREST_SCORE + ENDING_SOON_BONUS

# A fresh auction event deserves to be read before an equally good old one,
# and a moved price deserves nearly the same attention. These affect reading
# order only: observation history is allowed to reorder a report, but never to
# decide whether a listing clears a configured quality bar.
NEW_LISTING_PRIORITY_BONUS = 3
PRICE_CHANGE_PRIORITY_BONUS = 2

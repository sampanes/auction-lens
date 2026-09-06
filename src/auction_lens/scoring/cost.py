"""What a listing actually costs once the provider's fees are applied."""

from __future__ import annotations

from decimal import Decimal

from ..config import EconomicsConfig
from ..fields import CENTS
from ..models import Listing

NO_PREMIUM = Decimal("0")


def estimate_total_cost(listing: Listing, economics: EconomicsConfig) -> Decimal:
    """Add buyer premium, sales tax, and any flat processing fee to the bid.

    A listing may carry its own premium rate because auction events differ; the
    configured rate is only the fallback. Whether the premium is itself taxable
    varies by jurisdiction, so that is configuration rather than arithmetic.
    """
    premium_rate = listing.buyer_premium_rate
    if premium_rate is None:
        premium_rate = economics.default_buyer_premium
    premium = listing.current_bid * premium_rate
    taxable = listing.current_bid + (premium if economics.premium_is_taxable else NO_PREMIUM)
    tax = taxable * economics.sales_tax_rate
    total = listing.current_bid + premium + tax + economics.processing_fee
    return total.quantize(CENTS)

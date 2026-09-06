"""Getting listings into the project, whatever shape they arrive in.

``canonical`` reads the JSON and CSV boundary the rest of the project analyses.
``nellis`` turns one saved provider page into a row of that same shape, so a
scraped lot and a hand-written one are indistinguishable downstream.
"""

from .canonical import load_listings
from .nellis import canonical_grade, read_product_page

__all__ = ["canonical_grade", "load_listings", "read_product_page"]

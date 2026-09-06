# Nellis response fixtures

`browse-shell.html` is a reduced, synthetic-redacted fixture based on an
identified request to the authorized public Nellis `/browse` route on
2026-09-05. It retains the stable acquisition signals needed for future tests:

- server-rendered browse navigation;
- the POST search form and its field names;
- the public client-configuration shape, with live identifiers redacted; and
- the Remix bootstrap/stream boundary.

The initial `/browse` response does not contain auction listings. Listings are
loaded through the public client-side search integration, so this fixture is a
shell fixture rather than a listing-parser fixture. The ignored verbatim capture
stays under `private/cache/` for local investigation and must not be committed.

`product-page.html` is a synthetic product page carrying one redacted product
in the real streamed encoding: a flat array of interned values plus indexes
describing the object graph, exactly as the live site writes it. It was generated
from a verbatim capture rather than hand-written, because a hand-written payload
in plain JSON passes a parser that cannot in fact read a live page. It tests the
ingest adapter without retaining live markup or making a network request.

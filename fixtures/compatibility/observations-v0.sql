CREATE TABLE listings (
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    current_bid TEXT NOT NULL,
    estimated_retail TEXT,
    bid_count INTEGER NOT NULL,
    ends_at TEXT,
    location TEXT NOT NULL,
    conditions TEXT NOT NULL,
    image_url TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (source, listing_id)
);

CREATE TABLE price_history (
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    current_bid TEXT NOT NULL,
    bid_count INTEGER NOT NULL,
    UNIQUE (source, listing_id, observed_at)
);

CREATE TABLE logistics_decisions (
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('feasible', 'infeasible')),
    added_cost TEXT NOT NULL,
    note TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, listing_id)
);

INSERT INTO listings VALUES (
    'synthetic',
    'legacy-auction-1',
    'Legacy Example Speaker',
    'https://example.invalid/auction/legacy-auction-1',
    '10.00',
    '125.00',
    1,
    '2026-08-02T18:00:00+00:00',
    'Example Branch',
    'used',
    'https://example.invalid/photo/legacy-condition.jpg',
    '2026-08-01T12:00:00+00:00',
    '2026-08-01T12:00:00+00:00'
);

INSERT INTO price_history VALUES (
    'synthetic',
    'legacy-auction-1',
    '2026-08-01T12:00:00+00:00',
    '10.00',
    1
);

INSERT INTO logistics_decisions VALUES (
    'synthetic',
    'legacy-auction-1',
    'feasible',
    '12.50',
    'borrow a generic cart',
    '2026-08-01T13:00:00+00:00'
);

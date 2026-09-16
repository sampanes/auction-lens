# Changelog

Auction Lens uses semantic versions for operator-visible releases. Git tags are
the exact source snapshots; this file explains what changed in human terms.

## 0.8.0 - 2026-09-16

- Added optional people and garment-fit rules with validated, human-readable
  size and style names; listings are only excluded when fit is known.
- Read the provider's newer tagged dates and explicit undefined values without
  inventing data that was not present.
- Kept usable pages when one provider page changes shape, while carrying a
  bounded warning through the terminal, email, and webhook reports.
- Clarified quiet scheduled runs and tightened internal type contracts without
  changing the operator-facing command surface.

## 0.7.0 - 2026-09-15

- Reorganized the source tree around auction features and plain workflow names.
- Added behavior, provider, and persisted-file contracts before moving code.
- Split configuration into provider, interest, pricing, logistics, and report
  owners without changing the TOML format.
- Added an explicit provider registry that refuses unsupported page formats.
- Centralized report facts and safe delivery-receipt sequencing across channels.
- Replaced the oversized README with a quick start and question-first code map.

## 0.6.0

- Added a focused profile questionnaire with exact diff preview, atomic save,
  one-step restore, and fail-safe recovery.

## 0.5.0

- Added private per-destination delivery receipts so overlapping runs suppress
  unchanged findings without consuming space meant for new ones.

## 0.4.0

- Added finite interests, explicit fulfillment review, and compatible watchlist
  history so completed wants can retire without guessing what a purchase meant.

Earlier development history remains available in Git.

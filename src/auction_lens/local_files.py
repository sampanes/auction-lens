"""The default local paths shared by setup and the command line.

These names are public behavior, while the files themselves are private runtime
state. Keeping the defaults together prevents setup from creating one file and
the daily command from looking for another.
"""

PROGRAM = "auction-lens"
DEFAULT_CONFIG = "config/local.toml"
DEFAULT_DATABASE = "data/auction-lens.sqlite3"
DEFAULT_ENV_FILE = ".env"
DEFAULT_FEEDBACK_FILE = "private/feedback.json"
DEFAULT_INBOX = "data/inbox/listings.json"
DEFAULT_PROPOSAL_DIR = "private/proposals"
EXAMPLE_CONFIG = "config/providers/nellis.example.toml"

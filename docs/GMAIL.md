# Gmail setup

Auction Lens can send the daily report and the lots explicitly marked
`hunting` through a Gmail account. The Gmail address and password stay in the
ignored `.env` file; no account-specific value belongs in Git-tracked TOML.

## What you need

- A Google account with 2-Step Verification enabled.
- A dedicated Google App Password. Do not use the account's normal password.
- Auction Lens installed by following **Start here** in the main README.

Google's App Passwords page can be difficult to find. Open
[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
sign in, and create an app password named `Auction Lens`. Google commonly
displays the generated 16-character password in spaced groups. The spaces are
formatting, not part of the password.

## Guided setup

The same command that prepares a new machine also asks for the mail settings:

```cmd
.venv\Scripts\auction-lens.exe setup --email
```

It:

1. Creates the ignored config and `.env` if they are not there yet.
2. Asks for the SMTP host, offering `smtp.gmail.com`, and prints Google's
   App Password link when the host is a Gmail one.
3. Asks for the sending address and the recipient, which defaults to the sender.
4. Reads the password without echoing it, and drops the spaces Google displays
   it in -- pasting it exactly as shown is the usual way this step fails.
5. Writes the five settings into `.env`, leaving its comments and every other
   line alone.
6. Reads the configuration back and says whether `[reports.email]` is on.

It never sends a message and never prints the password. The last step reports
rather than edits: `enabled = true` is one line you own, and a setup helper that
rewrites TOML is how a configuration quietly gets corrupted. Port 465 and SSL
are already the defaults, so for Gmail there is nothing else to change.

Nothing here is Gmail-only. Any SMTP host is accepted; Gmail just gets a note
about the thing it alone requires.

The `.env` file is ignored but is still plain text on the local computer. Treat
it like any other credentials file: do not paste it into an issue, chat, log, or
commit.

## Manual setup

To configure the same settings by hand, enable the existing email section in
`config\local.toml`:

```toml
[reports.email]
enabled = true
port = 465
security = "ssl"
```

Then fill these existing lines in `.env`:

```text
AUCTION_LENS_SMTP_HOST=smtp.gmail.com
AUCTION_LENS_SMTP_USERNAME=sender@example.com
AUCTION_LENS_SMTP_PASSWORD=abcdefghijklmnop
AUCTION_LENS_EMAIL_FROM=sender@example.com
AUCTION_LENS_EMAIL_TO=recipient@example.com
```

The password example is deliberately fake. Paste the real App Password locally
as one uninterrupted 16-character value.

## Prove delivery

Use a real lot already present in the watchlist so the message has a useful
photo and link. Mark it `hunting` if needed:

```cmd
.venv\Scripts\auction-lens.exe watch ^
  --source PROVIDER_NAME ^
  --listing-id LISTING_ID ^
  --verdict hunting
```

Then email only the selected lots:

```cmd
.venv\Scripts\auction-lens.exe watchlist --verdict hunting --email
```

Confirm that the message arrives, the card is readable, and its photo and
listing link work. After that proof, `scripts\run-daily.cmd` is the
ready-to-schedule Windows entry point. If no chat webhook is configured, remove
`--webhook` from that script as its own comment instructs.

## Troubleshooting

- **App Passwords is unavailable:** confirm 2-Step Verification is enabled.
  Google may also disable App Passwords for some managed or security-restricted
  accounts.
- **Authentication fails:** generate a new App Password and run the setup script
  again. Do not substitute the normal Google account password.
- **Password has spaces:** the setup script removes them. For manual setup,
  remove all display spaces before saving `.env`.
- **Email reporting is disabled:** check that `[reports.email]` contains
  `enabled = true` in `config\local.toml`.
- **No useful cards appear:** add or update a watchlist entry to the `hunting`
  verdict before running the watchlist email command.

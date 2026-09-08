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

## Guided setup on Windows

From the repository root, run:

```cmd
scripts\setup-gmail.cmd
```

The script:

1. Creates the normal ignored config and `.env` files if needed.
2. Prompts for the sending Gmail address and report recipient.
3. Reads the App Password without displaying it.
4. Removes Google's formatting spaces automatically.
5. Writes the five SMTP settings to `.env`, enables `[reports.email]`, and sets
   Gmail's SSL/465 transport in `config\local.toml`.
6. Validates the result without printing any credential value.

It does not send a message. Re-run only the validation at any time with:

```cmd
scripts\setup-gmail.cmd -CheckOnly
```

The CMD file is only the convenient entry point. It delegates to
`setup-gmail.ps1`, because PowerShell can hide the password while it is entered
and update the local files without echoing credential values.

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

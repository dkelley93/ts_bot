# TS Store Bot

Two small scripts to monitor the Taylor Swift store and send email notifications.

## Scripts

### 1) `ts_bot.py` (main)
Checks one or more product pages and sends an email when an item appears available. It is designed to be run periodically (for example via `cron`).

- Uses Playwright (Chromium) to load each product page.
- Checks the DOM for the add-to-cart button state.
- When an item is available, it captures a screenshot and attaches it to the email.
- Uses a local state file to avoid repeatedly notifying if an item was found recently.

### 2) `new_merch.py`
Scrapes the Taylor Swift “all merch” collection page and compares the displayed product count to detect when the number of items changes.

## Tested Python version

- Python **3.13**

## Dependencies

Install dependencies for `ts_bot.py`:

```bash
python3 -m pip install -r requirements-ts_bot.txt
```

Playwright requires a browser install (Chromium is enough):

```bash
python3 -m playwright install chromium
```

Install dependencies for `new_merch.py`:

```bash
python3 -m pip install -r requirements-new_merch.txt
```

## Configuration (`ts_bot.py`)

`ts_bot.py` supports **Option 2** configuration:

- **`config.toml`** (local file, recommended, gitignored)
- **Environment variables** (override values in `config.toml`)

A template is provided at `config.example.toml`. Copy it to `config.toml`:

```bash
cp config.example.toml config.toml
```

### Required configuration fields

You must provide:

- `sender_email`
- `email_addresses`
- `links` (or `url`)
- `smtp_host`
- `smtp_port`
- `smtp_user`
- `smtp_password`

### `config.toml` fields

- `links` (list of product URLs)
- `url` (single URL convenience; used if `links` is empty)
- `sender_email` (email shown in the From header)
- `email_addresses` (list of recipients)
- `smtp_host`
- `smtp_port`
- `smtp_user` (SMTP auth user; can be different from `sender_email`)
- `smtp_password` (recommended to provide via ENV instead)

### Environment variables

Environment variables override `config.toml`:

- `TSB_SENDER_EMAIL`
- `TSB_EMAIL_ADDRESSES` (comma-separated)
- `TSB_LINKS` (comma-separated)
- `TSB_URL`
- `TSB_SMTP_HOST`
- `TSB_SMTP_PORT`
- `TSB_SMTP_USER`
- `TSB_SMTP_PASSWORD`

Example:

```bash
export TSB_SMTP_PASSWORD="your-app-password"
```

## Files and directories created

Running `ts_bot.py` can create:

- `ts_bot_state.txt`
  - Stored next to the script.
  - Tracks the last-known status (`FOUND` / `NOT FOUND`) per URL.
- `screenshots/`
  - Screenshot PNGs are written here temporarily.
  - Files are deleted after the email is sent (or after a send attempt).

## Running `ts_bot.py`

From the repo directory:

```bash
python3 ts_bot.py
```

### Running via cron (example)

Example: run every 5 minutes.

1) Ensure your environment variables are available to cron.
   - Common approach: put exports in a file (for example `~/.tsb_env`) and source it.

2) Edit your crontab:

```bash
crontab -e
```

3) Add a line like this (update paths as needed):

```cron
*/5 * * * * /bin/zsh -lc 'source ~/.tsb_env && /usr/bin/python3 /ABS/PATH/TO/ts_bot.py >> /ABS/PATH/TO/ts_bot_cron.log 2>&1'
```

Notes:

- Use absolute paths for both Python and the script.
- The `-lc` ensures your shell runs as a login shell so `source` works as expected.

## Notes on `new_merch.py`

- `new_merch.py` is currently a standalone script with hardcoded values.
- **Before pushing to a public repo, remove any hardcoded SMTP credentials** from `new_merch.py`.
  - Recommended: refactor it to use the same `config.toml`/ENV approach as `ts_bot.py`.

Run it directly:

```bash
python3 new_merch.py
```

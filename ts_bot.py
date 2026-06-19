"""ts_bot.py

Monitors one or more Taylor Swift store product pages and notifies a list of
recipients via email when an item appears available.

Configuration is loaded from an optional local `config.toml` (recommended to be
gitignored) and can be overridden via environment variables.

When availability is detected, the script captures a Playwright screenshot and
attaches it to the outgoing email.
"""

import datetime
import html as html_lib
import smtplib
import ssl
import os
import re
import tomllib
from urllib.parse import urlparse
from email.message import EmailMessage
from playwright.sync_api import sync_playwright

email_addresses = []
sender_email = ''
links = []

smtp_host = ''
smtp_port = 465
smtp_user = ''
smtp_password = ''

STATE_FILE_NAME = 'ts_bot_state.txt'
DATETIME_FORMAT = '%Y-%m-%d %H:%M:%S'
FOUND_WINDOW_MINUTES = 10


def _parse_csv(value):
    """Parse a comma-separated string into a list of non-empty, stripped values."""
    if value is None:
        return None

    items = [v.strip() for v in value.split(',')]
    items = [v for v in items if v]
    return items


def _load_toml_config():
    """Load `config.toml` from the script directory (if present).

    Returns:
        dict: Parsed TOML content, or an empty dict if no config file exists.
    """
    path = os.path.join(os.path.dirname(__file__), 'config.toml')
    if not os.path.exists(path):
        return {}

    with open(path, 'rb') as f:
        return tomllib.load(f)


def _env(name):
    """Return an environment variable value, treating empty strings as missing."""
    value = os.environ.get(name)
    return value if value is not None and value != '' else None


def load_settings():
    """Load configuration from config.toml and environment variables.

    Environment variables override config file values.

    Returns:
        dict: Normalized settings used by the script.

    Raises:
        RuntimeError: If required settings are missing.
    """
    cfg = _load_toml_config()

    settings = {
        'sender_email': cfg.get('sender_email', ''),
        'email_addresses': cfg.get('email_addresses', []),
        'links': cfg.get('links', []),
        'url': cfg.get('url'),
        'smtp_host': cfg.get('smtp_host', ''),
        'smtp_port': int(cfg.get('smtp_port', 465)),
        'smtp_user': cfg.get('smtp_user', ''),
        'smtp_password': cfg.get('smtp_password', ''),
    }

    env_sender_email = _env('TSB_SENDER_EMAIL')
    if env_sender_email is not None:
        settings['sender_email'] = env_sender_email

    env_emails = _parse_csv(_env('TSB_EMAIL_ADDRESSES'))
    if env_emails is not None:
        settings['email_addresses'] = env_emails

    env_links = _parse_csv(_env('TSB_LINKS'))
    if env_links is not None:
        settings['links'] = env_links

    env_url = _env('TSB_URL')
    if env_url is not None:
        settings['url'] = env_url

    env_smtp_host = _env('TSB_SMTP_HOST')
    if env_smtp_host is not None:
        settings['smtp_host'] = env_smtp_host

    env_smtp_port = _env('TSB_SMTP_PORT')
    if env_smtp_port is not None:
        settings['smtp_port'] = int(env_smtp_port)

    env_smtp_user = _env('TSB_SMTP_USER')
    if env_smtp_user is not None:
        settings['smtp_user'] = env_smtp_user

    env_smtp_password = _env('TSB_SMTP_PASSWORD')
    if env_smtp_password is not None:
        settings['smtp_password'] = env_smtp_password

    if (not settings['links']) and settings.get('url'):
        settings['links'] = [settings['url']]

    missing = []
    if not settings['sender_email']:
        missing.append('sender_email (TSB_SENDER_EMAIL or config.toml)')
    if not settings['email_addresses']:
        missing.append('email_addresses (TSB_EMAIL_ADDRESSES or config.toml)')
    if not settings['links']:
        missing.append('links/url (TSB_LINKS/TSB_URL or config.toml)')
    if not settings['smtp_host']:
        missing.append('smtp_host (TSB_SMTP_HOST or config.toml)')
    if not settings['smtp_port']:
        missing.append('smtp_port (TSB_SMTP_PORT or config.toml)')
    if not settings['smtp_user']:
        missing.append('smtp_user (TSB_SMTP_USER or config.toml)')
    if not settings['smtp_password']:
        missing.append('smtp_password (TSB_SMTP_PASSWORD or config.toml)')

    if missing:
        raise RuntimeError('Missing required configuration: ' + '; '.join(missing))

    return settings


_settings = load_settings()
email_addresses = _settings['email_addresses']
sender_email = _settings['sender_email']
links = _settings['links']
smtp_host = _settings['smtp_host']
smtp_port = _settings['smtp_port']
smtp_user = _settings['smtp_user']
smtp_password = _settings['smtp_password']


def _state_file_path():
    """Return the filesystem path for the state file."""
    return os.path.join(os.path.dirname(__file__), STATE_FILE_NAME)


def _parse_state_line(line):
    """Parse a single line from the state file.

    Args:
        line (str): A raw state line formatted as: "<datetime>, <url>, <status>".

    Returns:
        tuple[datetime.datetime, str, str] | None:
            Parsed (dt, url, status) tuple if valid; otherwise None.
    """
    parts = [p.strip() for p in line.split(',', 2)]
    if len(parts) != 3:
        return None

    dt_raw, url, status_raw = parts
    status = status_raw.strip().upper()
    if status not in {'FOUND', 'NOT FOUND'}:
        return None

    try:
        dt = datetime.datetime.strptime(dt_raw, DATETIME_FORMAT)
    except ValueError:
        try:
            dt = datetime.datetime.fromisoformat(dt_raw)
        except ValueError:
            return None

    return dt, url, status


def load_state():
    """Load previously recorded availability results from the state file.

    Returns:
        dict[str, tuple[datetime.datetime, str]]:
            Mapping of url -> (timestamp, status).
    """
    state = {}
    path = _state_file_path()
    if not os.path.exists(path):
        return state

    try:
        with open(path, 'r', encoding='utf-8') as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                parsed = _parse_state_line(line)
                if not parsed:
                    continue
                dt, url, status = parsed
                state[url] = (dt, status)
    except OSError as e:
        log(f"Error reading state file: {e}")

    return state


def write_state(state_by_url):
    """Persist the current availability status for each configured link.

    Args:
        state_by_url (dict[str, tuple[datetime.datetime, str]]):
            Mapping of url -> (timestamp, status).
    """
    path = _state_file_path()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            for url in links:
                dt, status = state_by_url.get(url, (datetime.datetime.now(), 'NOT FOUND'))
                dt_str = dt.strftime(DATETIME_FORMAT)
                f.write(f"{dt_str}, {url}, {status}\n")
    except OSError as e:
        log(f"Error writing state file: {e}")


def main():
    """Run a single pass over all configured links.

    The state file is used to suppress repeated notifications when an item was
    found recently.
    """
    now = datetime.datetime.now()
    state = load_state()
    new_state = {}
    email_sent = False

    for link in links:
        previous = state.get(link)
        if previous:
            previous_dt, previous_status = previous
            if (
                    previous_status == 'FOUND'
                    and (now - previous_dt) < datetime.timedelta(minutes=FOUND_WINDOW_MINUTES)
            ):
                log(f"Skipping check (FOUND within last {FOUND_WINDOW_MINUTES} minutes): {link}")
                new_state[link] = (previous_dt, 'FOUND')
                continue

        available, screenshot_path = check_availability(link)
        if available:
            log(f"Item available at {link}")

            if (not previous) or previous[1] != 'FOUND':
                if not email_sent:
                    send_email(link, screenshot_path)
                    email_sent = True

            new_state[link] = (now, 'FOUND')
        else:
            log(f"Item not available at {link}")
            new_state[link] = (now, 'NOT FOUND')

    write_state(new_state)


def _screenshots_dir():
    """Return the directory path where screenshots are written."""
    return os.path.join(os.path.dirname(__file__), 'screenshots')


def _safe_filename(text):
    """Convert text into a filesystem-safe filename fragment."""
    text = re.sub(r'[^a-zA-Z0-9._-]+', '_', text).strip('_')
    return text or 'screenshot'


def _screenshot_path(url):
    """Build a timestamped screenshot path for a given URL."""
    parsed = urlparse(url)
    base = _safe_filename(parsed.path.strip('/').split('/')[-1] or parsed.netloc)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    return os.path.join(_screenshots_dir(), f"{base}_{ts}.png")


def check_availability(url):
    """Check store-page availability using Playwright DOM inspection.

    Args:
        url (str): Product page URL.

    Returns:
        tuple[bool, str | None]:
            (available, screenshot_path). A screenshot is only captured when
            available is True.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            page.goto(url, wait_until='domcontentloaded', timeout=30_000)

            submit_button = page.locator('button[name="add"]')
            if submit_button.count() == 0:
                log("Submit button not found")
                return False, None

            button_span = submit_button.locator('span')
            if button_span.count() == 0:
                log("Submit button span not found")
                return False, None

            button_span_text = button_span.first.inner_text().strip()
            log("button_span_text: " + button_span_text)

            if button_span_text.strip().lower() != "not available":
                log("Item available")
                os.makedirs(_screenshots_dir(), exist_ok=True)
                screenshot_path = _screenshot_path(url)
                page.screenshot(path=screenshot_path, full_page=True)
                return True, screenshot_path

            return False, None
        finally:
            browser.close()


def send_email(url, screenshot_path):
    """Send a notification email (plain text + HTML) with optional screenshot.

    Args:
        url (str): Product URL to include in the email.
        screenshot_path (str | None): Path to a PNG to attach; may be None.
    """
    msg = EmailMessage()
    msg['Subject'] = 'Taylor Swift items available!'
    msg['From'] = sender_email
    msg['To'] = ','.join(email_addresses)

    safe_url = html_lib.escape(url)
    msg.set_content(f"Item available: {url}")
    msg.add_alternative(
        f"""<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Taylor Swift items available</title>
  </head>
  <body style=\"margin:0;padding:0;background:#fff7fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;\">
    <div style=\"max-width:640px;margin:0 auto;padding:24px;\">
      <div style=\"background:#ffe0ef;border:1px solid #ffb6d5;border-radius:16px;padding:22px;box-shadow:0 8px 24px rgba(255,182,213,.25);\">
        <div style=\"font-size:20px;font-weight:700;color:#7a1f4a;margin-bottom:8px;\">It’s happening!</div>
        <div style=\"font-size:14px;line-height:1.6;color:#5b1b36;\">
          A Taylor Swift item looks <b>available</b> right now.
        </div>

        <div style=\"margin-top:16px;\">
          <a href=\"{safe_url}\" style=\"display:inline-block;background:#ff6fae;color:#ffffff;text-decoration:none;padding:12px 16px;border-radius:999px;font-weight:700;\">Open product page</a>
        </div>

        <div style=\"margin-top:18px;font-size:12px;color:#7a1f4a;opacity:.9;\">
          I attached a screenshot for extra peace of mind.
        </div>
      </div>

      <div style=\"text-align:center;margin-top:14px;font-size:12px;color:#a04b74;\">
        Sent by your TS bot
      </div>
    </div>
  </body>
</html>""",
        subtype='html',
    )

    if screenshot_path and os.path.exists(screenshot_path):
        with open(screenshot_path, 'rb') as f:
            msg.add_attachment(
                f.read(),
                maintype='image',
                subtype='png',
                filename=os.path.basename(screenshot_path),
            )

    # Set up the secure context
    context = ssl.create_default_context()

    # Connect to the SMTP server and send the email
    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls(context=context)
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
        log("Email sent successfully!")
    except Exception as e:
        log(f"Error sending email: {e}")
    finally:
        if screenshot_path and os.path.exists(screenshot_path):
            try:
                os.remove(screenshot_path)
            except OSError:
                pass


def log(text):
    """Print a timestamped log line to stdout."""
    now = datetime.datetime.now()
    print(f"[{now}]: {text}")


if __name__ == "__main__":
    main()

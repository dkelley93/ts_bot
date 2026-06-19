"""new_merch.py

Monitors the Taylor Swift store "all merch" page by reading the displayed
product count and notifying recipients when the count changes.

Configuration is loaded from an optional local `config.toml` (recommended to be
gitignored) and can be overridden via environment variables.

TODO: Migrate BeautifulSoup parsing to Playwright for consistency with
`ts_bot.py`.
"""

import os
import smtplib
import ssl
import time
import tomllib

import requests
from bs4 import BeautifulSoup
from email.message import EmailMessage

stop = False
email_addresses = []
sender_email = ''

smtp_host = ''
smtp_port = 465
smtp_user = ''
smtp_password = ''

url = ''

items_available = 0


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
        'url': cfg.get('new_merch_url', cfg.get('url', '')),
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

    env_url = _env('TSB_NEW_MERCH_URL')
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

    missing = []
    if not settings['sender_email']:
        missing.append('sender_email (TSB_SENDER_EMAIL or config.toml)')
    if not settings['email_addresses']:
        missing.append('email_addresses (TSB_EMAIL_ADDRESSES or config.toml)')
    if not settings['url']:
        missing.append('url (TSB_NEW_MERCH_URL or config.toml)')
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
url = _settings['url']
smtp_host = _settings['smtp_host']
smtp_port = _settings['smtp_port']
smtp_user = _settings['smtp_user']
smtp_password = _settings['smtp_password']


def main_loop():
    """Run the monitor loop forever until interrupted."""
    while not stop:
        check_product_count()
        time.sleep(60)


def check_product_count():
    """Fetch the merch collection page and compare the displayed product count.

    If the count changes, an email is sent. If the count cannot be found, the
    script sends an email and stops.
    """
    global items_available, stop

    print("Checking Product Count...")

    # TODO: Migrate from BeautifulSoup -> Playwright for consistency with ts_bot.py and
    # to support dynamic client-rendered pages.

    response = requests.get(url)
    html_content = response.text
    soup = BeautifulSoup(html_content, 'html.parser')
    product_count = soup.find('span', {'id': 'ProductCountDesktop'})

    if product_count is None:
        send_email("Cannot find Product Count.", "Cannot find Product Count. Shutting down...")
        stop = True
        return

    count_text = product_count.text.replace("Showing", "").replace("Results", "").strip()
    count = int(count_text)

    if count == items_available:
        print(f"Product count has not changed: {count}")
        return

    if count > items_available:
        print(f"Merch count has increased from {items_available} to {count}")
        send_email("New Merch Available!", f"Merch count updated from {items_available} to {count}")
    else:
        print(f"Merch count has decreased from {items_available} to {count}")
        send_email("Merch Removed!", f"Merch count updated from {items_available} to {count}")

    items_available = count


def send_email(subject, content):
    """Send a plain-text notification email via configured SMTP settings."""
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = ','.join(email_addresses)
    msg.set_content(content)

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
        print("Email sent successfully!")
    except Exception as e:
        print(f"Error sending email: {e}")


if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("Stopping!")
        stop = True

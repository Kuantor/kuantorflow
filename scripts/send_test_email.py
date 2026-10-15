"""Send one test email to the admin through the site's own mailer (#591).

    venv/bin/python scripts/send_test_email.py

Uses exactly what the notices will use: `notify.send_admin_email()` and the
`SMTP_*` / `NOTIFY_EMAIL` settings in this checkout's `.env`. So "it arrived"
means the settings are right, and the first real notice will arrive too.

Prints what happened; when it is not `sent`, the reason is the last line of
`logs/notify.log` (a missing setting, a refused login, a timeout). Never
prints the password. Not part of a deploy; run it after changing the settings.
"""

import argparse
import socket
import sys
from pathlib import Path

# `scripts/` is what `sys.path` gets when this file is run, not the repo
# root -- so the app's modules below need the root put there first (#442).
import _bootstrap  # noqa: F401

import applog
import notify

MESSAGES = {
    "sent": "SENT to {to}. Check the inbox, and the spam folder.",
    "skipped": "NOT SENT: the mailer is not configured (SMTP_HOST, SMTP_USER, "
               "SMTP_PASSWORD, NOTIFY_EMAIL or ADMIN_EMAILS).",
    "dry-run": "NOT SENT: NOTIFY_DRY_RUN is on, so it was only logged.",
    "failed": "NOT SENT: the mail server refused or could not be reached.",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Send one test email to the admin with the .env mail "
                    "settings (#591).")
    parser.parse_args(argv)

    to = notify.recipient() or "(no recipient)"
    body = (f"If you can read this, KuantorFlow on {socket.gethostname()} "
            "can email you, and the admin notices will arrive here.")
    result = notify.send_admin_email(notify.KIND_TEST,
                                     "KuantorFlow: test email", body)

    print(MESSAGES.get(result, result).format(to=to))
    if result != "sent":
        print(f"The reason is the last line of {Path(applog.LOGS_DIR) / 'notify.log'}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

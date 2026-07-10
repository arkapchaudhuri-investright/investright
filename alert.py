#!/usr/bin/env python
"""Email an alert when a systemd unit fails. Wired up via `OnFailure=`.

    python alert.py investright-refresh.service

A nightly job that fails silently is worse than no nightly job: the site keeps
serving yesterday's numbers and nobody knows. systemd already notices the
failure — this just tells a human, reusing the SMTP relay mailer.py needs for
password resets.

Sends to ALERT_EMAIL, else SMTP_USER. Does nothing (exit 0) when no relay is
configured: an alerting system that itself explodes is not an improvement.
"""
import os
import subprocess
import sys

import digest  # noqa: F401 — its import loads .env into the environment
import mailer

LOG_LINES = 30


def _recent_log(unit):
    """The tail of the unit's journal, so the email says what actually broke."""
    try:
        out = subprocess.run(
            ["journalctl", "-u", unit, "-n", str(LOG_LINES), "--no-pager"],
            capture_output=True, text=True, timeout=20, check=False)
        return out.stdout.strip() or "(journal empty)"
    except Exception as exc:                        # noqa: BLE001
        return f"(couldn't read the journal: {exc.__class__.__name__}: {exc})"


def main():
    unit = sys.argv[1] if len(sys.argv) > 1 else "an InvestRight unit"
    to = os.environ.get("ALERT_EMAIL") or os.environ.get("SMTP_USER")
    if not (to and mailer.enabled()):
        print("[alert] no SMTP relay configured — nothing to send", flush=True)
        return 0

    body = (f"{unit} failed on the InvestRight VM.\n\n"
            f"The site keeps serving the last good data, so nothing is down — "
            f"but whatever this job does has stopped happening.\n\n"
            f"Last {LOG_LINES} journal lines:\n\n{_recent_log(unit)}\n\n"
            f"Investigate:\n"
            f"    ssh ubuntu@investright.us\n"
            f"    systemctl status {unit}\n"
            f"    journalctl -u {unit} -n 100\n")
    ok = mailer.send(to, f"[InvestRight] {unit} failed", body)
    print(f"[alert] {'sent' if ok else 'FAILED to send'} alert for {unit} to {to}",
          flush=True)
    return 0            # never fail: this runs *because* something already failed


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env bash
# Rebuild an InvestRight host from nothing. Written for the 2026-08 migration
# off a Free-Trial VM.Standard.E5.Flex onto an Always-Free VM.Standard.A1.Flex,
# but it is not migration-specific — it is the missing "how was this server
# built" script, and it works on any fresh Ubuntu 22.04/24.04 box.
#
# A1 is aarch64 while the old box was x86_64. Nothing here is architecture
# specific: SQLite files are portable across CPUs and endianness, the DB-IP
# .mmdb databases likewise, and every Python dependency publishes aarch64
# wheels. The venv is rebuilt from requirements.txt rather than copied, because
# a venv contains compiled x86 objects that would not run on ARM.
#
# Usage, as ubuntu@ on the NEW host:
#   git clone https://github.com/arkapchaudhuri-investright/investright.git /tmp/ir
#   sudo bash /tmp/ir/provision.sh
# Then copy .env and the database across (the script prints exactly how).
set -euo pipefail

APP=/opt/investright
REPO=https://github.com/arkapchaudhuri-investright/investright.git
RUN_USER=ubuntu

say() { printf "\n\033[1m==> %s\033[0m\n" "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

say "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get -qq update
apt-get -qq -y install python3 python3-venv python3-pip git curl openssl \
    debian-keyring debian-archive-keyring apt-transport-https ca-certificates

say "Caddy"
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get -qq update && apt-get -qq -y install caddy
fi

say "Application code"
if [ -d "$APP/.git" ]; then
  git -C "$APP" pull --ff-only
else
  mkdir -p "$APP" && chown "$RUN_USER:$RUN_USER" "$APP"
  sudo -u "$RUN_USER" git clone "$REPO" "$APP"
fi

say "Python environment (rebuilt, never copied — the old one holds x86 objects)"
sudo -u "$RUN_USER" python3 -m venv "$APP/.venv"
sudo -u "$RUN_USER" "$APP/.venv/bin/pip" -q install --upgrade pip
sudo -u "$RUN_USER" "$APP/.venv/bin/pip" -q install -r "$APP/requirements.txt"
sudo -u "$RUN_USER" "$APP/.venv/bin/python" -c "
import flask, yfinance, gunicorn, maxminddb, openpyxl, platform
print('   ok on', platform.machine(), '· flask', flask.__version__)"

say "systemd units and Caddy config"
install -m 644 "$APP"/systemd/investright*.service "$APP"/systemd/investright*.timer /etc/systemd/system/
mkdir -p /etc/systemd/system/investright-refresh.service.d
install -m 644 "$APP/systemd/refresh-onfailure.conf" \
    /etc/systemd/system/investright-refresh.service.d/onfailure.conf
# Multi-site layout: the Caddyfile only imports sites/, so a second site is a
# new file rather than an edit to the config the first site depends on.
mkdir -p /etc/investright/sites
install -m 644 "$APP/systemd/Caddyfile" /etc/investright/Caddyfile
install -m 644 "$APP"/systemd/sites/*.caddy /etc/investright/sites/
systemctl daemon-reload

say "Firewall (Oracle images default to DROP on everything but 22)"
iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || \
    iptables -I INPUT 1 -p tcp --dport 80 -j ACCEPT
iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || \
    iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
netfilter-persistent save >/dev/null 2>&1 || apt-get -qq -y install iptables-persistent

mkdir -p "$APP/data" && chown -R "$RUN_USER:$RUN_USER" "$APP"

cat <<EOS

$(say "Provisioned. Two things must still come from the old host:")

  1. Secrets — .env is deliberately NOT in git:
       scp ubuntu@OLD_IP:/opt/investright/.env  /tmp/env
       sudo install -o $RUN_USER -g $RUN_USER -m 600 /tmp/env $APP/.env && rm /tmp/env

  2. The database — take a fresh consistent snapshot, do not cp a live file:
       ssh ubuntu@OLD_IP '/opt/investright/.venv/bin/python /opt/investright/manage.py backup --apply'
       scp ubuntu@OLD_IP:/opt/investright/data/backups/\$(ssh ubuntu@OLD_IP 'ls -t /opt/investright/data/backups | head -1') /tmp/db.gz
       gunzip -c /tmp/db.gz | sudo -u $RUN_USER tee $APP/data/investright.db >/dev/null

  Then start it:
       sudo systemctl enable --now investright caddy
       sudo systemctl enable --now investright-refresh.timer investright-backup.timer \\
                                  investright-weekly.timer investright-symbols.timer
       curl -s -o /dev/null -w '%{http_code}\\n' http://localhost:8700/

  The two geo databases (~135 MB) are not worth copying — analytics.py
  re-downloads them on its first nightly run.

  DNS last, once the new host answers on its own IP.

$(say "Hosting a second site later")

  This host is laid out for more than one site. To add one:
    • give the app its own port and /opt/<app> directory
    • drop a <domain>.caddy file in /etc/investright/sites/
      (see $APP/systemd/sites/README.md for the template and port list)
    • point DNS at this host FIRST, then: sudo systemctl reload caddy

  Certificates are automatic per hostname. Apps bind 127.0.0.1 only — Caddy is
  the sole process facing the internet, which is why just 80 and 443 are open.
EOS

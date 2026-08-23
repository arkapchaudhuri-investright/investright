# One file per site

Each `*.caddy` file here is one website. `../Caddyfile` imports the whole
directory, so adding a site never means editing a file another site depends on.

To add one — say Career OS on its own subdomain:

```caddy
# career.investright.us.caddy
career.investright.us {
	reverse_proxy 127.0.0.1:8600
}
```

Then point a DNS A record at this host and reload:

```sh
sudo cp career.investright.us.caddy /etc/investright/sites/
sudo systemctl reload caddy
```

Caddy requests the certificate on the first request, so HTTPS needs no setup —
but DNS must resolve here *before* you reload, or the certificate request fails
and Caddy backs off for a while.

## Ports

One app per port, so nothing collides. Keep this list current:

| Port | App          |
|------|--------------|
| 8700 | InvestRight  |
| 8600 | Career OS    |

Apps bind to `127.0.0.1` only — Caddy is the single process facing the
internet, which is why the firewall opens 80 and 443 and nothing else.

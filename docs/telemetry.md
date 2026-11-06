# Telemetry

vibe-seller sends anonymous, aggregate usage events to help maintainers
understand adoption and reliability. **No business content is sent**:
no task titles, store/product names, error message text, screenshots,
chat content, email addresses, or credentials. Identification is by
an anonymous UUID
(`~/.vibe-seller/data/install_id`), never by user or email. Seats on a
multi-user install are counted with a one-way pseudonym
(`sha256(install_id:user_id)`, truncated) — the raw user id never
leaves the machine.

**One deliberate exception: custom AI provider labels.** Preset
providers are reported as a fixed enum (`deepseek`, `minimax`, …). For
a provider the user configured themselves, the name they gave it and
the HOSTNAME of its base URL are reported (e.g. `api.example.com`) so
the maintainers can see which custom endpoints people actually use.
The full base URL, its scheme, port, path, any key embedded in it, the
model, and the API key are never sent; a bare IP or `localhost` is
reported as `local`.

## Opt out

Pick whichever fits:

- **Settings UI**: Settings → AI Agent → toggle "Anonymous usage
  telemetry" off.
- **Env var**: `VIBE_SELLER_TELEMETRY=0 ./start.sh`.

When off, no requests leave the machine.

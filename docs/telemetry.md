# Telemetry

vibe-seller sends anonymous, aggregate usage events to help maintainers
understand adoption and reliability. **No business content is sent**:
no task titles, store/product names, error message text, screenshots,
chat content, AI profile names, email addresses, or credentials.
Identification is by an anonymous UUID
(`~/.vibe-seller/data/install_id`), never by user or email.

## Opt out

Pick whichever fits:

- **Settings UI**: Settings → AI Agent → toggle "Anonymous usage
  telemetry" off.
- **Env var**: `VIBE_SELLER_TELEMETRY=0 ./start.sh`.

When off, no requests leave the machine.

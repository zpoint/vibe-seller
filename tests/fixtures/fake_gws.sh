#!/usr/bin/env bash
# Fake `gws` binary for tests.
#
# Responds to:
#   gws --version              → prints version
#   gws auth status            → exit 0 (or 1 if GWS_FAKE_AUTH=fail)
#   gws generate-skills --output-dir <dir>
#       → writes 19 allowed skill dirs + junk (gws-chat, persona-hr,
#         recipe-foo) so the subset filter and cross-ref rewrite
#         get exercised.

set -euo pipefail

# Version ─────────────────────────────────────────────
if [[ "${1:-}" == "--version" ]]; then
  echo "gws 0.22.5 (fake)"
  exit 0
fi

# auth status ─────────────────────────────────────────
#
# Real gws auth status ALWAYS exits 0 — usability is communicated
# via the JSON body (auth_method, encryption_valid, etc.). We mirror
# that here so check_status() is exercised via the JSON parser, not
# the exit code. Cases (via GWS_FAKE_AUTH):
#   ok              → logged_in
#   encryption_bad  → encrypted creds present but can't decrypt
#                     (typical when the keyring moved hosts)
#   fail            → no credentials at all (auth_method=none)
if [[ "${1:-}" == "auth" && "${2:-}" == "status" ]]; then
  case "${GWS_FAKE_AUTH:-ok}" in
    fail)
      cat <<'JSON'
{
  "auth_method": "none",
  "storage": "none",
  "keyring_backend": "keyring",
  "encrypted_credentials_exists": false,
  "plain_credentials_exists": false,
  "token_cache_exists": false,
  "client_config_exists": false
}
JSON
      exit 0
      ;;
    encryption_bad)
      cat <<'JSON'
{
  "auth_method": "oauth2",
  "storage": "encrypted",
  "keyring_backend": "keyring",
  "encrypted_credentials_exists": true,
  "plain_credentials_exists": false,
  "token_cache_exists": false,
  "encryption_valid": false,
  "encryption_error": "Could not decrypt. May have been created on a different machine.",
  "project_id": "fake-project",
  "config_client_id": "51610964...com"
}
JSON
      exit 0
      ;;
    *)
      cat <<'JSON'
{
  "auth_method": "oauth2",
  "storage": "encrypted",
  "keyring_backend": "keyring",
  "encrypted_credentials_exists": true,
  "plain_credentials_exists": false,
  "token_cache_exists": true,
  "encryption_valid": true,
  "project_id": "fake-project",
  "client_id": "51610964...com",
  "has_refresh_token": true
}
JSON
      exit 0
      ;;
  esac
fi

# generate-skills ─────────────────────────────────────
if [[ "${1:-}" == "generate-skills" ]]; then
  out_dir=""
  shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --output-dir)
        out_dir="$2"
        shift 2
        ;;
      *)
        shift
        ;;
    esac
  done
  if [[ -z "$out_dir" ]]; then
    echo "fake_gws: --output-dir required" >&2
    exit 2
  fi
  # Mirror real gws >=0.22: --output-dir must be a relative path.
  # Without this, callers that pass absolute tmpdirs (from
  # tempfile.TemporaryDirectory) would silently succeed in tests but
  # fail in prod with:
  #   error[validation]: --output-dir must be a relative path...
  if [[ "$out_dir" = /* ]]; then
    echo "error[validation]: --output-dir must be a relative path, got absolute path '$out_dir'" >&2
    exit 2
  fi
  mkdir -p "$out_dir"

  # ---- allowed skills ----

  _write_skill() {
    local name="$1"; shift
    local desc="$1"; shift
    local body="${1:-}"
    mkdir -p "$out_dir/$name"
    {
      echo "---"
      echo "name: $name"
      echo "description: \"$desc\""
      echo "metadata:"
      echo "  version: 0.22.5"
      echo "  openclaw:"
      echo "    category: \"productivity\""
      echo "    requires:"
      echo "      bins:"
      echo "        - gws"
      echo "---"
      echo ""
      echo "# $name"
      echo ""
      echo "> **PREREQUISITE:** Read \`../gws-shared/SKILL.md\` for auth, global flags, and security rules. If missing, run \`gws generate-skills\` to create it."
      echo ""
      if [[ -n "$body" ]]; then
        echo "$body"
        echo ""
      fi
    } > "$out_dir/$name/SKILL.md"
  }

  _write_skill gws-shared "gws CLI: Shared patterns for authentication, global flags, and output formatting." "Shared base. No cross-ref needed."
  _write_skill gws-sheets "Google Sheets: Read and write spreadsheets." "See \`../gws-sheets-append/SKILL.md\` and \`../gws-sheets-read/SKILL.md\` for helpers. Note: the gws-sheets skill is the main one."
  _write_skill gws-sheets-append "Google Sheets: Append a row to a spreadsheet." "Parent: \`../gws-sheets/SKILL.md\`."
  _write_skill gws-sheets-read "Google Sheets: Read values from a spreadsheet." "Parent: \`../gws-sheets/SKILL.md\`."
  _write_skill gws-drive "Google Drive: Manage files, folders, and shared drives." "See \`../gws-drive-upload/SKILL.md\` for upload."
  _write_skill gws-drive-upload "Google Drive: Upload a file with automatic metadata." "Parent: \`../gws-drive/SKILL.md\`."
  _write_skill gws-gmail "Gmail: Send, read, and manage email." "Helpers: \`../gws-gmail-send/SKILL.md\`, \`../gws-gmail-read/SKILL.md\`, \`../gws-gmail-reply/SKILL.md\`, \`../gws-gmail-reply-all/SKILL.md\`, \`../gws-gmail-forward/SKILL.md\`, \`../gws-gmail-triage/SKILL.md\`, \`../gws-gmail-watch/SKILL.md\`."
  _write_skill gws-gmail-forward "Gmail: Forward a message to new recipients." "Parent: \`../gws-gmail/SKILL.md\`."
  _write_skill gws-gmail-read "Gmail: Read a message and extract its body or headers." "Parent: \`../gws-gmail/SKILL.md\`."
  _write_skill gws-gmail-reply "Gmail: Reply to a message." "Parent: \`../gws-gmail/SKILL.md\`."
  _write_skill gws-gmail-reply-all "Gmail: Reply-all to a message." "Parent: \`../gws-gmail/SKILL.md\`."
  _write_skill gws-gmail-send "Gmail: Send an email." "Parent: \`../gws-gmail/SKILL.md\`."
  _write_skill gws-gmail-triage "Gmail: Show unread inbox summary." "Parent: \`../gws-gmail/SKILL.md\`."
  _write_skill gws-gmail-watch "Gmail: Watch for new emails." "Parent: \`../gws-gmail/SKILL.md\`."
  _write_skill gws-docs "Read and write Google Docs." "See \`../gws-docs-write/SKILL.md\` for append."
  _write_skill gws-docs-write "Google Docs: Append text to a document." "Parent: \`../gws-docs/SKILL.md\`."
  _write_skill gws-calendar "Google Calendar: Manage calendars and events." "Helpers: \`../gws-calendar-agenda/SKILL.md\`, \`../gws-calendar-insert/SKILL.md\`."
  _write_skill gws-calendar-agenda "Google Calendar: Show upcoming events." "Parent: \`../gws-calendar/SKILL.md\`."
  _write_skill gws-calendar-insert "Google Calendar: Create a new event." "Parent: \`../gws-calendar/SKILL.md\`."

  # ---- junk that must be filtered out ----
  _write_skill gws-chat "Gmail Chat: Not included in Amazon-seller subset." "Should not appear in installed gws/."
  _write_skill gws-meet "Google Meet: Not included." "Filtered."
  _write_skill persona-hr "Persona: HR coordinator role play." "Filtered."
  _write_skill recipe-backup-sheet "Recipe: back up a sheet as CSV." "Filtered."

  exit 0
fi

# Unknown subcommand — fall through as no-op so scripts that probe
# for the binary don't crash.
echo "fake_gws: unknown command: $*" >&2
exit 0

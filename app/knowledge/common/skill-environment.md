# Skill Environment Setup

Skills that need Python packages use the **shared workspace venv** at `~/.vibe-seller/.venv/`. Dependencies are auto-installed during skill sync — you do NOT need to create per-skill virtual environments.

## How It Works

- Your `PATH` and `VIRTUAL_ENV` already point to the shared venv
- Just run `python script.py` — it resolves to the shared venv's Python
- Skill dependencies (from `requirements.txt`) are installed automatically during skill sync
- If a package is missing, install it with: `uv pip install <package>` (or `pip install <package>`)

## Running Skill Scripts

```bash
# Run a skill's Python script — python is on PATH from the shared venv
python .claude/skills/amazon-invoice/generate_invoice.py --output ./output.pdf

# If you need to install a package manually
uv pip install reportlab
```

## Notes

- Do NOT create per-skill `.venv/` directories — they are unnecessary and waste disk
- The shared venv is at `~/.vibe-seller/.venv/` and is managed by the workspace
- If `uv` is not available, fall back to `pip install`

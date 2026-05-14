#!/usr/bin/env python3
"""
Documentation Review Script - Uses GLM API (Anthropic-compatible) to review PR docs.
Posts line-by-line comments using GitHub's Pull Request Reviews API.
"""

from dataclasses import dataclass
import json
import os
import re
import traceback

from anthropic import Anthropic
import requests


@dataclass
class DiffHunk:
    """Represents a hunk in a diff."""

    file_path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list  # Each line is (line_type, content, position)
    # position is 1-indexed from the first @@ line in each file


@dataclass
class FileDiff:
    """Represents a complete file diff."""

    path: str
    hunks: list


def parse_diff(diff_text: str) -> list[FileDiff]:
    """
    Parse diff text into FileDiff objects with position mapping.
    Position counts from the first @@ line in each file (1-indexed).
    """
    files = []
    current_file = None
    current_hunk = None
    position = 0

    for line in diff_text.split('\n'):
        # New file diff
        if line.startswith('diff --git'):
            if current_file:
                files.append(current_file)
            current_file = None
            current_hunk = None

        # Extract file path
        elif line.startswith('+++ b/'):
            file_path = line[6:]  # Remove '+++ b/'
            current_file = FileDiff(path=file_path, hunks=[])
            position = 0

        # Hunk header
        elif line.startswith('@@') and current_file is not None:
            match = re.match(
                r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line
            )
            if match:
                old_start = int(match.group(1))
                old_count = int(match.group(2)) if match.group(2) else 1
                new_start = int(match.group(3))
                new_count = int(match.group(4)) if match.group(4) else 1

                current_hunk = DiffHunk(
                    file_path=current_file.path,
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    lines=[],
                )
                current_file.hunks.append(current_hunk)
                position += 1
                current_hunk.lines.append(('header', line, position))

        # Diff content lines
        elif current_hunk is not None:
            position += 1
            if line.startswith('+'):
                current_hunk.lines.append(('addition', line, position))
            elif line.startswith('-'):
                current_hunk.lines.append(('deletion', line, position))
            elif line.startswith(' '):
                current_hunk.lines.append(('context', line, position))
            elif line.startswith('\\'):
                # "\ No newline at end of file" - doesn't count as a line
                position -= 1
            else:
                current_hunk.lines.append(('other', line, position))

    if current_file:
        files.append(current_file)

    return files


def build_line_to_position_map(
    files: list[FileDiff],
) -> dict[tuple[str, int], int]:
    """
    Build a mapping from (file_path, new_line_number) to position.
    Only lines that exist in the new file (RIGHT side) can receive comments.
    """
    line_map = {}

    for file_diff in files:
        for hunk in file_diff.hunks:
            new_line = hunk.new_start
            for line_type, content, position in hunk.lines:
                if line_type == 'header':
                    continue
                elif line_type == 'addition':
                    line_map[(file_diff.path, new_line)] = position
                    new_line += 1
                elif line_type == 'context':
                    line_map[(file_diff.path, new_line)] = position
                    new_line += 1
                elif line_type == 'deletion':
                    # Deletions don't have a line number in the new file
                    pass

    return line_map


SYSTEM_PROMPT = """\
You review PRs for missing or stale documentation. Respond ONLY with JSON.

IMPORTANT: You will be given the PR diff AND the current contents \
of existing doc files. Do NOT claim a file is missing if its \
contents are provided below. Only flag genuinely missing sections \
within files that are part of the diff.

## Documentation Structure

This project uses a layered documentation approach:

### Root-level files (always loaded by Claude Code):
- **CLAUDE.md** (<300 lines): Conventions, commands, code style, \
workflow recipes (Adding X), system prompts table, agent context \
injection table, testing commands. NO subsystem descriptions, \
NO architecture deep-dives.
- **README.md**: Project overview, quick start, prerequisites, \
tech stack, implementation status. NOT an API reference.
- **DESIGN.md**: Architecture overview, AI agent design, data \
model schemas, concurrency model. NO stale implementation \
phases or verification plans.

### docs/ directory (topic-specific deep-dives):
- **docs/tasks.md**: Task lifecycle, data persistence, status \
transitions, agent context injection, scheduled tasks.
- **docs/workspace.md**: Workspace assistant, knowledge system, \
skills system.
- **docs/subsystems.md**: Patrol, email, browser profiles, \
concurrency, Ziniao/WSL, CDP proxy.
- **docs/frontend.md**: React components, views, i18n, SSE.
- **docs/backend.md**: FastAPI modules, models, schemas, config.
- **docs/api.md**: All API routes by router.
- **docs/testing.md**: Test tiers, fixtures, FakeAgent.
- **docs/browser.md**: Browser backends, manager, CDP proxy.
- **docs/events.md**: SSE event bus, event types, business events.

## Review Rules

1. If a PR adds/changes a CODE feature (new module, new class, \
new function, changed behavior), check that the RELEVANT \
docs/ file is updated (not just CLAUDE.md or README.md).
2. If a PR adds a new API endpoint, flag if docs/api.md is not \
updated.
3. If a PR changes task status flow, flag if docs/tasks.md is \
not updated.
4. CLAUDE.md should NOT grow beyond 300 lines. If a PR adds \
subsystem details to CLAUDE.md, suggest moving to docs/.
5. Skip: self-documenting code, minor refactors, internal helpers. \
Skip files not touched by the PR.
6. Changes to agent system prompts (app/prompts/*.md) and \
generated workspace files (~/.vibe-seller/CLAUDE.md) are \
INTERNAL runtime behavior — they do NOT need developer \
documentation in CLAUDE.md or docs/. Only flag if the \
prompt change reflects a new developer-facing feature or API.

JSON format:
{{
  "summary": "### What changed\\n- bullet 1\\n- bullet 2\\n\\n### Verdict\\n...",
  "comments": [{{"file": "path", "line": 42, "body": "issue"}}],
  "missing_docs": ["docs/api.md: Add endpoint X"]
}}

The "summary" field MUST be GitHub-flavored markdown with clear \
sections. Use ### headings and bullet points. Structure it as:
1. **What changed** — 2-4 bullets summarizing the doc-relevant \
   changes in this PR.
2. **Verdict** — one sentence: docs are adequate, or list what \
   is missing/stale.

Do NOT write a wall of text. Keep it scannable.

Rules: line numbers from NEW file only. \
Empty comments if docs are adequate. JSON only, no other text.
"""


DOC_FILES = [
    'README.md',
    'CLAUDE.md',
    'DESIGN.md',
    'docs/tasks.md',
    'docs/workspace.md',
    'docs/api.md',
    'docs/subsystems.md',
    'docs/frontend.md',
    'docs/backend.md',
    'docs/testing.md',
    'docs/browser.md',
    'docs/events.md',
]


def get_existing_docs() -> str:
    """Read existing doc files from the repo checkout."""
    sections = []
    for filename in DOC_FILES:
        if os.path.isfile(filename):
            with open(filename, encoding='utf-8') as f:
                content = f.read()
            # Truncate large files to avoid blowing up the prompt
            if len(content) > 8000:
                content = content[:8000] + '\n... (truncated)'
            sections.append(f'=== {filename} (exists) ===\n{content}')
        else:
            sections.append(f'=== {filename} (does not exist) ===')
    return '\n\n'.join(sections)


def get_pr_diff(repo: str, pr_number: str, token: str) -> str:
    """Fetch PR diff from GitHub API."""
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3.diff',
    }
    diff_url = f'https://api.github.com/repos/{repo}/pulls/{pr_number}'

    response = requests.get(diff_url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.text


def call_llm_api(
    client: Anthropic, model: str, diff: str, existing_docs: str
) -> dict:
    """Call LLM API and return parsed JSON response."""
    full_prompt = (
        f'{SYSTEM_PROMPT}\n\n'
        'Here are the current doc files in the repo:\n\n'
        f'{existing_docs}\n\n'
        'Review this PR diff for documentation completeness:\n\n'
        f'```diff\n{diff[:24000]}\n```\n\n'
        'Remember: Your response must be '
        'ONLY valid JSON, no other text.'
    )

    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[
            {
                'role': 'user',
                'content': full_prompt,
            }
        ],
    )

    # Handle different content block types
    if not message.content:
        raise RuntimeError(
            f'LLM returned empty content '
            f'(stop_reason={getattr(message, "stop_reason", "?")})'
        )
    response_text = ''
    for block in message.content:
        if hasattr(block, 'text'):
            response_text += block.text
        elif hasattr(block, 'thinking'):
            response_text += block.thinking
        else:
            response_text += str(block)

    # Clean up meta-commentary that some models add
    meta_phrases = [
        r'The user wants me to review[^.]*\.',
        r'Let me analyze[^.]*\.',
        r"I'll review[^.]*\.",
        r'Looking at this PR[^.]*\.',
        r'I need to review[^.]*\.',
        r'The user is asking me to[^.]*\.',
    ]
    for phrase in meta_phrases:
        response_text = re.sub(
            phrase, '', response_text, flags=re.IGNORECASE
        ).strip()

    # Parse JSON response
    try:
        # Try to extract JSON if wrapped in markdown code blocks
        if '```json' in response_text:
            json_match = re.search(
                r'```json\s*(.*?)\s*```', response_text, re.DOTALL
            )
            if json_match:
                response_text = json_match.group(1)
        elif response_text.strip().startswith('```'):
            json_match = re.search(
                r'```\s*(.*?)\s*```', response_text, re.DOTALL
            )
            if json_match:
                response_text = json_match.group(1)

        # Look for JSON object pattern if still not valid
        json_pattern = re.search(
            r'\{[\s\S]*"summary"[\s\S]*"comments"[\s\S]*\}', response_text
        )
        if json_pattern:
            response_text = json_pattern.group(0)

        result = json.loads(response_text.strip())

        # Clean up the summary if it still has meta-commentary
        summary = result.get('summary', '')
        for phrase in meta_phrases:
            summary = re.sub(phrase, '', summary, flags=re.IGNORECASE).strip()
        result['summary'] = summary

        return result
    except json.JSONDecodeError:
        print(
            'ERROR: Failed to parse LLM response as JSON.'
            f' Response: {response_text[:500]}'
        )
        raise RuntimeError(
            'LLM response is not valid JSON. Review cannot proceed.'
        )


def validate_comment(
    comment: dict, line_map: dict[tuple[str, int], int]
) -> tuple[int, str] | None:
    """
    Validate a comment and return (position, body) if valid.
    Returns None if the comment cannot be mapped to a valid position.
    """
    file_path = comment.get('file')
    line = comment.get('line')
    body = comment.get('body')

    if not file_path or not line or not body:
        return None

    # Look up position in the map
    position = line_map.get((file_path, line))
    if position is None:
        return None

    return (position, body.strip())


def create_pr_review(
    repo: str,
    pr_number: str,
    token: str,
    commit_sha: str,
    summary: str,
    comments: list[dict],
    missing_docs: list[str],
) -> None:
    """
    Create a formal PR review using GitHub's Reviews API.
    Posts line-by-line comments on specific lines.
    """
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json',
    }

    # Build the review body
    body_parts = [
        '## 📚 Docs Reviewer Bot\n',
        summary,
    ]

    if missing_docs:
        body_parts.append('\n### Missing Documentation')
        for doc in missing_docs:
            body_parts.append(f'- [ ] {doc}')

    body_parts.append('\n---')

    review_body = '\n'.join(body_parts)

    # Prepare the review payload
    review_data = {
        'commit_id': commit_sha,
        'body': review_body,
        'event': 'COMMENT',
        'comments': comments,
    }

    review_url = (
        f'https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews'
    )

    response = requests.post(
        review_url, headers=headers, json=review_data, timeout=30
    )
    response.raise_for_status()

    result = response.json()
    print(f'Created review: {result.get("html_url", "unknown")}')
    print(f'Posted {len(comments)} line-specific comments')


def main():
    # Get LLM configuration
    llm_base_url = os.environ['GLM_BASE_URL']
    llm_api_key = os.environ['GLM_API_KEY']
    llm_model = os.environ['GLM_MODEL']

    # Get GitHub configuration
    repo = os.environ['REPO']
    pr_number = os.environ['PR_NUMBER']
    token = os.environ['GITHUB_TOKEN']
    commit_sha = os.environ['COMMIT_SHA']

    # Get PR diff
    diff = get_pr_diff(repo, pr_number, token)

    if not diff.strip():
        print('No diff to review')
        return

    # Parse the diff to build line mapping
    print('Parsing diff...')
    files = parse_diff(diff)
    line_map = build_line_to_position_map(files)
    print(f'Found {len(files)} files with {len(line_map)} mappable lines')

    # Read existing doc files for context
    existing_docs = get_existing_docs()
    print(f'Loaded existing doc context ({len(existing_docs)} chars)')

    # Call LLM API
    client = Anthropic(
        base_url=llm_base_url,
        api_key=llm_api_key,
        timeout=300.0,
    )

    max_retries = 2
    review_data = None
    for attempt in range(1, max_retries + 1):
        try:
            review_data = call_llm_api(client, llm_model, diff, existing_docs)
            break
        except RuntimeError as e:
            print(f'Attempt {attempt}/{max_retries} failed: {e}')
            if attempt == max_retries:
                print('ERROR: All retries exhausted.')
                raise
            print('Retrying...')
        except Exception as e:
            print(f'ERROR: Failed to get review from LLM API: {e}')
            traceback.print_exc()
            raise

    # Extract review components
    summary = review_data.get('summary', 'No summary provided')
    comments_data = review_data.get('comments', [])
    missing_docs = review_data.get('missing_docs', [])

    if not isinstance(comments_data, list):
        raise ValueError(
            f"Invalid 'comments' field in LLM response: expected list, got {type(comments_data)}"
        )

    if not isinstance(missing_docs, list):
        missing_docs = []

    print(f'LLM returned {len(comments_data)} comments to validate')

    # Validate and convert comments to GitHub format
    github_comments = []
    for comment in comments_data:
        result = validate_comment(comment, line_map)
        if result is not None:
            position, body = result
            github_comments.append({
                'path': comment['file'],
                'position': position,
                'body': body,
            })
        else:
            print(f'Skipping unmappable comment: {comment}')

    # Create the PR review with line-by-line comments
    create_pr_review(
        repo=repo,
        pr_number=pr_number,
        token=token,
        commit_sha=commit_sha,
        summary=summary,
        comments=github_comments,
        missing_docs=missing_docs,
    )

    print('Doc review posted successfully')


if __name__ == '__main__':
    main()

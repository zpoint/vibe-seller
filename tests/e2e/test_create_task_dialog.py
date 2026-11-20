"""E2E: the New Task dialog is one prompt box that takes any file.

- A long prompt is created without a title: the server derives one
  from the first sentence, and the list shows the row's title (the
  placeholder, or the AI title once it has been pushed over SSE).
- Any file type can be attached. Images get a thumbnail, other files
  an extension chip, and every file lands in the task. Only an
  inline-safe type (PNG here) opens in the browser; CSV and HTML come
  back as downloads, so an uploaded page can never run on the app's
  origin.

Each test stops the agent it started — the point is the dialog, not
the run.
"""

import re
import time

from playwright.sync_api import Page, expect
import pytest

import tests.e2e.e2e_helpers as e2e_helpers

pytestmark = [pytest.mark.e2e]

FIRST = 'Lower the bid on widget-006 to 1.00 in the SA marketplace.'
PROMPT = f'{FIRST} Then compare last week ACOS against the week before.'
# The first sentence is 58 characters: capped at 50 with an ellipsis.
PLACEHOLDER = FIRST[:49] + '…'

# 1x1 transparent PNG.
PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
    '1f15c4890000000d49444154789c6360000002000005000127a3d4a1'
    '0000000049454e44ae426082'
)


def _open_dialog(page: Page, tag: str) -> None:
    store = f'e2e-{tag}-{int(time.time())}'
    page.get_by_role('button', name='+ New Store').click()
    page.get_by_placeholder('Store name...').fill(store)
    page.get_by_role('button', name='Create', exact=True).click()
    page.get_by_role('button', name=re.compile(store)).first.click()
    page.get_by_role('button', name='+ New Task').click()


def _create(page: Page, prompt: str) -> dict:
    """Type *prompt* into the one box, submit, return the created row."""
    page.get_by_placeholder('e.g. Navigate to google.com').fill(prompt)
    with page.expect_response(
        lambda r: r.url.endswith('/api/tasks') and r.request.method == 'POST'
    ) as created:
        page.get_by_role('button', name='Create & Run').click()
    assert created.value.ok, created.value.text()
    sent = created.value.request.post_data_json
    assert not sent.get('title'), 'the dialog sends no title'
    assert sent['description'] == prompt
    return created.value.json()


def _url(path: str) -> str:
    # Read at call time: an owned-server module re-aims BASE_URL.
    return e2e_helpers.BASE_URL.rstrip('/') + path


def _stop(page: Page, task_id: str) -> None:
    page.request.post(_url(f'/api/tasks/{task_id}/agent/stop'))


def test_one_box_prompt_gets_a_derived_title(authenticated_page: Page):
    page = authenticated_page
    _open_dialog(page, 'title')
    # One box: there is no separate title input any more.
    expect(page.locator('#task-prompt')).to_be_visible()
    expect(page.get_by_label(re.compile('^Title', re.I))).to_have_count(0)

    task = _create(page, PROMPT)
    try:
        assert task['title'] == PLACEHOLDER
        assert task['description'] == PROMPT
        # The detail header shows what the server holds right now: the
        # placeholder, or the AI title if it already landed via SSE.
        header = page.locator('h2').filter(
            has_not_text=re.compile(r'^e2e-title-')
        )
        deadline = time.time() + 30
        while True:
            title = page.request.get(_url(f'/api/tasks/{task["id"]}')).json()[
                'title'
            ]
            assert title and title != PROMPT, title
            try:
                expect(header.filter(has_text=title).first).to_be_visible(
                    timeout=2000
                )
                break
            except AssertionError:
                if time.time() > deadline:
                    raise
        expect(page.locator('button', has_text=title).first).to_be_visible()
    finally:
        _stop(page, task['id'])


def test_any_file_type_attaches_and_only_images_render(
    authenticated_page: Page,
):
    page = authenticated_page
    _open_dialog(page, 'files')
    page.locator('input[type="file"]').set_input_files([
        {'name': 'shot.png', 'mimeType': 'image/png', 'buffer': PNG},
        {
            'name': 'orders.csv',
            'mimeType': 'text/csv',
            'buffer': b'sku,qty\nwidget-006,3\n',
        },
        {
            'name': 'page.html',
            'mimeType': 'text/html',
            'buffer': b'<script>window.pwned = 1</script>',
        },
    ])
    # Nothing is rejected; the image previews, the others are chips.
    expect(page.get_by_test_id('attachment-rejected')).to_have_count(0)
    expect(page.get_by_role('img', name='shot.png')).to_be_visible()
    chips = page.get_by_test_id('attachment-file-chip')
    expect(chips).to_have_count(2)
    expect(chips.filter(has_text='CSV')).to_have_count(1)
    expect(chips.filter(has_text='HTML')).to_have_count(1)

    task = _create(page, 'Check these files')
    try:
        names: list[str] = []
        deadline = time.time() + 20
        while time.time() < deadline:
            names = [
                f['name']
                for f in page.request.get(
                    _url(f'/api/tasks/{task["id"]}/files')
                ).json()
            ]
            if sum(n.startswith('uploads/') for n in names) >= 3:
                break
            time.sleep(0.5)
        by_ext = {
            ext: next(n for n in names if n.endswith(ext))
            for ext in ('.png', '.csv', '.html')
        }

        def served(path: str):
            r = page.request.get(_url(f'/api/tasks/{task["id"]}/files/{path}'))
            assert r.ok, (path, r.status)
            return r.headers

        png = served(by_ext['.png'])
        assert png['content-type'] == 'image/png'
        assert 'attachment' not in png.get('content-disposition', '')
        for ext in ('.csv', '.html'):
            h = served(by_ext[ext])
            assert h['content-type'] == 'application/octet-stream', h
            assert h['content-disposition'].startswith('attachment'), h
            assert h['x-content-type-options'] == 'nosniff', h
    finally:
        _stop(page, task['id'])

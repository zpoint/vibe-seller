"""PreToolUse guards keeping image deliverables model-produced.

An image deliverable comes from the image MODEL, never from local pixel
editing. ``generated_images/`` has exactly one writer — the vision
router, after the user confirms a ``vibe_seller_generate_image`` call
(``app/routers/vision.py``) — and every file there carries provenance: a
``generated_image`` task message recording the prompt and the model,
which is the claim the inline card in the UI renders those bytes under.

Real incident (task 2fde3b9c, "keep the content, only remove the
background"): the agent generated the image with the tool, then could
not SEE the result — the task ran on a text-only model, so the Read came
back as an image block the model discarded. It substituted a numeric
pixel audit in Python, judged the model's background "off-white"
(254,254,253) and the product too large, and rebuilt the deliverable
locally with rembg + Pillow — writing over the tool's PNG at the SAME
path. The user's card still said ``model: gpt-image-2-2k`` while showing
a locally composited image, and the task's reflection wrote "prefer
rembg over vibe_seller_generate_image" into store knowledge, which would
have made every later image task do the same thing.

So the invariant is: a raster image file is never PRODUCED locally.
Measuring one stays fine (``file``, ``stat``, ``Image.open`` + numpy);
producing or replacing one is the model's job, via another tool call.
Guards inspect the inline command text only, on purpose — skill scripts
that legitimately touch images (``amazon-listing/scripts/ocr_1688.py``
reads them for OCR) are invoked by filename and never inspected here.

Like the guards in :mod:`app.ai.bash_safety`, the match is a string
check rather than real shell parsing: false negatives (an obscure form
slips through) are fine, false positives (blocking a read-only audit)
would be bad.
"""

import re

from app.ai.bash_safety import COMMAND_PREFIX

_GEN_IMAGES_TOKEN = 'generated_images/'

# Imaging libraries. Their presence is what makes a ``.save(`` in the
# same snippet a raster write rather than, say, the openpyxl workbook
# save in ``amazon-ads/scripts/ads_bulk.py``.
_IMAGING_LIB_RE = re.compile(
    r'(?i)(?:'
    r'\bPIL\b|Image\.open|Image\.new|\bImageOps\b|\bImageDraw\b'
    r'|\bImageChops\b|\bcv2\b|\bskimage\b|\bpyvips\b|\bimageio\b'
    r'|\bwand\.image\b'
    r')'
)

# Calls that PRODUCE or MUTATE raster bytes: ``.save(``/``imwrite(``/
# ``write_bytes(`` write the file, ``Image.new(``/``.paste(``/
# ``.putalpha(``/``alpha_composite(`` composite one. Read-only
# inspection uses none of these.
_IMAGE_PRODUCE_RE = re.compile(
    r'(?i)(?:'
    r'\.save\s*\(|imwrite\s*\(|write_bytes\s*\('
    r'|Image\.new\s*\(|\.paste\s*\(|\.putalpha\s*\('
    r'|alpha_composite\s*\(|\.thumbnail\s*\('
    r')'
)

# rembg and friends exist ONLY to strip a background — there is no
# read-only use, so any mention (``pip install rembg`` included) is
# already the wrong path and is denied before the install cost.
_BG_REMOVER_RE = re.compile(r'(?i)\b(?:rembg|backgroundremover|carvekit)\b')

_IMAGE_EXT_RE = re.compile(r'(?i)\.(?:png|jpe?g|webp|bmp|tiff?|gif|avif)\b')

# ImageMagick / ffmpeg in command position. ``convert`` is an ordinary
# English word, so it only counts when the command also names an image
# file.
_IMAGE_CLI_RE = re.compile(
    COMMAND_PREFIX + r'(?:magick|mogrify|convert|ffmpeg)\b'
)

# A write that LANDS in generated_images/: shell redirect, or a
# file-moving command with the directory among its arguments.
_GEN_REDIRECT_RE = re.compile(r'>>?\s*[^|;&\n]*generated_images/')
_GEN_FILE_CMD_RE = re.compile(
    COMMAND_PREFIX
    + r'(?:cp|mv|rm|ln|tee|install|dd|rsync|scp|truncate|touch)\b'
    + r'[^;|&\n]*generated_images/'
)

_GEN_IMAGES_PATH_RE = re.compile(r'(?:^|/)generated_images/')

_IMAGE_DENY = (
    'BLOCKED — {label}. An image deliverable comes from the image '
    'MODEL, never from local pixel editing.\n\n'
    '`generated_images/` is owned by `vibe_seller_generate_image`: '
    'every file there has a recorded prompt + model, and the card the '
    'user sees renders those bytes under that claim. A locally built '
    'or edited file at that path makes the card lie about where the '
    'image came from.\n\n'
    'To CHANGE a generated image — background not pure white, product '
    'too large or touching the edges, wrong crop, wrong colour, '
    'anything — call `vibe_seller_generate_image` AGAIN:\n'
    '  • pass the PREVIOUS generated image (its `generated_images/…` '
    'path) as a `reference_image`, so the model EDITS that result '
    'instead of drifting from scratch;\n'
    '  • write a prompt naming ONLY the fix — e.g. "keep the product '
    'pixel-identical; make the background pure white RGB(255,255,255) '
    'and leave a clear margin so the product fills about 85% of the '
    'frame".\n\n'
    'If you cannot actually SEE the image in your context (its Read '
    'came back as something you could not look at), do NOT invent a '
    'pixel-measurement proxy and do NOT "fix" what you measured: the '
    'user sees the image inline in the task, so hand it to them and '
    'ask whether it is right.\n\n'
    'READING an image is fine — `file`, `stat`, `Image.open` + numpy. '
    'Only producing or replacing one is blocked. This is platform '
    'policy, not a preference discovered in this task: do not record a '
    'local-processing workaround as knowledge.'
)


def check_local_image_edit(command: str) -> str | None:
    """Deny a Bash command that produces or replaces a raster image.

    Four surfaces, mirroring the observed detour: a background-removal
    library in any form; an inline snippet that both uses an imaging
    library and calls a produce/mutate function; ImageMagick/ffmpeg
    against an image file; and any write landing in
    ``generated_images/`` (redirect, cp/mv/rm/tee, or an inline save).
    """
    if not command:
        return None
    if _BG_REMOVER_RE.search(command):
        return _IMAGE_DENY.format(
            label='background-removal library (rembg and friends)'
        )
    if _IMAGING_LIB_RE.search(command) and _IMAGE_PRODUCE_RE.search(command):
        return _IMAGE_DENY.format(
            label='inline script writing an image with an imaging library'
        )
    if _IMAGE_CLI_RE.search(command) and _IMAGE_EXT_RE.search(command):
        return _IMAGE_DENY.format(
            label='ImageMagick/ffmpeg rewriting an image file'
        )
    if _GEN_IMAGES_TOKEN in command:
        if _GEN_REDIRECT_RE.search(command):
            return _IMAGE_DENY.format(
                label='shell redirection into generated_images/'
            )
        if _GEN_FILE_CMD_RE.search(command):
            return _IMAGE_DENY.format(
                label='cp/mv/rm/tee targeting generated_images/'
            )
        if _IMAGE_PRODUCE_RE.search(command):
            return _IMAGE_DENY.format(
                label='inline script writing into generated_images/'
            )
    return None


def check_generated_image_write(tool_name: str, tool_input: dict) -> str | None:
    """Deny a built-in Write/Edit targeting ``generated_images/**``.

    Closes the file-tool hop around :func:`check_local_image_edit`:
    denied at the Bash layer, an agent's next move is the built-in
    Write tool against the same path.
    """
    if tool_name not in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        return None
    path = tool_input.get('file_path') or tool_input.get('notebook_path') or ''
    if not isinstance(path, str) or not _GEN_IMAGES_PATH_RE.search(path):
        return None
    return _IMAGE_DENY.format(label=f'{tool_name} into generated_images/')

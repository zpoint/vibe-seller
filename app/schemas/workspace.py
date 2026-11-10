from pydantic import BaseModel

from app.text_utils import SafeStr


class FileWriteRequest(BaseModel):
    content: SafeStr


class DeleteLinesRequest(BaseModel):
    # The file's line count as the caller read it, so a deletion is never
    # applied to a file that changed since (the ranges would shift).
    line_count: int
    ranges: list[tuple[int, int]]


class FileResetRequest(BaseModel):
    path: str
    commit: str


class SkillCreateRequest(BaseModel):
    name: SafeStr
    description: SafeStr = ''
    origin_url: str = ''


class SkillSaveRequest(BaseModel):
    """Upsert a user-space skill: full SKILL.md + optional bundles.

    ``files`` holds text (path -> content, stored as UTF-8);
    ``files_bin`` holds base64 (path -> base64) for anything that is
    not valid UTF-8 — those round-trip byte-identical.
    ``executables`` names written files that get the executable bit.
    ``origin_url`` records provenance (the skill lists as imported).
    ``replace`` swaps in the tree wholesale instead of merging (the
    import flow), and replacing an EXISTING user skill needs
    ``overwrite``. One call writes the whole skill.
    """

    skill_md: SafeStr
    files: dict[str, SafeStr] = {}
    files_bin: dict[str, SafeStr] = {}
    executables: list[SafeStr] = []
    origin_url: SafeStr = ''
    replace: bool = False
    overwrite: bool = False


class StoreProfileCreateRequest(BaseModel):
    slug: str
    name: str
    platform: str = ''
    country: str = ''
    backend: str = 'chrome'


class FileTreeNode(BaseModel):
    path: str
    is_dir: bool
    size: int = 0

from pydantic import BaseModel

from app.text_utils import SafeStr


class FileWriteRequest(BaseModel):
    content: SafeStr


class FileResetRequest(BaseModel):
    path: str
    commit: str


class SkillCreateRequest(BaseModel):
    name: SafeStr
    description: SafeStr = ''
    origin_url: str = ''


class SkillSaveRequest(BaseModel):
    """Upsert a user-space skill: full SKILL.md + optional bundles."""

    skill_md: SafeStr
    files: dict[str, SafeStr] = {}


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

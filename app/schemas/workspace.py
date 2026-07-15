from pydantic import BaseModel


class FileWriteRequest(BaseModel):
    content: str


class FileResetRequest(BaseModel):
    path: str
    commit: str


class SkillCreateRequest(BaseModel):
    name: str
    description: str = ''
    origin_url: str = ''


class SkillSaveRequest(BaseModel):
    """Upsert a user-space skill: full SKILL.md + optional bundles."""

    skill_md: str
    files: dict[str, str] = {}


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

"""Telemetry side-channel: lets the frontend report install-level
context (currently just the i18n locale) that the backend can't
derive on its own."""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import telemetry

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/telemetry', tags=['telemetry'])

# Public endpoints — no auth required. The frontend calls this before
# the user is authenticated (i18n boots before login), and the only
# thing recorded is anonymous install metadata.

_ALLOWED_LOCALES = frozenset({'en', 'zh'})


class LocalePayload(BaseModel):
    locale: str = Field(..., max_length=16)


@router.post('/locale')
async def report_locale(payload: LocalePayload):
    """Frontend reports its current i18n locale.

    Called once on i18n init and again whenever the user toggles
    language. Unknown locales are coerced to 'unknown' so the
    telemetry dashboard stays tidy.
    """
    locale = payload.locale if payload.locale in _ALLOWED_LOCALES else 'unknown'
    telemetry.set_app_locale(locale)
    return {'ok': True, 'locale': locale}

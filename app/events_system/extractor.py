"""
LLM-based event extraction from channel messages.

Extracts structured events (deadlines, activities, promotions) from
email/WeChat message text in Chinese and English.
"""

import asyncio
import json
import logging

import anthropic

from app.env_options import Options
from app.prompts import EXTRACTION_PROMPT

logger = logging.getLogger(__name__)


class EventExtractor:
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            api_key = Options.ANTHROPIC_API_KEY.get() or None
            if not api_key:
                raise RuntimeError(
                    'ANTHROPIC_API_KEY environment variable not set. '
                    'Event extraction requires an Anthropic API key.'
                )
            self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    async def extract_events(
        self, message_text: str, channel_type: str = ''
    ) -> list[dict]:
        """Extract structured events from message text using Claude API."""
        if not message_text.strip():
            return []

        try:
            client = self._get_client()

            response = await asyncio.to_thread(
                client.messages.create,
                model='claude-haiku-4-5-20251001',
                max_tokens=1024,
                system=EXTRACTION_PROMPT,
                messages=[{'role': 'user', 'content': message_text}],
            )

            text = response.content[0].text.strip()
            # Handle markdown code blocks
            if text.startswith('```'):
                text = text.split('\n', 1)[1] if '\n' in text else text[3:]
                if text.endswith('```'):
                    text = text[:-3]
                text = text.strip()

            events = json.loads(text)
            if not isinstance(events, list):
                return []
            return events

        except Exception as e:
            logger.error(f'Event extraction failed: {e}')
            return []


# Singleton
event_extractor = EventExtractor()

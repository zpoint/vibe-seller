"""
Google Calendar event backend.

Uses Google Calendar API v3 with a service account or OAuth2 credentials.
Requires credentials_path and calendar_id in config.
"""

import asyncio
from datetime import date as date_type
import logging

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
except ImportError:
    service_account = None  # type: ignore[assignment]
    build = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

from app.events_system.syncer import EventBackend, register_backend


@register_backend('google_calendar')
class GoogleCalendarBackend(EventBackend):
    """Sync events to Google Calendar."""

    def __init__(self):
        self.credentials_path: str = ''
        self.calendar_id: str = 'primary'
        self._service = None

    def configure(self, config: dict):
        self.credentials_path = config.get('credentials_path', '')
        self.calendar_id = config.get('calendar_id', 'primary')

    def _get_service(self):
        if self._service is None:
            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path,
                scopes=['https://www.googleapis.com/auth/calendar'],
            )
            self._service = build('calendar', 'v3', credentials=credentials)
        return self._service

    async def create_event(
        self,
        title: str,
        description: str | None,
        event_date: str | None,
        deadline: str | None,
    ) -> str:
        """Create a Google Calendar event. Returns the event ID."""
        event_body = self._build_event_body(
            title, description, event_date, deadline
        )

        def _do():
            service = self._get_service()
            return (
                service.events()
                .insert(calendarId=self.calendar_id, body=event_body)
                .execute()
            )

        result = await asyncio.to_thread(_do)
        event_id = result.get('id', '')
        logger.info(f'Created Google Calendar event: {event_id}')
        return event_id

    async def update_event(
        self,
        external_id: str,
        title: str,
        description: str | None,
        event_date: str | None,
        deadline: str | None,
    ) -> None:
        """Update a Google Calendar event."""
        event_body = self._build_event_body(
            title, description, event_date, deadline
        )

        def _do():
            service = self._get_service()
            service.events().update(
                calendarId=self.calendar_id,
                eventId=external_id,
                body=event_body,
            ).execute()

        await asyncio.to_thread(_do)
        logger.info(f'Updated Google Calendar event: {external_id}')

    async def delete_event(self, external_id: str) -> None:
        """Delete a Google Calendar event."""

        def _do():
            service = self._get_service()
            service.events().delete(
                calendarId=self.calendar_id, eventId=external_id
            ).execute()

        await asyncio.to_thread(_do)
        logger.info(f'Deleted Google Calendar event: {external_id}')

    def _build_event_body(
        self,
        title: str,
        description: str | None,
        event_date: str | None,
        deadline: str | None,
    ) -> dict:
        """Build the Google Calendar event body."""
        body: dict = {'summary': title}
        if description:
            body['description'] = description

        date = event_date or deadline
        if date:
            if 'T' in date:
                # DateTime event
                body['start'] = {'dateTime': date}
                body['end'] = {'dateTime': date}
            else:
                # All-day event
                body['start'] = {'date': date}
                body['end'] = {'date': date}
        else:
            # No date — create an all-day event for today
            today = date_type.today().isoformat()
            body['start'] = {'date': today}
            body['end'] = {'date': today}

        return body

"""
Dida365 (TickTick Chinese version) event backend.

Uses Dida365 Open API: https://developer.dida365.com/docs
Requires an access_token and project_id in config.
"""

import logging

import httpx

from app.events_system.syncer import EventBackend, register_backend

logger = logging.getLogger(__name__)

DIDA365_API_BASE = 'https://api.dida365.com/open/v1'


@register_backend('dida365')
class Dida365Backend(EventBackend):
    """Sync events to Dida365 as tasks with due dates."""

    def __init__(self):
        self.access_token: str = ''
        self.project_id: str = ''

    def configure(self, config: dict):
        self.access_token = config.get('access_token', '')
        self.project_id = config.get('project_id', '')

    def _headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
        }

    async def create_event(
        self,
        title: str,
        description: str | None,
        event_date: str | None,
        deadline: str | None,
    ) -> str:
        """Create a task in Dida365. Returns the task ID."""
        payload: dict = {'title': title}
        if description:
            payload['content'] = description
        if self.project_id:
            payload['projectId'] = self.project_id
        # Dida365 uses dueDate for deadlines
        due = deadline or event_date
        if due:
            # Ensure ISO 8601 format with timezone
            if 'T' not in due:
                due = due + 'T00:00:00+0000'
            payload['dueDate'] = due

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f'{DIDA365_API_BASE}/task',
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            task_id = data.get('id', '')
            logger.info(f'Created Dida365 task: {task_id}')
            return task_id

    async def update_event(
        self,
        external_id: str,
        title: str,
        description: str | None,
        event_date: str | None,
        deadline: str | None,
    ) -> None:
        """Update a task in Dida365."""
        payload: dict = {'id': external_id, 'title': title}
        if description:
            payload['content'] = description
        if self.project_id:
            payload['projectId'] = self.project_id
        due = deadline or event_date
        if due:
            if 'T' not in due:
                due = due + 'T00:00:00+0000'
            payload['dueDate'] = due

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f'{DIDA365_API_BASE}/task/{external_id}',
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            logger.info(f'Updated Dida365 task: {external_id}')

    async def delete_event(self, external_id: str) -> None:
        """Delete a task from Dida365."""
        # Dida365 delete requires projectId + taskId
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f'{DIDA365_API_BASE}/project/{self.project_id}/task/{external_id}',
                headers=self._headers(),
            )
            resp.raise_for_status()
            logger.info(f'Deleted Dida365 task: {external_id}')

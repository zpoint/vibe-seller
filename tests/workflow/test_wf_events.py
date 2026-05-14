"""Workflow tests for event status machine, activities, and filtering."""

import pytest
from sqlalchemy import select

from app.models.event import Event
from app.models.event_activity import EventActivity

pytestmark = pytest.mark.workflow


class TestEventCrud:
    async def test_create_event(self, admin_client):
        r = await admin_client.post(
            '/api/events',
            json={'title': 'New Event', 'priority': 1},
        )
        assert r.status_code == 200
        e = r.json()
        assert e['title'] == 'New Event'
        assert e['status'] == 'open'
        assert e['priority'] == 1


class TestEventStatusMachine:
    async def _create_event(self, client, title='Test Event', status='open'):
        """Create an event (defaults to 'open' status)."""
        r = await client.post('/api/events', json={'title': title})
        assert r.status_code == 200
        return r.json()

    async def test_status_transitions_happy_path(self, admin_client):
        e = await self._create_event(admin_client)
        eid = e['id']

        # open → in_progress
        r = await admin_client.post(
            f'/api/events/{eid}/status',
            json={'status': 'in_progress'},
        )
        assert r.status_code == 200
        assert r.json()['status'] == 'in_progress'

        # in_progress → resolved
        r = await admin_client.post(
            f'/api/events/{eid}/status',
            json={'status': 'resolved'},
        )
        assert r.status_code == 200
        assert r.json()['status'] == 'resolved'

        # resolved → closed
        r = await admin_client.post(
            f'/api/events/{eid}/status',
            json={'status': 'closed'},
        )
        assert r.status_code == 200
        assert r.json()['status'] == 'closed'

    async def test_invalid_transition_rejected(self, admin_client):
        e = await self._create_event(admin_client)
        eid = e['id']

        # open → closed is valid, but let's go to closed first
        await admin_client.post(
            f'/api/events/{eid}/status',
            json={'status': 'closed'},
        )

        # closed → in_progress is NOT allowed
        r = await admin_client.post(
            f'/api/events/{eid}/status',
            json={'status': 'in_progress'},
        )
        assert r.status_code == 400

    async def test_confirm_draft(self, admin_client, override_async_session):
        """POST /confirm transitions draft → open."""
        # Create a draft event directly in DB
        async with override_async_session() as db:
            event = Event(title='Draft Event', status='draft')
            db.add(event)
            await db.commit()
            await db.refresh(event)
            eid = event.id

        r = await admin_client.post(f'/api/events/{eid}/confirm')
        assert r.status_code == 200
        assert r.json()['status'] == 'open'

    async def test_dismiss_event(self, admin_client):
        e = await self._create_event(admin_client)
        r = await admin_client.post(f'/api/events/{e["id"]}/dismiss')
        assert r.status_code == 200
        assert r.json()['status'] == 'dismissed'


class TestEventActivities:
    async def test_status_change_logs_activity(self, admin_client):
        r = await admin_client.post(
            '/api/events', json={'title': 'Activity Track'}
        )
        eid = r.json()['id']

        # Transition
        await admin_client.post(
            f'/api/events/{eid}/status',
            json={'status': 'in_progress'},
        )

        # Check activities
        r = await admin_client.get(f'/api/events/{eid}/activities')
        assert r.status_code == 200
        activities = r.json()
        # Should have at least 2: 'created' + 'status_changed'
        actions = [a['action'] for a in activities]
        assert 'created' in actions
        assert 'status_changed' in actions

    async def test_add_note_activity(self, admin_client):
        r = await admin_client.post('/api/events', json={'title': 'Note Event'})
        eid = r.json()['id']

        r = await admin_client.post(
            f'/api/events/{eid}/activities',
            json={
                'content': 'This is a note',
                'action': 'note_added',
            },
        )
        assert r.status_code == 200
        assert r.json()['content'] == 'This is a note'
        assert r.json()['action'] == 'note_added'


class TestEventFilters:
    async def test_filter_by_status(self, admin_client):
        # Create two events with different statuses
        await admin_client.post('/api/events', json={'title': 'Open one'})
        r2 = await admin_client.post(
            '/api/events', json={'title': 'Closed one'}
        )
        await admin_client.post(
            f'/api/events/{r2.json()["id"]}/status',
            json={'status': 'closed'},
        )

        r = await admin_client.get('/api/events?status=open')
        assert r.status_code == 200
        events = r.json()
        assert all(e['status'] == 'open' for e in events)

    async def test_filter_by_store(self, admin_client):
        # Create a store
        sr = await admin_client.post(
            '/api/stores', json={'name': 'Event Store'}
        )
        sid = sr.json()['id']

        # Create events with and without store
        await admin_client.post(
            '/api/events',
            json={'title': 'Store event', 'store_id': sid},
        )
        await admin_client.post('/api/events', json={'title': 'No store event'})

        r = await admin_client.get(f'/api/events?store_id={sid}')
        assert r.status_code == 200
        assert all(e['store_id'] == sid for e in r.json())


class TestEventDelete:
    async def test_delete_cascades_activities(
        self, admin_client, override_async_session
    ):
        """Delete event → activities also removed."""
        r = await admin_client.post(
            '/api/events', json={'title': 'Delete Cascade'}
        )
        eid = r.json()['id']

        # Add activity
        await admin_client.post(
            f'/api/events/{eid}/activities',
            json={'content': 'note', 'action': 'note_added'},
        )

        # Verify activity exists
        r = await admin_client.get(f'/api/events/{eid}/activities')
        assert len(r.json()) >= 2  # created + note

        # Delete event
        r = await admin_client.delete(f'/api/events/{eid}')
        assert r.status_code == 200

        # Verify event gone
        r = await admin_client.get(f'/api/events/{eid}')
        assert r.status_code == 404

        # Verify activities gone (check DB directly)
        async with override_async_session() as db:
            result = await db.execute(
                select(EventActivity).where(EventActivity.event_id == eid)
            )
            assert result.scalars().all() == []

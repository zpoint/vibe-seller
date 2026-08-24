"""Lone surrogates must never reach a UTF-8 encode.

Claude's stream-json can emit an unpaired surrogate (JSON escapes them
losslessly, so ``json.loads`` hands one straight through). Persisting it
raised ``UnicodeEncodeError: surrogates not allowed`` from inside the
SQLite bind, which reached the agent as an opaque tool failure.

These tests pin the guard at the encode boundary — the column type and
``sanitize_text`` — so no future writer has to remember it.
"""

import pytest
from sqlalchemy import (
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select as sa_select,
)

from app.text_utils import SafeText, sanitize_text

pytestmark = pytest.mark.unit

# An unpaired low surrogate: what a truncated CJK/emoji sequence yields.
LONE = '\udcad'


class TestSanitizeText:
    def test_clean_text_is_returned_unchanged(self):
        text = 'Weekly report — 报告 ✅ done'
        assert sanitize_text(text) is text

    def test_lone_surrogate_becomes_replacement_char(self):
        assert sanitize_text(f'a{LONE}b') == 'a�b'

    def test_result_is_utf8_encodable(self):
        # The actual contract: the output can be persisted at all.
        sanitize_text(f'report {LONE} tail').encode('utf-8')

    def test_every_surrogate_in_the_range_is_replaced(self):
        raw = ''.join(chr(cp) for cp in (0xD800, 0xDBFF, 0xDC00, 0xDFFF))
        assert sanitize_text(raw) == '�' * 4

    def test_valid_astral_chars_survive(self):
        # 😀 is a real code point, not a surrogate pair, in Python.
        text = 'emoji 😀 and 𝕏'
        assert sanitize_text(text) == text

    def test_empty_string(self):
        assert sanitize_text('') == ''


class TestSafeTextColumn:
    """A real INSERT through the driver — the crash site itself."""

    @staticmethod
    def _table_and_engine():
        meta = MetaData()
        table = Table(
            'probe',
            meta,
            Column('id', String, primary_key=True),
            Column('body', SafeText),
        )
        engine = create_engine('sqlite://')
        meta.create_all(engine)
        return table, engine

    def test_insert_with_lone_surrogate_succeeds(self):
        table, engine = self._table_and_engine()
        with engine.begin() as conn:
            conn.execute(
                insert(table).values(id='t1', body=f'result {LONE} text')
            )
            stored = conn.execute(
                sa_select(table.c.body).where(table.c.id == 't1')
            ).scalar_one()
        assert stored == 'result � text'

    def test_plain_text_column_still_raises(self):
        """Proves the test above is testing SafeText, not SQLite."""
        meta = MetaData()
        table = Table(
            'probe_plain',
            meta,
            Column('id', String, primary_key=True),
            Column('body', String),
        )
        engine = create_engine('sqlite://')
        meta.create_all(engine)
        with pytest.raises(UnicodeEncodeError):
            with engine.begin() as conn:
                conn.execute(
                    insert(table).values(id='t1', body=f'result {LONE} text')
                )

    def test_none_passes_through(self):
        table, engine = self._table_and_engine()
        with engine.begin() as conn:
            conn.execute(insert(table).values(id='t2', body=None))
            stored = conn.execute(
                sa_select(table.c.body).where(table.c.id == 't2')
            ).scalar_one()
        assert stored is None


class TestAgentTextColumnsUseTheGuard:
    """The columns agents actually write must carry the guarded type.

    A plain ``Text`` here is the regression: it compiles, passes every
    other test, and only fails in production on the one submission that
    happens to carry a surrogate.
    """

    @pytest.mark.parametrize(
        'model_path,fields',
        [
            (
                'app.models.task.Task',
                (
                    'result',
                    'submitted_result',
                    'accepted_result',
                    'error',
                    'plan',
                    'review_gaps',
                    'transcript_tail',
                    'todos',
                ),
            ),
            ('app.models.task_message.TaskMessage', ('content',)),
            ('app.models.task_log.TaskLog', ('content',)),
            ('app.models.schedule_state.ScheduleState', ('value',)),
        ],
    )
    def test_column_type(self, model_path, fields):
        module_name, cls_name = model_path.rsplit('.', 1)
        module = __import__(module_name, fromlist=[cls_name])
        model = getattr(module, cls_name)
        for field in fields:
            col = model.__table__.c[field]
            assert isinstance(col.type, SafeText), (
                f'{cls_name}.{field} is {col.type!r}; agent-written text '
                'columns must use SafeText (see app/text_utils.py)'
            )


class TestAgentFacingRequestModels:
    """Agent-facing request fields must sanitize at the boundary.

    Persisting is not the last consumer — the result gates hand the text
    to a language detector that encodes to UTF-8, so a surrogate took
    down a submission BEFORE any write. Cleaning in the request model
    means every consumer downstream is safe.

    Add new MCP-facing models here. A plain ``str`` on one of these is
    the regression: it works for every input except the one that
    matters.
    """

    @pytest.mark.parametrize(
        'model_path,fields',
        [
            (
                'app.routers.task_submission.SetTaskResultRequest',
                ('result', 'incomplete'),
            ),
            ('app.routers.tasks_files.SetTaskErrorRequest', ('error',)),
            ('app.schemas.workspace.FileWriteRequest', ('content',)),
            (
                'app.schemas.workspace.SkillSaveRequest',
                ('skill_md', 'files'),
            ),
            ('app.schemas.task.TaskCreate', ('title', 'description')),
            (
                'app.routers.tasks_schedule_state.SetScheduleStateRequest',
                ('value',),
            ),
            ('app.schemas.wecom_bot.WeComBotSendRequest', ('content',)),
            (
                'app.routers.workspace_assistant.MessageRequest',
                ('content',),
            ),
        ],
    )
    def test_field_sanitizes_surrogates(self, model_path, fields):
        module_name, cls_name = model_path.rsplit('.', 1)
        module = __import__(module_name, fromlist=[cls_name])
        model = getattr(module, cls_name)
        for field in fields:
            info = model.model_fields[field]
            probe = self._probe_value(info.annotation)
            payload = {
                name: self._probe_value(f.annotation, clean=True)
                for name, f in model.model_fields.items()
                if f.is_required()
            }
            payload[field] = probe
            cleaned = getattr(model(**payload), field)
            flat = self._flatten(cleaned)
            assert LONE not in flat, (
                f'{cls_name}.{field} passed a lone surrogate through; '
                'annotate it with SafeStr (see app/text_utils.py)'
            )

    @staticmethod
    def _probe_value(annotation, clean=False):
        """A value of the right shape, carrying a surrogate unless clean."""
        text = 'probe' if clean else f'probe {LONE}'
        origin = str(annotation)
        if 'list' in origin:
            return [text]
        if 'dict' in origin:
            return {'notes.md': text}
        return text

    @staticmethod
    def _flatten(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return ''.join(str(v) for v in value)
        if isinstance(value, dict):
            return ''.join(str(v) for v in value.values())
        return str(value)

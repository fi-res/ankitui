import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ankitui.core import Collection  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def col(tmp_path):
    c = Collection(str(tmp_path / "collection.anki2"))
    c.sched.fuzz_enabled = False
    yield c
    if c.db:
        c.close()


def add_basic(col, front, back="", did=1, notetype="Basic", tags=()):
    nt = col.notetype_by_name(notetype)
    note = col.new_note(nt)
    note.fields = [front, back] + [""] * (len(nt["flds"]) - 2)
    note.tags = list(tags)
    col.add_note(note, did)
    return note


def answer_next(col, ease):
    qc = col.sched.get_next_card()
    assert qc is not None
    col.sched.answer_card(qc.card, qc.states, ease, 1000)
    return col.get_card(qc.card.id)

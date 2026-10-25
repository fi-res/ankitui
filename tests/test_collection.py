import pytest

from ankitui.core import Collection, consts
from ankitui.core.search import SearchError
from ankitui.core.template import render_cloze

from conftest import add_basic


def test_reopen_persists(tmp_path):
    path = str(tmp_path / "c.anki2")
    col = Collection(path)
    add_basic(col, "hello", "world")
    col.add_normal_deck("A::B")
    col.close()
    col = Collection(path)
    assert col.note_count() == 1
    assert col.deck_id_by_name("A::B") and col.deck_id_by_name("A")
    col.close()


def test_card_generation(col):
    add_basic(col, "f", "b", notetype="Basic (and reversed card)")
    add_basic(col, "f2", "b2", notetype="Basic (optional reversed card)")
    assert col.card_count() == 3
    nt = col.notetype_by_name("Cloze")
    note = col.new_note(nt)
    note.fields = ["{{c1::a}} {{c3::b}}", ""]
    assert col.add_note(note, 1) == 2
    ords = sorted(c.ord for c in col.cards_of_note(note.id))
    assert ords == [0, 2]


def test_update_note_adds_cards(col):
    note = add_basic(col, "f", "", notetype="Basic (optional reversed card)")
    assert col.card_count() == 1
    note.fields[1] = "back"
    note.fields[2] = "y"
    assert col.update_note(note) == 1


def test_render(col):
    note = add_basic(col, "<b>Q</b>", "A")
    card = col.cards_of_note(note.id)[0]
    r = col.render_card(card)
    assert r.question == "<b>Q</b>"
    assert "A" in r.answer and "<hr id=answer>" in r.answer


def test_cloze_render():
    text = "{{c1::Paris::city}} is in {{c2::France}}"
    assert render_cloze(text, 0, True) == '<span class="cloze">[city]</span> is in France'
    assert render_cloze(text, 1, False) == 'Paris is in <span class="cloze">France</span>'


def test_duplicates(col):
    add_basic(col, "same")
    nt = col.notetype_by_name("Basic")
    note = col.new_note(nt)
    note.fields = ["same", ""]
    assert col.note_is_duplicate_or_empty(note) == "duplicate"
    note.fields = ["", "x"]
    assert col.note_is_duplicate_or_empty(note) == "empty"


def test_search(col):
    a = add_basic(col, "apple pie", "x", tags=["food::fruit"])
    did = col.add_normal_deck("Other")
    add_basic(col, "banana", "y", did=did, tags=["food"])
    assert len(col.find_cards("apple")) == 1
    assert len(col.find_cards("tag:food")) == 2
    assert len(col.find_cards("tag:food -apple")) == 1
    assert len(col.find_cards("deck:Other")) == 1
    assert len(col.find_cards("deck:Default or deck:Other")) == 2
    assert len(col.find_cards("front:ban*")) == 1
    assert len(col.find_cards("is:new")) == 2
    assert len(col.find_cards(f"nid:{a.id}")) == 1
    assert len(col.find_cards('"apple pie"')) == 1
    with pytest.raises(SearchError):
        col.find_cards("is:nonsense")


def test_remove_deck(col):
    did = col.add_normal_deck("X")
    add_basic(col, "a", did=did)
    col.remove_deck(did)
    assert col.card_count() == 0 and col.note_count() == 0
    assert col.get_deck(did) is None


def test_rename_deck_moves_children(col):
    did = col.add_normal_deck("A::B::C")
    col.rename_deck(col.deck_id_by_name("A"), "Z")
    assert col.get_deck(did)["name"] == "Z::B::C"
    with pytest.raises(ValueError):
        col.rename_deck(col.deck_id_by_name("Z"), "Z::B::Q")


def test_forget_and_set_due(col):
    add_basic(col, "a")
    cid = col.find_cards("")[0]
    col.sched.set_due_date([cid], 3)
    card = col.get_card(cid)
    assert card.type == consts.CARD_TYPE_REV and card.due == col.today + 3
    col.sched.forget_cards([cid])
    assert col.get_card(cid).type == consts.CARD_TYPE_NEW

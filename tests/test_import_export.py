import os
import sqlite3
import zipfile

from ankitui.core import Collection, consts
from ankitui.core.exporting import export_apkg, export_colpkg, export_notes_text
from ankitui.core.importing import TextImportOptions, import_apkg, import_colpkg, import_text

from conftest import FIXTURES, add_basic, answer_next


def test_import_modern_apkg(col):
    res = import_apkg(col, os.path.join(FIXTURES, "modern.apkg"))
    assert res.notes_added == 6 and res.cards_added == 7 and res.media_added == 1
    did = col.deck_id_by_name("Lang::French")
    conf = col.config_for_deck(did)
    assert conf["name"] == "Fast" and conf["new"]["delays"] == [1.0, 5.0, 15.0] and conf["new"]["perDay"] == 7
    assert os.path.exists(os.path.join(col.media_dir, "cat.png"))
    counts = col.sched.deck_counts(did)
    assert (counts.new, counts.learning) == (3, 3)
    # importing again adds nothing
    res = import_apkg(col, os.path.join(FIXTURES, "modern.apkg"))
    assert res.notes_added == 0 and res.cards_added == 0


def test_import_legacy_and_colpkg(col):
    res = import_apkg(col, os.path.join(FIXTURES, "legacy.apkg"))
    assert res.notes_added == 7 and res.cards_added == 9
    assert col.deck_id_by_name("Geo")


def test_replace_with_colpkg(tmp_path):
    path = str(tmp_path / "c.anki2")
    Collection(path).close()
    import_colpkg(os.path.join(FIXTURES, "modern.colpkg"), path)
    col = Collection(path)
    assert col.card_count() == 9 and col.note_count() == 7
    assert col.notetype_by_name("Cloze")
    col.close()


def test_apkg_roundtrip(col, tmp_path):
    did = col.add_normal_deck("Spanish::Verbs")
    for i in range(3):
        add_basic(col, f"hablar {i}", f"to speak {i}", did=did, tags=["verb"])
    open(os.path.join(col.media_dir, "x.jpg"), "wb").write(b"img")
    add_basic(col, '<img src="x.jpg">', "pic", did=did)
    col.set_current_deck(did)
    answer_next(col, 4)
    out = str(tmp_path / "out.apkg")
    notes, cards = export_apkg(col, out, did)
    assert (notes, cards) == (4, 4)
    names = zipfile.ZipFile(out).namelist()
    assert "collection.anki21" in names and "media" in names

    other = Collection(str(tmp_path / "other" / "c.anki2"))
    res = import_apkg(other, out)
    assert res.notes_added == 4 and res.media_added == 1 and res.revlog_added == 1
    reviewed = other.db.execute("SELECT count() FROM cards WHERE type = 2").fetchone()[0]
    assert reviewed == 1
    other.close()

    plain = str(tmp_path / "plain.apkg")
    export_apkg(col, plain, did, include_scheduling=False)
    db_path = str(tmp_path / "x.anki2")
    with zipfile.ZipFile(plain) as zf, open(db_path, "wb") as f:
        f.write(zf.read("collection.anki21"))
    db = sqlite3.connect(db_path)
    assert db.execute("SELECT count() FROM cards WHERE type != 0").fetchone()[0] == 0
    assert db.execute("SELECT count() FROM revlog").fetchone()[0] == 0
    db.close()


def test_colpkg_export(col, tmp_path):
    add_basic(col, "a")
    out = str(tmp_path / "c.colpkg")
    export_colpkg(col, out)
    target = str(tmp_path / "new" / "c.anki2")
    os.makedirs(os.path.dirname(target))
    import_colpkg(out, target)
    other = Collection(target)
    assert other.note_count() == 1
    other.close()


def test_text_roundtrip(col, tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("#separator:tab\n#tags column:3\nchat\tcat\tanimal\nchien\tdog\tanimal\n\tempty\n", encoding="utf-8")
    nt = col.notetype_by_name("Basic")
    res = import_text(col, str(src), TextImportOptions(notetype_id=int(nt["id"]), deck_id=1))
    assert res.notes_added == 2 and res.notes_skipped == 1
    assert len(col.find_cards("tag:animal")) == 2
    res = import_text(col, str(src), TextImportOptions(notetype_id=int(nt["id"]), deck_id=1))
    assert res.notes_added == 0
    out = tmp_path / "out.txt"
    assert export_notes_text(col, str(out)) == 2
    assert "chat\tcat\tanimal" in out.read_text(encoding="utf-8")


def test_csv_autodetect(col, tmp_path):
    src = tmp_path / "in.csv"
    src.write_text('front,back\n"a, b",c\n', encoding="utf-8")
    nt = col.notetype_by_name("Basic")
    res = import_text(col, str(src), TextImportOptions(notetype_id=int(nt["id"]), deck_id=1))
    assert res.notes_added == 2
    assert len(col.find_cards('"front:a, b"')) == 1

import os

import pytest

from ankitui.core import Collection
from ankitui.core.sync import CollectionSyncer, HttpSyncClient, MediaSyncer, SyncError, login, media_db_path

from conftest import add_basic, answer_next
from fake_sync_server import FakeSyncServer


def test_login_and_transport():
    with FakeSyncServer() as srv:
        assert login("user", "pass", srv.url) == "HKEY"
        path, header, body = srv.requests[-1]
        assert path == "sync/hostKey" and header["v"] == 11 and body == {"u": "user", "p": "pass"}
        with pytest.raises(SyncError) as err:
            login("user", "wrong", srv.url)
        assert err.value.kind == "auth"


def test_meta_redirect_and_no_changes(col):
    with FakeSyncServer(redirect_meta_once=True) as srv:
        srv.col_mod, srv.col_scm = col.mod, col.scm
        client = HttpSyncClient("HKEY", srv.url)
        result = CollectionSyncer(col, client).sync()
        assert result.action == "no_changes"
        assert result.new_endpoint == srv.url + "shard/"
        assert client.endpoint.endswith("/shard/")


def test_full_sync_required(col):
    add_basic(col, "q")
    with FakeSyncServer() as srv:
        srv.col_mod, srv.col_scm = 1, 1
        result = CollectionSyncer(col, HttpSyncClient("HKEY", srv.url)).sync()
        assert result.action == "full_required"
        assert result.upload_ok and not result.download_ok


def test_media_sync_up_to_date(col):
    with FakeSyncServer() as srv:
        stats = MediaSyncer(col.media_dir, media_db_path(col), HttpSyncClient("HKEY", srv.url)).sync()
        assert stats == {"downloaded": 0, "uploaded": 0, "deleted": 0}


def test_pending_changes_bump_mod(col):
    col.ls = col.mod = 1000
    col.save(bump_mod=False)
    add_basic(col, "q")
    col.mod = col.ls
    col.save(bump_mod=False)
    meta = CollectionSyncer(col, HttpSyncClient("HKEY", "http://127.0.0.1:1/")).local_meta()
    assert meta.modified > 1000


# --------------------------------------------------------------- integration

SERVER = os.environ.get("ANKITUI_SYNC_TEST_URL")  # e.g. http://127.0.0.1:27701/
USER = os.environ.get("ANKITUI_SYNC_TEST_USER", "alice:secret")


@pytest.mark.skipif(not SERVER, reason="set ANKITUI_SYNC_TEST_URL to run against a real Anki sync server")
def test_two_clients_through_real_server(tmp_path):
    username, password = USER.split(":", 1)
    hkey = login(username, password, SERVER)

    def sync(col, full=None):
        result = CollectionSyncer(col, HttpSyncClient(hkey, SERVER)).sync()
        if result.action == "full_required":
            syncer = CollectionSyncer(col, HttpSyncClient(hkey, SERVER))
            if full == "upload":
                syncer.full_upload()
            else:
                syncer.full_download()
                col = Collection(col.path)
        MediaSyncer(col.media_dir, media_db_path(col), HttpSyncClient(hkey, SERVER)).sync()
        return col

    a = Collection(str(tmp_path / "a" / "c.anki2"))
    a.sched.fuzz_enabled = False
    did = a.add_normal_deck("Shared")
    for i in range(3):
        add_basic(a, f"note {i}", did=did)
    open(os.path.join(a.media_dir, "a.png"), "wb").write(b"aaa")
    a = sync(a, "upload")

    b = Collection(str(tmp_path / "b" / "c.anki2"))
    b = sync(b, "download")
    assert b.note_count() == 3 and os.path.exists(os.path.join(b.media_dir, "a.png"))

    b.set_current_deck(b.deck_id_by_name("Shared"))
    answer_next(b, 3)
    add_basic(b, "from b", did=b.deck_id_by_name("Shared"), tags=["btag"])
    b = sync(b)

    a = sync(a)
    assert a.note_count() == 4
    assert a.db.execute("SELECT count() FROM revlog").fetchone()[0] == 1
    assert "btag" in a.tags
    a.remove_notes(a.find_notes('"note 0"'))
    a = sync(a)
    b = sync(b)
    assert b.note_count() == 3
    a.close()
    b.close()

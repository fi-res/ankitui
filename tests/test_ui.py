import pytest
import os

from ankitui.ui.app import AnkiTUI
from ankitui.ui.browser import BrowserScreen
from ankitui.ui.custom_study import CustomStudyScreen
from ankitui.ui.deck_options import DeckOptionsScreen, format_steps, parse_steps
from ankitui.ui.import_export import ExportScreen
from ankitui.ui.overview import OverviewScreen
from ankitui.ui.render import html_to_text
from ankitui.ui.reviewer import ReviewerScreen

from conftest import add_basic


def make_app(tmp_path):
    os.environ.setdefault("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return AnkiTUI(str(tmp_path / "c.anki2"))


async def test_add_and_study(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        await pilot.press(*"bonjour", "tab", *"hello", "ctrl+s")
        await pilot.pause()
        assert app.col.card_count() == 1
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ReviewerScreen)
        await pilot.press("space")
        await pilot.pause()
        assert app.screen.showing_answer
        await pilot.press("4")
        await pilot.pause()
        assert app.col.db.execute("SELECT count() FROM revlog").fetchone()[0] == 1
        assert isinstance(app.screen, OverviewScreen)


async def test_type_answer(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        add_basic(app.col, "capital of France", "Paris", notetype="Basic (type in the answer)")
        app.screen.refresh_decks()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*"Paris", "enter")
        await pilot.pause()
        assert app.screen.showing_answer and app.screen.typed == "Paris"
        await pilot.press("1")
        await pilot.pause()
        assert app.col.sched.counts().learning == 1


async def test_undo_in_reviewer(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        add_basic(app.col, "a")
        add_basic(app.col, "b")
        app.screen.refresh_decks()
        await pilot.press("enter")
        await pilot.pause()
        first = app.screen.current.card.id
        await pilot.press("space", "3")
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        assert app.screen.current.card.id == first


async def test_custom_study_screen(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        for i in range(3):
            add_basic(app.col, f"q{i}", tags=["t"])
        app.screen.refresh_decks()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CustomStudyScreen)
        app.screen.query_one("#cs-cram").value = True
        await pilot.pause()
        app.screen.query_one("#cs-value").value = "2"
        app.screen.submit()
        await pilot.pause()
        assert app.col.get_deck(app.col.current_deck_id())["name"] == "Custom Study Session"
        assert app.col.sched.counts().new == 2


async def test_options_and_browser_and_export(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(140, 50)) as pilot:
        add_basic(app.col, "apple")
        await pilot.press("o")
        await pilot.pause()
        assert isinstance(app.screen, DeckOptionsScreen)
        app.screen.query_one("#num-new_per_day").value = "5"
        app.screen.query_one("#learn-steps").value = "2m 1h"
        app.screen.action_save()
        await pilot.pause()
        conf = app.col.dconf["1"]
        assert conf["new"]["perDay"] == 5 and conf["new"]["delays"] == [2.0, 60.0]

        await pilot.press("b")
        await pilot.pause()
        assert isinstance(app.screen, BrowserScreen)
        assert app.screen.cids
        await pilot.press("s")
        await pilot.pause()
        assert app.col.get_card(app.screen.cids[0]).queue == -1
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ExportScreen)
        out = str(tmp_path / "deck.apkg")
        app.screen.query_one("#ex-path").value = out
        app.screen.go()
        await pilot.pause()
        assert os.path.exists(out)


def test_steps_parsing():
    assert parse_steps("1m 10m 1h 2d 30s") == [1.0, 10.0, 60.0, 2880.0, 0.5]
    assert format_steps([1.0, 10.0, 60.0, 2880.0, 0.5]) == "1m 10m 1h 2d 30s"


def test_html_render():
    text = html_to_text('<b>bold</b><br><span class="cloze">[...]</span><div>x &amp; y</div>[sound:a.mp3]<img src="c.png">')
    plain = text.plain
    assert "bold" in plain and "[...]" in plain and "x & y" in plain
    assert "♪ a.mp3" in plain and "[image: c.png]" in plain


async def test_stats_and_help_screens(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from ankitui.ui.help import HelpScreen
    from ankitui.ui.stats import StatsScreen, StatsPanel

    app = make_app(tmp_path)
    async with app.run_test(size=(140, 50)) as pilot:
        add_basic(app.col, "a")
        await pilot.press("s")
        await pilot.pause(0.3)
        assert isinstance(app.screen, StatsScreen)
        assert len(app.screen.query(StatsPanel)) == 12
        await pilot.press("p")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        app.action_toggle_icons()
        await pilot.pause()
        assert app.settings["icons"] is False
        app.action_toggle_icons()


async def test_login_and_sync_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from fake_sync_server import FakeSyncServer

    from ankitui.ui.sync import LoginScreen, SyncScreen

    app = make_app(tmp_path)
    with FakeSyncServer() as srv:
        async with app.run_test(size=(120, 40)) as pilot:
            app.settings.pop("sync", None)
            app.settings["sync_backend"] = "builtin"
            srv.col_mod, srv.col_scm = app.col.mod, app.col.scm
            await pilot.press("y")
            await pilot.pause()
            assert isinstance(app.screen, LoginScreen)
            screen = app.screen
            screen.query_one("#username").value = "user"
            screen.query_one("#password").value = "wrong"
            screen.query_one("#custom").value = True
            screen.query_one("#endpoint").value = srv.url
            screen.submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "incorrect" in str(screen.query_one("#login-error").render())
            screen.query_one("#password").value = "pass"
            screen.submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.settings["sync"]["hkey"] == "HKEY"
            assert isinstance(app.screen, SyncScreen)
            for _ in range(20):
                await app.workers.wait_for_complete()
                await pilot.pause(0.05)
                if not app.screen.running:
                    break
            text = str(app.screen.query_one("#sync-messages").render())
            assert "up to date" in text
            await pilot.press("escape")


@pytest.mark.skipif(
    not os.environ.get("ANKITUI_SYNC_TEST_URL"), reason="set ANKITUI_SYNC_TEST_URL to run against a real Anki sync server"
)
async def test_sync_with_anki_library(tmp_path, monkeypatch):
    pytest.importorskip("anki")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from ankitui.ui.sync import FullSyncChoice, SyncScreen, store_auth
    from ankitui.core.ankilib_sync import login

    url = os.environ["ANKITUI_SYNC_TEST_URL"]
    user, password = os.environ.get("ANKITUI_SYNC_TEST_USER", "alice:secret").split(":", 1)
    app = make_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        add_basic(app.col, "via library")
        hkey, endpoint = login(user, password, url)
        store_auth(app, user, hkey, endpoint)
        app.settings["sync_backend"] = "auto"
        await pilot.press("y")
        for _ in range(100):
            await pilot.pause(0.1)
            if isinstance(app.screen, FullSyncChoice):
                app.screen.upload()
            if isinstance(app.screen, SyncScreen) and not app.screen.running:
                break
        assert app.screen.use_library
        assert "Collection uploaded" in str(app.screen.query_one("#sync-messages").render())
        assert app.col is not None and app.col.note_count() == 1


async def test_settings_screen(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from ankitui.ui.dialogs import ConfirmDialog
    from ankitui.ui.settings_screen import SettingsScreen
    from ankitui.ui.sync import store_auth

    app = make_app(tmp_path)
    async with app.run_test(size=(120, 50)) as pilot:
        store_auth(app, "alice", "HKEY", None)
        mod = app.col.mod
        await pilot.press("comma")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        # opening the screen must not modify the collection
        assert app.col.mod == mod
        assert "alice" in str(screen.query_one("#account-status").render())
        assert screen.query_one("#logout").display and not screen.query_one("#login").display

        screen.query_one("#sync-media").value = False
        screen.query_one("#parent-limits").value = True
        screen.query_one("#rollover").value = "6"
        screen.query_one("#learn-ahead").value = "5000"
        await pilot.pause()
        assert app.settings["sync_media"] is False
        assert app.col.conf["applyAllParentLimits"] is True
        assert app.col.conf["rollover"] == 6
        assert app.col.conf["collapseTime"] == 1200  # out of range values are rejected
        assert "between" in str(screen.query_one("#study-error").render())

        screen.logout()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        app.screen.dismiss(True)
        await pilot.pause()
        assert "sync" not in app.settings
        assert screen.query_one("#login").display
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)


async def test_deck_page_actions(tmp_path):
    from ankitui.ui.deck_picker import DeckPickerScreen
    from ankitui.ui.dialogs import ConfirmDialog, PromptDialog

    app = make_app(tmp_path)
    async with app.run_test(size=(120, 50)) as pilot:
        did = app.col.add_normal_deck("Spanish")
        add_basic(app.col, "hola", "hello", did=did)
        app.screen.refresh_decks(did)
        await pilot.press("v")
        await pilot.pause()
        assert isinstance(app.screen, OverviewScreen)

        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, PromptDialog)
        app.screen.dismiss("Languages::Spanish")
        await pilot.pause()
        assert app.col.get_deck(did)["name"] == "Languages::Spanish"

        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ExportScreen) and app.screen.did == did
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("delete")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        app.screen.dismiss(True)
        await pilot.pause()
        assert app.col.get_deck(did) is None
        assert isinstance(app.screen, DeckPickerScreen)

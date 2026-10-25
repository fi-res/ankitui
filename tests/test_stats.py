import time

from ankitui.core.stats import Stats
from ankitui.ui import icons
from ankitui.ui.charts import heatmap, hbar, proportion_bar

from conftest import add_basic, answer_next


_seq = [0]


def _revlog(col, days_ago, ease=3, rtype=1, last_ivl=5, taken=5000):
    _seq[0] += 1
    rid = int((time.time() - days_ago * 86400) * 1000) - _seq[0]
    col.db.execute(
        "INSERT INTO revlog VALUES (?,?,?,?,?,?,?,?,?)",
        (rid, col.find_cards("")[0], -1, ease, 3, last_ivl, 2500, taken, rtype),
    )


def test_today_and_counts(col):
    for i in range(3):
        add_basic(col, f"q{i}")
    answer_next(col, 1)
    answer_next(col, 3)
    s = Stats(col)
    t = s.today_stats()
    assert t.cards == 2 and t.again == 1 and t.learn == 2
    c = s.card_counts()
    assert (c.new, c.learning, c.total) == (1, 2, 3)


def test_calendar_streaks(col):
    add_basic(col, "q")
    for d in (0, 1, 2, 5, 6):
        _revlog(col, d)
    cal = Stats(col).calendar(30)
    assert cal.current_streak == 3
    assert cal.longest_streak == 3
    assert cal.days_studied == 5 and cal.total == 5


def test_history_buttons_retention(col):
    add_basic(col, "q")
    _revlog(col, 0, ease=1, last_ivl=30)
    _revlog(col, 1, ease=3, last_ivl=30)
    _revlog(col, 1, ease=3, last_ivl=3)
    _revlog(col, 2, ease=2, rtype=0, last_ivl=-600)
    s = Stats(col)
    hist = s.review_history(7)
    assert hist.total() == 4 and hist.total("mature") == 2 and hist.total("learn") == 1
    b = s.buttons(7)
    assert b.counts["mature"] == [1, 0, 1, 0] and b.counts["learning"] == [0, 1, 0, 0]
    rows = {r.label: r for r in s.true_retention()}
    assert rows["Today"].mature == 0.0
    assert rows["Yesterday"].total == 100.0
    assert rows["Last week"].count == 3


def test_future_due_and_histograms(col):
    add_basic(col, "a")
    add_basic(col, "b")
    cids = col.find_cards("")
    col.sched.set_due_date([cids[0]], 1)
    col.sched.set_due_date([cids[1]], 3)
    s = Stats(col)
    f = s.future_due(7)
    assert f.tomorrow == 1 and f.days[3] == 1 and f.total == 2
    assert s.intervals().total == 2
    assert s.added(7)[-1] == 2


def test_deck_scope(col):
    did = col.add_normal_deck("Other")
    add_basic(col, "a")
    add_basic(col, "b", did=did)
    assert Stats(col, did).card_counts().total == 1
    assert Stats(col).card_counts().total == 2


def test_charts_render():
    assert len(hbar(5, 10, 20, "red").plain) == 20
    assert len(proportion_bar([(1, "red"), (3, "blue")], 12).plain) == 12
    assert "Mon" in heatmap({0: 5, -1: 2}, 10).plain


def test_icon_fallback():
    icons.set_enabled(False)
    try:
        assert icons.icon("new") == "+ "
    finally:
        icons.set_enabled(True)
    assert icons.icon("new") == " "

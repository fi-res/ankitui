import time

import pytest

from ankitui.core import consts
from ankitui.core.sched.scheduler import CRAM_ALL, CRAM_NEW, CustomStudyError, answer_button_time_collapsible

from conftest import add_basic, answer_next


def test_new_card_flow(col):
    add_basic(col, "q", "a")
    assert col.sched.counts().new == 1
    qc = col.sched.get_next_card()
    assert col.sched.describe_next_states(qc.states) == ["<1m", "<6m", "<10m", "4d"]
    card = answer_next(col, 3)
    assert card.type == consts.CARD_TYPE_LRN and card.queue == consts.QUEUE_LRN
    assert card.left == 1
    counts = col.sched.counts()
    assert (counts.new, counts.learning) == (0, 1)
    card = answer_next(col, 3)
    assert card.type == consts.CARD_TYPE_REV and card.ivl == 1
    assert card.due == col.today + 1
    revlog = col.db.execute("SELECT ease, ivl, lastIvl, type FROM revlog ORDER BY id").fetchall()
    assert revlog == [(3, -600, 0, 0), (3, 1, -600, 0)]


def test_daily_limits(col):
    conf = col.dconf["1"]
    conf["new"]["perDay"] = 3
    col.save_config(conf)
    for i in range(5):
        add_basic(col, f"q{i}")
    assert col.sched.counts().new == 3
    answer_next(col, 4)
    assert col.sched.counts().new == 2
    col.sched.clear_queues()
    assert col.sched.counts().new == 2
    col.sched.extend_limits(1, 2, 0)
    assert col.sched.counts().new == 4


def test_child_limits_capped_by_parent(col):
    parent = col.add_normal_deck("P")
    child = col.add_normal_deck("P::C")
    small = col.add_config("small")
    small["new"]["perDay"] = 2
    col.save_config(small)
    deck = col.get_deck(child)
    deck["conf"] = small["id"]
    col.save_deck(deck)
    for i in range(4):
        add_basic(col, f"c{i}", did=child)
        add_basic(col, f"p{i}", did=parent)
    assert col.sched.deck_counts(child).new == 2
    assert col.sched.deck_counts(parent).new == 6


def test_bury_new_siblings(col):
    conf = col.dconf["1"]
    conf["new"]["bury"] = True
    col.save_config(conf)
    add_basic(col, "front", "back", notetype="Basic (and reversed card)")
    assert col.sched.counts().new == 1
    answer_next(col, 3)
    queues = sorted(r[0] for r in col.db.execute("SELECT queue FROM cards"))
    assert queues == [consts.QUEUE_SCHED_BURIED, consts.QUEUE_LRN]


def test_undo_answer(col):
    add_basic(col, "q")
    qc = col.sched.get_next_card()
    answer_next(col, 3)
    entry = col.sched.undo()
    assert entry is not None
    card = col.get_card(qc.card.id)
    assert card.queue == consts.QUEUE_NEW and card.reps == 0
    assert col.db.execute("SELECT count() FROM revlog").fetchone()[0] == 0
    assert col.get_deck(1)["newToday"][1] == 0
    assert col.sched.get_next_card().card.id == qc.card.id


def test_leech_suspends(col):
    conf = col.dconf["1"]
    conf["lapse"]["leechFails"] = 1
    conf["lapse"]["leechAction"] = consts.LEECH_SUSPEND
    col.save_config(conf)
    note = add_basic(col, "q")
    answer_next(col, 4)
    card = col.get_card(col.find_cards("")[0])
    card.due = col.today
    col.update_card(card)
    col.sched.clear_queues()
    card = answer_next(col, 1)
    assert card.queue == consts.QUEUE_SUSPENDED
    assert col.get_note(note.id).has_tag("leech")


def test_suspend_and_bury(col):
    add_basic(col, "a")
    add_basic(col, "b")
    cids = col.find_cards("")
    col.sched.suspend_cards([cids[0]])
    col.sched.bury_cards([cids[1]])
    assert col.sched.counts().new == 0
    col.sched.unsuspend_or_unbury(cids)
    assert col.sched.counts().new == 2


def test_unbury_on_new_day(col):
    add_basic(col, "a")
    col.sched.bury_cards(col.find_cards(""))
    col.conf["lastUnburied"] = col.today - 1
    col.sched.clear_queues()
    assert col.sched.counts().new == 1


def test_filtered_deck_preview_returns_card(col):
    add_basic(col, "a")
    did, count = col.sched.create_filtered_deck("F", "is:new", 10, consts.DYN_ADDED, resched=False)
    assert count == 1
    col.set_current_deck(did)
    qc = col.sched.get_next_card()
    assert col.sched.describe_next_states(qc.states) == ["<1m", "<10m", "End", "End"]
    card = answer_next(col, 3)
    assert card.did == 1 and card.odid == 0 and card.queue == consts.QUEUE_NEW


def test_filtered_deck_rescheduling(col):
    add_basic(col, "a")
    did, _ = col.sched.create_filtered_deck("F", "deck:Default", 10, consts.DYN_DUE, resched=True)
    col.set_current_deck(did)
    card = answer_next(col, 3)
    assert card.did == did and card.queue == consts.QUEUE_LRN and card.odue == card.due
    card = answer_next(col, 3)
    assert card.did == 1 and card.odid == 0 and card.queue == consts.QUEUE_REV


def test_empty_filtered_deck_restores(col):
    add_basic(col, "a")
    did, _ = col.sched.create_filtered_deck("F", "", 10, consts.DYN_RANDOM, resched=True)
    col.sched.empty_filtered_deck(did)
    card = col.get_card(col.find_cards("")[0])
    assert card.did == 1 and card.odid == 0 and card.due == 1


def test_custom_study_review_ahead_and_forgot(col):
    add_basic(col, "a")
    answer_next(col, 4)  # easy -> review due in 4 days
    with pytest.raises(CustomStudyError):
        col.sched.custom_study(1, "forgot", 1)
    did = col.sched.custom_study(1, "ahead", 5)
    assert col.get_deck(did)["name"] == consts.CUSTOM_STUDY_DECK_NAME
    assert col.current_deck_id() == did
    qc = col.sched.get_next_card()
    assert qc is not None
    # early review: good keeps at least the scheduled interval
    assert qc.states.good.scheduled_days >= 4


def test_custom_study_cram_with_tags(col):
    add_basic(col, "a", tags=["x"])
    add_basic(col, "b", tags=["y"])
    did = col.sched.custom_study(1, "cram", 100, cram_kind=CRAM_NEW, tags_include=["x"])
    assert col.sched.counts().new == 1
    col.set_current_deck(1)
    did2 = col.sched.custom_study(1, "cram", 100, cram_kind=CRAM_ALL, tags_exclude=["x"])
    assert did2 == did
    assert len(col.find_cards(f"deck:\"{consts.CUSTOM_STUDY_DECK_NAME}\"")) == 1


def test_mix_and_review_order(col):
    for i in range(3):
        add_basic(col, f"n{i}")
    cids = col.find_cards("")
    for cid in cids[:2]:
        card = col.get_card(cid)
        card.type = card.queue = consts.QUEUE_REV
        card.ivl, card.due, card.factor = 5, col.today, 2500
        col.update_card(card)
    conf = col.dconf["1"]
    conf["newMix"] = consts.MIX_BEFORE_REVIEWS
    col.save_config(conf)
    assert col.sched.get_next_card().kind == "new"
    conf["newMix"] = consts.MIX_AFTER_REVIEWS
    col.save_config(conf)
    assert col.sched.get_next_card().kind == "review"


def test_answer_button_labels():
    assert answer_button_time_collapsible(0, 1200) == "End"
    assert answer_button_time_collapsible(60, 1200) == "<1m"
    assert answer_button_time_collapsible(86400 * 4, 1200) == "4d"
    assert answer_button_time_collapsible(86400 * 45, 1200) == "1.5mo"


def test_day_timing(col):
    t = col.timing_today()
    assert t.next_day_at > time.time()
    assert t.next_day_at - time.time() <= 86400

"""Which Slack message subtypes count as real human input.

Two failures, one in each direction, both seen for real:

* The original skip-list named only ("bot_message", "channel_join"), so when JC
  left the channel the engine ran a full extraction on "<@U…> has left the
  channel" and then crashed threading a reply onto a system message.
* Replacing it with an allow-list of (None, "file_share") then swallowed a
  broker's "yes" sign-off, because replying in a thread with "also send to
  channel" ticked arrives as subtype "thread_broadcast". Nothing happened and
  nothing was logged.

So the allow-list is right, but it has to name every subtype a person can
produce by typing in a channel — not just the two that were obvious.
"""
from __future__ import annotations

import slack_engine


def accepted(subtype=None, bot_id=None) -> bool:
    event = {"text": "yes", "ts": "1.1"}
    if subtype is not None:
        event["subtype"] = subtype
    if bot_id:
        event["bot_id"] = bot_id
    return slack_engine.is_human_message(event)


def test_a_plain_message_is_accepted():
    assert accepted() is True


def test_a_message_with_files_is_accepted():
    assert accepted("file_share") is True


def test_a_thread_reply_broadcast_to_the_channel_is_accepted():
    # The sign-off that went missing. A broker ticking "also send to channel" is
    # still a broker approving.
    assert accepted("thread_broadcast") is True


def test_a_me_message_is_accepted():
    assert accepted("me_message") is True


def test_the_bots_own_messages_are_ignored():
    assert accepted(None, bot_id="B123") is False
    assert accepted("bot_message") is False


def test_channel_membership_noise_is_ignored():
    for sub in ("channel_join", "channel_leave", "group_join", "group_leave"):
        assert accepted(sub) is False, sub


def test_edits_and_deletions_are_ignored():
    for sub in ("message_changed", "message_deleted", "message_replied"):
        assert accepted(sub) is False, sub


def test_channel_housekeeping_is_ignored():
    for sub in ("channel_topic", "channel_purpose", "channel_name",
                "channel_archive", "pinned_item", "bot_add"):
        assert accepted(sub) is False, sub


def test_an_unknown_future_subtype_is_ignored():
    # The whole point of an allow-list: whatever Slack adds next stays out until
    # someone decides it is human input.
    assert accepted("something_slack_invents_in_2027") is False

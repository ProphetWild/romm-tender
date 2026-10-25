"""Which slot a start outside the game page confirms without asking, read off the setup answer."""

from __future__ import annotations

from typing import Any

import pytest

from domain.save_slot_setup import slot_to_confirm


def _answer(**fields: Any) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "has_local_saves": False,
        "local_files": [],
        "server_slots": [],
        "default_slot": "default",
        "slot_confirmed": False,
        "active_slot": None,
        "recommended_action": "show_wizard",
        "server_query_failed": False,
    }
    answer.update(fields)
    return answer


class TestTheSlotConfirmedWithoutAsking:
    def test_the_default_where_local_saves_exist_and_the_server_has_none(self):
        assert slot_to_confirm(_answer(recommended_action="auto_confirm_default", has_local_saves=True)) == "default"

    def test_the_default_where_neither_side_holds_a_save(self):
        assert slot_to_confirm(_answer()) == "default"

    def test_the_default_slot_the_settings_name(self):
        assert slot_to_confirm(_answer(default_slot="deck")) == "deck"

    def test_none_where_the_server_holds_slots_the_user_must_choose_between(self):
        assert slot_to_confirm(_answer(server_slots=[{"slot": "pc", "count": 1}])) is None

    @pytest.mark.parametrize("action", ["server_unreachable", "not_found", "romm_refused", "device_not_registered"])
    def test_none_where_the_servers_slots_could_not_be_read(self, action):
        """An empty ``server_slots`` there is no statement that the server has none."""
        assert slot_to_confirm(_answer(recommended_action=action, server_query_failed=True)) is None

    @pytest.mark.parametrize("action", ["server_unreachable", "not_found", "romm_refused", "device_not_registered"])
    def test_none_for_a_failed_read_even_without_its_flag(self, action):
        assert slot_to_confirm(_answer(recommended_action=action)) is None

    def test_none_for_a_failed_read_under_a_reason_not_known_here(self):
        assert slot_to_confirm(_answer(recommended_action="something_new", server_query_failed=True)) is None

    @pytest.mark.parametrize("slot", ["", None, 3])
    def test_none_without_a_default_slot_to_confirm(self, slot):
        assert slot_to_confirm(_answer(default_slot=slot)) is None

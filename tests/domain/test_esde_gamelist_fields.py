"""Tests for ``domain/esde_gamelist_fields.py`` — a RomM ROM's values for Tender's ES-DE gamelist fields."""

from __future__ import annotations

from typing import Any

import pytest

from domain.esde_gamelist import TENDER_FIELDS
from domain.esde_gamelist_fields import gamelist_fields

CHRONO_RELEASE_MS = 794880000000  # 1995-03-11T00:00:00Z
CHRONO_RELEASE_LAST_SECOND_MS = 794966399000  # 1995-03-11T23:59:59Z


def _rom(**overrides: Any) -> dict[str, Any]:
    rom: dict[str, Any] = {
        "id": 42,
        "name": "Chrono Trigger",
        "summary": "A time travel RPG.",
        "metadatum": {
            "genres": ["Role-playing (RPG)", "Adventure"],
            "companies": ["Square", "Square Soft", "Nintendo"],
            "first_release_date": CHRONO_RELEASE_MS,
            "average_rating": 92.5,
            "game_modes": ["Single player"],
            "player_count": "1",
        },
    }
    rom.update(overrides)
    return rom


def _metadatum(**overrides: Any) -> dict[str, Any]:
    return {**_rom()["metadatum"], **overrides}


class TestAFullRom:
    def test_every_field_is_read(self):
        assert gamelist_fields(_rom()) == {
            "name": "Chrono Trigger",
            "desc": "A time travel RPG.",
            "releasedate": "19950311T000000",
            "developer": "Square",
            "publisher": "Square Soft",
            "genre": "Role-playing (RPG), Adventure",
            "players": "1",
        }

    def test_it_answers_only_tenders_fields(self):
        assert set(gamelist_fields(_rom())) <= set(TENDER_FIELDS)


class TestTheReleaseDate:
    def test_it_is_the_utc_day_in_es_de_s_form(self):
        rom = _rom(metadatum=_metadatum(first_release_date=CHRONO_RELEASE_LAST_SECOND_MS))

        assert gamelist_fields(rom)["releasedate"] == "19950311T000000"

    def test_an_absent_date_is_not_written(self):
        assert "releasedate" not in gamelist_fields(_rom(metadatum=_metadatum(first_release_date=None)))

    @pytest.mark.parametrize("milliseconds", [0, 43200000])
    def test_a_date_on_es_de_s_default_day_is_not_written(self, milliseconds):
        """ES-DE reads ``19700101T000000`` as no date and leaves it out of every entry it writes (``MetaData.cpp``)."""
        rom = _rom(metadatum=_metadatum(first_release_date=milliseconds))

        assert "releasedate" not in gamelist_fields(rom)

    def test_a_date_before_1970_is_written(self):
        rom = _rom(metadatum=_metadatum(first_release_date=-86400000 * 365))

        assert gamelist_fields(rom)["releasedate"] == "19690101T000000"

    def test_a_year_below_one_thousand_keeps_four_digits(self):
        """ES-DE's date is ``YYYYMMDDTHHMMSS`` (``MetaData.cpp``); glibc's ``strftime("%Y")`` writes 999 as ``999``."""
        rom = _rom(metadatum=_metadatum(first_release_date=-30628713600000))  # 0999-06-01T00:00:00Z

        assert gamelist_fields(rom)["releasedate"] == "09990601T000000"

    def test_a_date_no_calendar_holds_is_not_written(self):
        rom = _rom(metadatum=_metadatum(first_release_date=10**20))

        assert "releasedate" not in gamelist_fields(rom)


class TestTheCompanies:
    def test_one_company_is_the_developer_only(self):
        fields = gamelist_fields(_rom(metadatum=_metadatum(companies=["Square"])))

        assert fields["developer"] == "Square"
        assert "publisher" not in fields

    def test_no_company_writes_neither(self):
        fields = gamelist_fields(_rom(metadatum=_metadatum(companies=[])))

        assert "developer" not in fields
        assert "publisher" not in fields

    def test_a_company_that_is_not_a_name_is_passed_over(self):
        fields = gamelist_fields(_rom(metadatum=_metadatum(companies=[None, "  ", "Square", 7, "Nintendo"])))

        assert fields["developer"] == "Square"
        assert fields["publisher"] == "Nintendo"


class TestTheOtherFields:
    def test_no_genre_writes_none(self):
        assert "genre" not in gamelist_fields(_rom(metadatum=_metadatum(genres=[])))

    def test_a_genre_that_is_not_a_name_is_passed_over(self):
        fields = gamelist_fields(_rom(metadatum=_metadatum(genres=["RPG", "", None, " Adventure "])))

        assert fields["genre"] == "RPG, Adventure"

    def test_no_player_count_writes_none(self):
        assert "players" not in gamelist_fields(_rom(metadatum=_metadatum(player_count="")))

    def test_a_numeric_player_count_is_written_as_text(self):
        assert gamelist_fields(_rom(metadatum=_metadatum(player_count=2)))["players"] == "2"

    def test_a_player_count_that_is_not_a_count_is_not_written(self):
        assert "players" not in gamelist_fields(_rom(metadatum=_metadatum(player_count=True)))

    @pytest.mark.parametrize("summary", [None, "", "   "])
    def test_no_summary_writes_no_description(self, summary):
        assert "desc" not in gamelist_fields(_rom(summary=summary))

    def test_values_are_trimmed(self):
        fields = gamelist_fields(_rom(name="  Chrono Trigger \n", summary="\n A time travel RPG. \n"))

        assert fields["name"] == "Chrono Trigger"
        assert fields["desc"] == "A time travel RPG."

    def test_a_rom_without_metadata_answers_its_name(self):
        assert gamelist_fields({"name": "Chrono Trigger"}) == {"name": "Chrono Trigger"}

    @pytest.mark.parametrize("name", [None, "", 7])
    def test_a_rom_without_a_name_answers_no_name(self, name):
        assert "name" not in gamelist_fields(_rom(name=name))


class TestMetadataThatCannotBeRead:
    @pytest.mark.parametrize(
        "metadatum",
        [
            {"first_release_date": "soon"},
            {"average_rating": "high"},
            {"first_release_date": [1]},
            {"average_rating": {}},
        ],
    )
    def test_only_the_name_is_answered(self, metadatum):
        assert gamelist_fields(_rom(metadatum=metadatum)) == {"name": "Chrono Trigger"}

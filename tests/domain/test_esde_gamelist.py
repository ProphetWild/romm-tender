"""Tests for ``domain/esde_gamelist.py`` — merging Tender's fields into an ES-DE ``gamelist.xml``.

The fixtures are written the way ES-DE writes the file (pugixml: an XML
declaration without encoding, one tab per level, one child per line), with the
fields ES-DE and the user own beside Tender's.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from domain.esde_gamelist import (
    TENDER_FIELDS,
    GamelistMerge,
    UnreadableGamelist,
    _scan,
    merge_gamelist,
)

CHRONO = "./Chrono Trigger (USA).zip"
METROID = "./Super Metroid (Japan, USA).sfc"
FF7 = "./Final Fantasy VII.m3u"

DECLARATION = '<?xml version="1.0"?>\n'


def _game(path: str, *children: str, tag: str = "game") -> str:
    lines = [f"\t<{tag}>\n", f"\t\t<path>{path}</path>\n"]
    lines += [f"\t\t{child}\n" for child in children]
    lines.append(f"\t</{tag}>\n")
    return "".join(lines)


def _gamelist(*entries: str, before: str = "") -> str:
    return DECLARATION + before + "<gameList>\n" + "".join(entries) + "</gameList>\n"


METROID_ENTRY = _game(
    METROID,
    "<name>Super Metroid</name>",
    "<desc>Samus &amp; the last Metroid.</desc>",
    "<favorite>true</favorite>",
    "<playcount>12</playcount>",
)
HACKS_FOLDER = _game("./Hacks", "<name>Hacks</name>", tag="folder")
ALTERNATIVE_EMULATOR = "<alternativeEmulator>\n\t<label>Snes9x - Current</label>\n</alternativeEmulator>\n"


def _merge(
    current: str | None,
    owned: dict[str, dict[str, str]],
    written: dict[str, dict[str, str]] | None = None,
) -> GamelistMerge:
    merge = merge_gamelist(current, owned, written or {})
    assert isinstance(merge, GamelistMerge), merge
    return merge


def _esde_reads(text: str, path: str) -> dict[str, str]:
    """What ES-DE reads for *path*: the first child of each field name of the last ``<game>`` naming it."""
    body = text.removeprefix(DECLARATION)
    game_list = ET.fromstring(f"<w>{body}</w>").find("gameList")
    assert game_list is not None
    games = [game for game in game_list.findall("game") if game.findtext("path") == path]
    assert games, f"no <game> for {path}"
    return {child.tag: child.text or "" for child in reversed(list(games[-1])) if child.tag != "path"}


class TestTheSevenFields:
    def test_the_fields_are_the_seven_es_de_shows(self):
        assert TENDER_FIELDS == ("name", "desc", "releasedate", "developer", "publisher", "genre", "players")


class TestANewGamelist:
    def test_it_is_written_the_way_es_de_writes_one(self):
        merge = _merge(
            None,
            {
                CHRONO: {
                    "name": "Chrono Trigger",
                    "desc": "A time travel RPG.",
                    "releasedate": "19950311T000000",
                    "developer": "Square",
                    "publisher": "Square",
                    "genre": "Role-playing (RPG)",
                    "players": "1",
                },
                METROID: {"name": "Super Metroid"},
            },
        )

        assert merge.text == _gamelist(
            _game(
                CHRONO,
                "<name>Chrono Trigger</name>",
                "<desc>A time travel RPG.</desc>",
                "<releasedate>19950311T000000</releasedate>",
                "<developer>Square</developer>",
                "<publisher>Square</publisher>",
                "<genre>Role-playing (RPG)</genre>",
                "<players>1</players>",
            ),
            _game(METROID, "<name>Super Metroid</name>"),
        )
        assert merge.changed is True

    def test_the_fields_are_written_in_es_de_s_order_whatever_order_they_are_given_in(self):
        merge = _merge(None, {CHRONO: {"players": "1", "name": "Chrono Trigger"}})

        assert merge.text == _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>", "<players>1</players>"))

    def test_a_field_with_an_empty_value_is_not_written(self):
        merge = _merge(None, {CHRONO: {"name": "Chrono Trigger", "desc": "", "genre": ""}})

        assert merge.text == _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>"))
        assert merge.written == {CHRONO: {"name": "Chrono Trigger"}}

    def test_the_record_holds_every_field_written(self):
        merge = _merge(None, {CHRONO: {"name": "Chrono Trigger", "players": "1"}})

        assert merge.written == {CHRONO: {"name": "Chrono Trigger", "players": "1"}}

    def test_an_entry_with_nothing_to_write_is_not_appended(self):
        merge = _merge(None, {CHRONO: {"name": ""}})

        assert merge.changed is False
        assert merge.written == {CHRONO: {}}

    def test_nothing_owned_writes_nothing(self):
        merge = _merge(None, {})

        assert merge.changed is False
        assert merge.written == {}

    @pytest.mark.parametrize("text", ["", "  \n", "\ufeff", "\ufeff\n"])
    def test_an_empty_file_is_read_as_no_file(self, text):
        merge = _merge(text, {CHRONO: {"name": "Chrono Trigger"}})

        assert merge.text == _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>"))
        assert merge.changed is True


class TestEverythingTenderDoesNotOwn:
    def test_every_other_element_and_everything_between_them_stays_byte_for_byte(self):
        chrono = _game(CHRONO, "<name>Chrono Trigger</name>")
        before = DECLARATION + ALTERNATIVE_EMULATOR + "<gameList>\n" + METROID_ENTRY + "\t<!-- kept by hand -->\n"
        after = HACKS_FOLDER + "</gameList>\n"
        current = before + chrono + after

        merge = _merge(
            current, {CHRONO: {"name": "Chrono Trigger", "players": "1"}}, {CHRONO: {"name": "Chrono Trigger"}}
        )

        assert merge.text.startswith(before)
        assert merge.text.endswith(after)
        assert merge.text[len(before) : -len(after)] == _game(
            CHRONO, "<name>Chrono Trigger</name>", "<players>1</players>"
        )

    def test_an_entry_tender_no_longer_owns_is_left_as_it_is(self):
        chrono = _game(CHRONO, "<name>Chrono Trigger</name>", "<desc>Old.</desc>")
        current = _gamelist(METROID_ENTRY, chrono)

        merge = _merge(current, {}, {CHRONO: {"name": "Chrono Trigger", "desc": "Old."}})

        assert merge.text == current
        assert merge.changed is False

    def test_the_record_of_an_entry_tender_no_longer_owns_is_carried(self):
        current = _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>"))

        merge = _merge(current, {METROID: {"name": "Super Metroid"}}, {CHRONO: {"name": "Chrono Trigger"}})

        assert merge.written == {CHRONO: {"name": "Chrono Trigger"}, METROID: {"name": "Super Metroid"}}

    def test_a_folder_element_naming_tenders_path_is_not_tenders_entry(self):
        folder = _game(FF7, "<name>Someone's folder</name>", tag="folder")
        current = _gamelist(folder)

        merge = _merge(current, {FF7: {"name": "Final Fantasy VII"}})

        assert merge.text == _gamelist(folder, _game(FF7, "<name>Final Fantasy VII</name>"))


class TestTendersFieldsInItsEntry:
    def test_an_absent_field_is_added_after_the_last_child(self):
        current = _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>", "<playcount>3</playcount>"))

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger", "desc": "A time travel RPG.", "players": "1"}})

        assert merge.text == _gamelist(
            _game(
                CHRONO,
                "<name>Chrono Trigger</name>",
                "<playcount>3</playcount>",
                "<desc>A time travel RPG.</desc>",
                "<players>1</players>",
            )
        )
        assert merge.written == {CHRONO: {"desc": "A time travel RPG.", "players": "1"}}

    def test_a_field_tender_wrote_is_replaced_by_the_new_value(self):
        current = _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>", "<desc>Old summary.</desc>"))

        merge = _merge(
            current,
            {CHRONO: {"name": "Chrono Trigger", "desc": "New summary."}},
            {CHRONO: {"name": "Chrono Trigger", "desc": "Old summary."}},
        )

        assert merge.text == _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>", "<desc>New summary.</desc>"))
        assert merge.written == {CHRONO: {"name": "Chrono Trigger", "desc": "New summary."}}

    def test_a_field_the_user_edited_is_kept_and_the_record_keeps_tenders_last_value(self):
        current = _gamelist(_game(CHRONO, "<name>Chrono Trigger (my copy)</name>"))

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger!"}}, {CHRONO: {"name": "Chrono Trigger"}})

        assert merge.text == current
        assert merge.changed is False
        assert merge.written == {CHRONO: {"name": "Chrono Trigger"}}

    def test_a_field_the_user_edited_stays_the_users_on_every_later_sync(self):
        current = _gamelist(_game(CHRONO, "<name>Chrono Trigger (my copy)</name>"))
        first = _merge(current, {CHRONO: {"name": "Chrono Trigger!"}}, {CHRONO: {"name": "Chrono Trigger"}})

        second = _merge(first.text, {CHRONO: {"name": "Chrono Trigger"}}, first.written)

        assert second.text == current
        assert second.written == {CHRONO: {"name": "Chrono Trigger"}}

    def test_a_field_tender_never_wrote_is_kept_and_not_recorded(self):
        current = _gamelist(_game(CHRONO, "<desc>Scraped by ES-DE.</desc>"))

        merge = _merge(current, {CHRONO: {"desc": "From RomM."}})

        assert merge.text == current
        assert merge.written == {CHRONO: {}}

    def test_a_field_tender_wrote_that_is_now_empty_is_removed_with_its_line(self):
        current = _gamelist(
            _game(CHRONO, "<name>Chrono Trigger</name>", "<desc>Old summary.</desc>", "<favorite>true</favorite>")
        )

        merge = _merge(
            current, {CHRONO: {"name": "Chrono Trigger"}}, {CHRONO: {"name": "Chrono Trigger", "desc": "Old summary."}}
        )

        assert merge.text == _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>", "<favorite>true</favorite>"))
        assert merge.written == {CHRONO: {"name": "Chrono Trigger"}}

    def test_a_field_sharing_its_line_is_removed_alone(self):
        current = _gamelist(
            "\t<game><path>./Chrono Trigger (USA).zip</path><desc>Old.</desc><hidden>true</hidden></game>\n"
        )

        merge = _merge(current, {CHRONO: {}}, {CHRONO: {"desc": "Old."}})

        assert merge.text == _gamelist("\t<game><path>./Chrono Trigger (USA).zip</path><hidden>true</hidden></game>\n")

    def test_a_removed_last_field_gives_its_place_to_the_added_ones(self):
        current = _gamelist(_game(CHRONO, "<desc>Old summary.</desc>"))

        merge = _merge(current, {CHRONO: {"players": "1"}}, {CHRONO: {"desc": "Old summary."}})

        assert merge.text == _gamelist(_game(CHRONO, "<players>1</players>"))

    def test_a_field_the_user_edited_stays_even_when_tender_has_no_value_now(self):
        current = _gamelist(_game(CHRONO, "<desc>My notes.</desc>"))

        merge = _merge(current, {CHRONO: {}}, {CHRONO: {"desc": "Old summary."}})

        assert merge.text == current
        assert merge.written == {CHRONO: {"desc": "Old summary."}}

    def test_a_field_the_user_cleared_is_added_again(self):
        current = _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>"))

        merge = _merge(
            current,
            {CHRONO: {"name": "Chrono Trigger", "desc": "A time travel RPG."}},
            {CHRONO: {"name": "Chrono Trigger", "desc": "A time travel RPG."}},
        )

        assert merge.text == _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>", "<desc>A time travel RPG.</desc>"))

    @pytest.mark.parametrize("empty", ["<desc/>", "<desc />", "<desc></desc>"])
    def test_an_empty_field_is_filled_in_place(self, empty):
        """ES-DE reads an empty element as the field's default (``MetaDataList::createFromXML``)."""
        current = _gamelist(_game(CHRONO, empty, "<favorite>true</favorite>"))

        merge = _merge(current, {CHRONO: {"desc": "A time travel RPG."}})

        assert merge.text == _gamelist(_game(CHRONO, "<desc>A time travel RPG.</desc>", "<favorite>true</favorite>"))
        assert merge.written == {CHRONO: {"desc": "A time travel RPG."}}

    def test_a_field_holding_only_whitespace_is_empty_to_es_de_and_filled(self):
        """pugixml drops whitespace-only text unless ``parse_ws_pcdata`` is set, and ES-DE's ``load_file`` sets none."""
        current = _gamelist(_game(CHRONO, "<desc> \n </desc>"))

        merge = _merge(current, {CHRONO: {"desc": "A time travel RPG."}})

        assert merge.text == _gamelist(_game(CHRONO, "<desc>A time travel RPG.</desc>"))

    def test_a_value_holding_only_whitespace_is_not_written(self):
        merge = _merge(None, {CHRONO: {"name": "Chrono Trigger", "desc": " \n\t"}})

        assert merge.written == {CHRONO: {"name": "Chrono Trigger"}}

    def test_an_empty_field_tender_has_no_value_for_is_left_alone(self):
        current = _gamelist(_game(CHRONO, "<desc/>"))

        merge = _merge(current, {CHRONO: {"name": ""}})

        assert merge.text == current

    def test_a_fields_attributes_are_kept_when_its_value_is_replaced(self):
        current = _gamelist(_game(CHRONO, '<desc lang="en">Old.</desc>'))

        merge = _merge(current, {CHRONO: {"desc": "New."}}, {CHRONO: {"desc": "Old."}})

        assert merge.text == _gamelist(_game(CHRONO, '<desc lang="en">New.</desc>'))

    def test_a_self_closed_fields_attributes_are_kept_when_it_is_filled(self):
        current = _gamelist(_game(CHRONO, '<desc lang="en" />'))

        merge = _merge(current, {CHRONO: {"desc": "New."}})

        assert merge.text == _gamelist(_game(CHRONO, '<desc lang="en">New.</desc>'))

    @pytest.mark.parametrize(
        "field",
        [
            "<desc><![CDATA[Old.]]></desc>",
            "<desc>Old.<!-- a note --></desc>",
            "<desc>Old.<b>bold</b></desc>",
        ],
    )
    def test_a_field_holding_markup_is_never_tenders(self, field):
        current = _gamelist(_game(CHRONO, field))

        merge = _merge(current, {CHRONO: {"desc": "New."}}, {CHRONO: {"desc": "Old."}})

        assert merge.text == current
        assert merge.written == {CHRONO: {"desc": "Old."}}

    def test_the_first_of_two_same_named_fields_is_the_one_es_de_reads_and_judged(self):
        current = _gamelist(_game(CHRONO, "<desc>Old.</desc>", "<desc>Second.</desc>"))

        merge = _merge(current, {CHRONO: {"desc": "New."}}, {CHRONO: {"desc": "Old."}})

        assert merge.text == _gamelist(_game(CHRONO, "<desc>New.</desc>", "<desc>Second.</desc>"))

    def test_every_other_child_of_tenders_entry_stays_byte_for_byte(self):
        others = (
            "<sortname>Chrono Trigger, The</sortname>",
            "<rating>0.9</rating>",
            "<favorite>true</favorite>",
            "<kidgame>false</kidgame>",
            "<hidden>false</hidden>",
            "<playcount>3</playcount>",
            "<altemulator>Snes9x - Current</altemulator>",
            "<lastplayed>20261020T201500</lastplayed>",
        )
        current = _gamelist(_game(CHRONO, "<name>Old</name>", *others))

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}}, {CHRONO: {"name": "Old"}})

        assert merge.text == _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>", *others))

    def test_an_unchanged_entry_changes_nothing(self):
        current = _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>", "<players>1</players>"))
        fields = {"name": "Chrono Trigger", "players": "1"}

        merge = _merge(current, {CHRONO: fields}, {CHRONO: fields})

        assert merge.text == current
        assert merge.changed is False
        assert merge.written == {CHRONO: fields}

    def test_an_entity_spelled_otherwise_than_tender_would_is_the_same_value(self):
        current = _gamelist(_game(CHRONO, "<publisher>Square&#32;&#x26;&#32;Enix</publisher>"))

        merge = _merge(current, {CHRONO: {"publisher": "Square & Enix"}}, {CHRONO: {"publisher": "Square & Enix"}})

        assert merge.text == current
        assert merge.changed is False


class TestFindingTendersEntry:
    def test_a_path_with_an_entity_in_it_is_matched(self):
        path = "./Mario & Luigi (USA).zip"
        current = _gamelist(_game("./Mario &amp; Luigi (USA).zip", "<name>Old</name>"))

        merge = _merge(current, {path: {"name": "Mario & Luigi"}}, {path: {"name": "Old"}})

        assert merge.text == _gamelist(_game("./Mario &amp; Luigi (USA).zip", "<name>Mario &amp; Luigi</name>"))

    @pytest.mark.parametrize(
        "spelling",
        [
            ".//Final Fantasy VII.m3u",
            "./Final Fantasy VII.m3u/",
            "./Disc/../Final Fantasy VII.m3u",
            "./././Final Fantasy VII.m3u",
        ],
    )
    def test_a_path_es_de_resolves_to_the_same_file_is_matched(self, spelling):
        """ES-DE compares paths through ``getGenericPath`` and ``getCanonicalPath`` (es-core FileSystemUtil.cpp)."""
        current = _gamelist(_game(spelling, "<name>Old</name>"))

        merge = _merge(current, {FF7: {"name": "Final Fantasy VII"}}, {FF7: {"name": "Old"}})

        assert merge.text == _gamelist(_game(spelling, "<name>Final Fantasy VII</name>"))

    @pytest.mark.parametrize(
        "spelling",
        [
            " ./Chrono Trigger (USA).zip ",
            "\n\t\t\t./Chrono Trigger (USA).zip\n\t\t",
            "Chrono Trigger (USA).zip",
            "/home/deck/retrodeck/roms/snes/Chrono Trigger (USA).zip",
            "./../Chrono Trigger (USA).zip",
        ],
    )
    def test_a_path_es_de_does_not_read_as_relative_to_the_system_folder_is_not_tenders_entry(self, spelling):
        """``resolveRelativePath`` resolves only a path starting ``./``; nothing else is the system folder's file."""
        other = _game(spelling, "<name>Someone's</name>")
        current = _gamelist(other)

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}}, {CHRONO: {"name": "Someone's"}})

        assert merge.text == _gamelist(other, _game(CHRONO, "<name>Chrono Trigger</name>"))

    def test_of_two_entries_for_one_path_the_last_is_tenders(self):
        """``parseGamelist`` replaces a game's metadata with each entry naming it, so ES-DE shows the last one."""
        first = _game(CHRONO, "<name>Old</name>", "<favorite>true</favorite>")
        current = _gamelist(first, _game(CHRONO, "<name>Old</name>"))

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}}, {CHRONO: {"name": "Old"}})

        assert merge.text == _gamelist(first, _game(CHRONO, "<name>Chrono Trigger</name>"))

    def test_an_entry_without_a_path_is_never_tenders(self):
        pathless = "\t<game>\n\t\t<name>Orphan</name>\n\t</game>\n"
        current = _gamelist(pathless)

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}})

        assert merge.text == _gamelist(pathless, _game(CHRONO, "<name>Chrono Trigger</name>"))

    def test_an_entry_whose_path_holds_markup_is_never_tenders(self):
        odd = _game("./Chrono<!-- x --> Trigger (USA).zip", "<name>Odd</name>")
        current = _gamelist(odd)

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}})

        assert merge.text == _gamelist(odd, _game(CHRONO, "<name>Chrono Trigger</name>"))


class TestAppendingAnEntry:
    def test_it_goes_after_the_last_element_of_the_gamelist(self):
        current = _gamelist(METROID_ENTRY, HACKS_FOLDER)

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}})

        assert merge.text == _gamelist(METROID_ENTRY, HACKS_FOLDER, _game(CHRONO, "<name>Chrono Trigger</name>"))
        assert merge.written == {CHRONO: {"name": "Chrono Trigger"}}

    def test_entries_are_appended_in_the_order_they_are_owned_in(self):
        current = _gamelist(HACKS_FOLDER)

        merge = _merge(current, {METROID: {"name": "Super Metroid"}, CHRONO: {"name": "Chrono Trigger"}})

        assert merge.text == _gamelist(
            HACKS_FOLDER, _game(METROID, "<name>Super Metroid</name>"), _game(CHRONO, "<name>Chrono Trigger</name>")
        )

    def test_it_goes_after_the_last_element_and_before_what_follows_it(self):
        current = _gamelist(METROID_ENTRY, "\t<!-- end -->\n")

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}})

        assert merge.text == _gamelist(METROID_ENTRY, _game(CHRONO, "<name>Chrono Trigger</name>"), "\t<!-- end -->\n")

    @pytest.mark.parametrize("empty", ["<gameList/>", "<gameList />"])
    def test_a_self_closed_gamelist_is_opened(self, empty):
        """pugixml saves an element with no children self-closed, ``<gameList />``."""
        current = DECLARATION + ALTERNATIVE_EMULATOR + empty + "\n"

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}})

        assert (
            merge.text
            == DECLARATION
            + ALTERNATIVE_EMULATOR
            + "<gameList>\n"
            + _game(CHRONO, "<name>Chrono Trigger</name>")
            + "</gameList>\n"
        )

    def test_a_self_closed_gamelists_attributes_are_kept(self):
        current = DECLARATION + '<gameList version="1"/>\n'

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}})

        assert (
            merge.text
            == DECLARATION + '<gameList version="1">\n' + _game(CHRONO, "<name>Chrono Trigger</name>") + "</gameList>\n"
        )

    def test_an_empty_gamelist_gets_its_closing_tag_on_a_line_of_its_own(self):
        current = DECLARATION + "<gameList></gameList>\n"

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}})

        assert merge.text == _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>"))

    def test_a_gamelist_holding_only_a_comment_keeps_it_after_the_new_entry(self):
        current = DECLARATION + "<gameList>\n\t<!-- nothing yet -->\n</gameList>\n"

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}})

        assert merge.text == _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>"), "\t<!-- nothing yet -->\n")

    def test_the_path_is_written_escaped(self):
        path = "./Mario & Luigi <Beta>.zip"

        merge = _merge(None, {path: {"name": "Mario & Luigi"}})

        assert "<path>./Mario &amp; Luigi &lt;Beta&gt;.zip</path>" in merge.text
        assert _esde_reads(merge.text, path) == {"name": "Mario & Luigi"}


class TestValues:
    def test_a_value_is_escaped_and_reads_back_as_it_was_given(self):
        desc = "Fight <Lavos> & save \"time\" — 'now'."

        merge = _merge(None, {CHRONO: {"desc": desc}})

        assert "<desc>Fight &lt;Lavos&gt; &amp; save \"time\" — 'now'.</desc>" in merge.text
        assert _esde_reads(merge.text, CHRONO) == {"desc": desc}

    def test_a_multi_line_value_keeps_its_lines(self):
        merge = _merge(None, {CHRONO: {"desc": "One.\nTwo."}})

        assert _esde_reads(merge.text, CHRONO) == {"desc": "One.\nTwo."}

    def test_a_carriage_return_is_read_back_as_a_line_feed_so_it_is_recorded_as_one(self):
        """An XML parser reads every CR LF and lone CR as LF (XML 1.0, "End-of-Line Handling")."""
        merge = _merge(None, {CHRONO: {"desc": "One.\r\nTwo.\rThree."}})

        assert _esde_reads(merge.text, CHRONO) == {"desc": "One.\nTwo.\nThree."}
        assert merge.written == {CHRONO: {"desc": "One.\nTwo.\nThree."}}
        assert _merge(merge.text, {CHRONO: {"desc": "One.\r\nTwo.\rThree."}}, merge.written).changed is False

    def test_a_character_xml_cannot_hold_is_dropped(self):
        merge = _merge(None, {CHRONO: {"desc": "Bad\x00 \x08byte\x1f\ufffe\ud800s."}})

        assert _esde_reads(merge.text, CHRONO) == {"desc": "Bad bytes."}
        assert merge.written == {CHRONO: {"desc": "Bad bytes."}}

    def test_a_value_left_empty_once_unwritable_characters_go_is_not_written(self):
        merge = _merge(None, {CHRONO: {"name": "Chrono Trigger", "desc": "\x00\x01"}})

        assert merge.written == {CHRONO: {"name": "Chrono Trigger"}}


class TestTheFilesOwnStyle:
    def test_a_crlf_file_gets_crlf_in_what_tender_adds(self):
        lf = _gamelist(METROID_ENTRY, _game(CHRONO, "<name>Chrono Trigger</name>"))
        current = lf.replace("\n", "\r\n")

        merge = _merge(
            current, {CHRONO: {"name": "Chrono Trigger", "desc": "One.\nTwo."}, FF7: {"name": "Final Fantasy VII"}}
        )

        assert merge.text == _gamelist(
            METROID_ENTRY,
            _game(CHRONO, "<name>Chrono Trigger</name>", "<desc>One.\nTwo.</desc>"),
            _game(FF7, "<name>Final Fantasy VII</name>"),
        ).replace("\n", "\r\n")

    def test_a_crlf_field_tender_wrote_is_removed_with_its_whole_line(self):
        current = _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>", "<desc>Old.</desc>")).replace("\n", "\r\n")

        merge = _merge(
            current, {CHRONO: {"name": "Chrono Trigger"}}, {CHRONO: {"name": "Chrono Trigger", "desc": "Old."}}
        )

        assert merge.text == _gamelist(_game(CHRONO, "<name>Chrono Trigger</name>")).replace("\n", "\r\n")

    def test_a_file_without_a_declaration_gets_none(self):
        current = "<gameList>\n" + METROID_ENTRY + "</gameList>\n"

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}})

        assert (
            merge.text
            == "<gameList>\n" + METROID_ENTRY + _game(CHRONO, "<name>Chrono Trigger</name>") + "</gameList>\n"
        )

    def test_a_byte_order_mark_is_kept(self):
        current = "\ufeff" + _gamelist(METROID_ENTRY)

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}})

        assert merge.text == "\ufeff" + _gamelist(METROID_ENTRY, _game(CHRONO, "<name>Chrono Trigger</name>"))

    def test_a_declaration_naming_an_encoding_is_read(self):
        current = '<?xml version="1.0" encoding="UTF-8"?>\n<gameList>\n' + METROID_ENTRY + "</gameList>\n"

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}})

        assert merge.text.endswith(_game(CHRONO, "<name>Chrono Trigger</name>") + "</gameList>\n")

    def test_a_space_indented_file_gets_its_own_indentation(self):
        chrono = "  <game>\n    <path>./Chrono Trigger (USA).zip</path>\n    <name>Chrono Trigger</name>\n  </game>\n"
        current = DECLARATION + "<gameList>\n" + chrono + "</gameList>\n"

        merge = _merge(
            current, {CHRONO: {"name": "Chrono Trigger", "players": "1"}, FF7: {"name": "Final Fantasy VII"}}
        )

        assert merge.text == (
            DECLARATION
            + "<gameList>\n"
            + "  <game>\n    <path>./Chrono Trigger (USA).zip</path>\n    <name>Chrono Trigger</name>\n"
            + "    <players>1</players>\n  </game>\n"
            + "  <game>\n    <path>./Final Fantasy VII.m3u</path>\n    <name>Final Fantasy VII</name>\n  </game>\n"
            + "</gameList>\n"
        )

    def test_a_file_on_one_line_stays_on_one_line(self):
        current = DECLARATION + "<gameList><game><path>./Chrono Trigger (USA).zip</path></game></gameList>"

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}, FF7: {"name": "Final Fantasy VII"}})

        assert merge.text == DECLARATION + (
            "<gameList><game><path>./Chrono Trigger (USA).zip</path><name>Chrono Trigger</name></game>"
            "<game><path>./Final Fantasy VII.m3u</path><name>Final Fantasy VII</name></game></gameList>"
        )

    def test_where_the_indentation_cannot_be_read_es_de_s_is_used(self):
        current = (
            DECLARATION
            + "<gameList><!-- c --><game><!-- c --><path>./Chrono Trigger (USA).zip</path></game></gameList>"
        )

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}})

        assert merge.text == DECLARATION + (
            "<gameList><!-- c --><game><!-- c --><path>./Chrono Trigger (USA).zip</path>"
            "\n\t\t<name>Chrono Trigger</name></game></gameList>"
        )

    def test_attributes_holding_angle_brackets_and_quotes_do_not_confuse_the_reading(self):
        odd = (
            '\t<game note="a > b" other=\'"q"\'>\n'
            "\t\t<path>./Chrono Trigger (USA).zip</path>\n\t\t<name>Old</name>\n\t</game>\n"
        )
        current = _gamelist(odd)

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}}, {CHRONO: {"name": "Old"}})

        assert merge.text == _gamelist(odd.replace("<name>Old</name>", "<name>Chrono Trigger</name>"))

    def test_processing_instructions_and_cdata_between_elements_are_read_past(self):
        between = (
            "\t<?tool keep?>\n"
            "\t<!-- <game><path>./Chrono Trigger (USA).zip</path></game> -->\n"
            "\t<![CDATA[<game><path>./Chrono Trigger (USA).zip</path></game>]]>\n"
        )
        current = _gamelist(between, METROID_ENTRY)

        merge = _merge(current, {CHRONO: {"name": "Chrono Trigger"}})

        assert merge.text == _gamelist(between, METROID_ENTRY, _game(CHRONO, "<name>Chrono Trigger</name>"))


class TestAFileThatIsNotAGamelist:
    @pytest.mark.parametrize(
        "text",
        [
            DECLARATION + "<gameList>\n\t<game>\n",
            DECLARATION + "<gameList><game></gameList>",
            DECLARATION + "<gameList>&nbsp;</gameList>",
            DECLARATION + "<gameList>\n\t<game>\n\t\t<path>./A & B.zip</path>\n\t</game>\n</gameList>\n",
            "not xml at all",
            DECLARATION + DECLARATION + "<gameList/>",
            '<?xml version="1.0"',
        ],
    )
    def test_a_file_that_is_not_well_formed_is_refused(self, text):
        assert isinstance(merge_gamelist(text, {CHRONO: {"name": "Chrono Trigger"}}, {}), UnreadableGamelist)

    def test_a_file_without_a_gamelist_element_is_refused(self):
        refusal = merge_gamelist(DECLARATION + "<games/>\n", {CHRONO: {"name": "Chrono Trigger"}}, {})

        assert isinstance(refusal, UnreadableGamelist)
        assert "gameList" in refusal.reason

    def test_a_document_type_declaration_is_refused(self):
        """ES-DE writes none, and pugixml would not expand an entity one declares where ElementTree does."""
        text = DECLARATION + '<!DOCTYPE gameList [<!ENTITY x "y">]>\n<gameList>&x;</gameList>\n'

        assert isinstance(merge_gamelist(text, {CHRONO: {"name": "Chrono Trigger"}}, {}), UnreadableGamelist)

    def test_a_refusal_says_why(self):
        refusal = merge_gamelist("not xml", {}, {})

        assert isinstance(refusal, UnreadableGamelist)
        assert refusal.reason


class TestWhatTheCallerHandsIn:
    def test_a_field_that_is_not_one_of_tenders_is_a_broken_invariant(self):
        with pytest.raises(ValueError, match="favorite"):
            merge_gamelist(None, {CHRONO: {"favorite": "true"}}, {})

    @pytest.mark.parametrize("path", ["Chrono Trigger (USA).zip", "/roms/snes/Chrono.zip", "./", "./.."])
    def test_a_path_that_is_not_in_the_system_folder_is_a_broken_invariant(self, path):
        with pytest.raises(ValueError, match="system folder"):
            merge_gamelist(None, {path: {"name": "Chrono Trigger"}}, {})

    def test_two_paths_naming_one_file_are_a_broken_invariant(self):
        with pytest.raises(ValueError, match="same file"):
            merge_gamelist(None, {FF7: {"name": "A"}, FF7 + "/": {"name": "B"}}, {})


class TestMergingAgain:
    def test_a_second_merge_with_the_same_input_changes_nothing(self):
        current = _gamelist(
            METROID_ENTRY,
            _game(CHRONO, "<name>Mine</name>", "<desc>Old.</desc>", "<favorite>true</favorite>"),
            HACKS_FOLDER,
        )
        owned = {
            CHRONO: {"name": "Chrono Trigger", "desc": "New.", "players": "1"},
            FF7: {"name": "Final Fantasy VII", "genre": "RPG"},
        }
        written = {CHRONO: {"name": "Chrono Trigger", "desc": "Old."}}

        first = _merge(current, owned, written)
        second = _merge(first.text, owned, first.written)

        assert first.changed is True
        assert second.changed is False
        assert second.text == first.text
        assert second.written == first.written


class TestReadingTheStructure:
    def test_a_start_tag_that_cannot_be_read_is_a_broken_invariant(self):
        """The reading runs only over text ElementTree accepted; this is its guard should that ever not hold."""
        with pytest.raises(ValueError, match="tag"):
            _scan("<gameList>< odd></gameList>")

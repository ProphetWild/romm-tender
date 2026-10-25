"""Property-based tests for the ES-DE gamelist merge.

The safety invariant is that a merge never disturbs what is not Tender's: every
element of the file but Tender's own entries, everything between elements, and
every child of Tender's entries that is not one of its fields comes out byte for
byte, whatever the file holds. Beside it: the output always parses, every field
reads afterwards as the rule says it should, and a second merge with the same
input changes nothing. One gamelist bound to one output can show none of these
over the space of files a user's ES-DE and hand edits produce.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from hypothesis import given
from hypothesis import strategies as st

from domain.esde_gamelist import TENDER_FIELDS, GamelistMerge, merge_gamelist

HEAD = '<?xml version="1.0"?>\n<gameList>'
TAIL = "\n</gameList>\n"
OTHER_TAGS = ("favorite", "playcount", "sortname", "lastplayed", "altemulator", "hidden")
STATES = ("absent", "tenders", "edited", "unrecorded", "empty")

_XML_UNWRITABLE = re.compile("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")

safe_text = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E) | st.sampled_from(["\n", "\t", "é", "—", "&"]),
    max_size=8,
)
present_text = safe_text.filter(lambda value: value.strip(" \t\n\r"))
any_text = st.text(max_size=8) | st.sampled_from(["", " ", "\r\n", "a\rb", "\x00", "<&>", "\ufffe"])


def _as_written(value: str) -> str | None:
    """A value as it reads back once written, or ``None`` where nothing is written."""
    cleaned = _XML_UNWRITABLE.sub("", value).replace("\r\n", "\n").replace("\r", "\n")
    return cleaned if cleaned.strip(" \t\n\r") else None


@dataclass
class Entry:
    path: str
    owned: bool
    tenders: bool
    children: list[tuple[str, str | None]] = field(default_factory=list)
    states: dict[str, str] = field(default_factory=dict)

    def text(self) -> str:
        lines = [f"<game>\n\t\t<path>{escape(self.path)}</path>"]
        for tag, value in self.children:
            lines.append(f"\n\t\t<{tag}/>" if value is None else f"\n\t\t<{tag}>{escape(value)}</{tag}>")
        lines.append("\n\t</game>")
        return "".join(lines)


@dataclass
class Scenario:
    entries: list[Entry]
    separators: list[str]
    owned: dict[str, dict[str, str]]
    written: dict[str, dict[str, str]]
    absent_owned: list[str]

    def text(self) -> str:
        return (
            HEAD + "".join(sep + entry.text() for sep, entry in zip(self.separators, self.entries, strict=True)) + TAIL
        )


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    entries: list[Entry] = []
    for index in range(draw(st.integers(0, 3))):
        tags = draw(st.lists(st.sampled_from(TENDER_FIELDS + OTHER_TAGS), unique=True, max_size=4))
        entry = Entry(path=f"./Other & {index}.zip", owned=False, tenders=False)
        entry.children = [(tag, draw(st.none() | safe_text)) for tag in tags]
        entries.append(entry)

    owned: dict[str, dict[str, str]] = {}
    written: dict[str, dict[str, str]] = {}
    absent_owned: list[str] = []
    for index in range(3):
        path = f"./Tender <{index}>.zip"
        is_owned = draw(st.booleans())
        if is_owned:
            owned[path] = {name: draw(any_text) for name in draw(st.sets(st.sampled_from(TENDER_FIELDS)))}
        record: dict[str, str] = {}
        if not draw(st.booleans()):
            if is_owned:
                absent_owned.append(path)
            for name in TENDER_FIELDS:
                if draw(st.booleans()):
                    record[name] = draw(present_text)
            if record or draw(st.booleans()):
                written[path] = record
            continue
        entry = Entry(path=path, owned=is_owned, tenders=True)
        others = draw(st.lists(st.sampled_from(OTHER_TAGS), unique=True, max_size=2))
        slots: list[tuple[str, str | None]] = [(tag, draw(safe_text)) for tag in others]
        for name in TENDER_FIELDS:
            state = draw(st.sampled_from(STATES))
            entry.states[name] = state
            value = draw(present_text)
            if state == "tenders":
                slots.append((name, value))
                record[name] = value
            elif state == "edited":
                slots.append((name, value + "!"))
                record[name] = value
            elif state == "unrecorded":
                slots.append((name, value))
            else:
                if state == "empty":
                    slots.append((name, None))
                if draw(st.booleans()):
                    record[name] = value
        entry.children = draw(st.permutations(slots))
        written[path] = record
        entries.append(entry)

    entries = draw(st.permutations(entries))
    separators = [draw(st.sampled_from(["\n\t", "\n\t<!-- & -->\n\t", "\n\n\t"])) for _ in entries]
    return Scenario(entries, separators, owned, written, absent_owned)


def _merge(scenario: Scenario) -> GamelistMerge:
    merge = merge_gamelist(scenario.text(), scenario.owned, scenario.written)
    assert isinstance(merge, GamelistMerge)
    return merge


def _esde_reads(text: str) -> dict[str, dict[str, str]]:
    """Each ``<game>``'s Tender fields as ES-DE reads them: the first child of each name, a blank one absent."""
    game_list = ET.fromstring("<w>" + text.removeprefix('<?xml version="1.0"?>\n') + "</w>").find("gameList")
    assert game_list is not None
    reads: dict[str, dict[str, str]] = {}
    for game in game_list.findall("game"):
        values: dict[str, str] = {}
        for child in reversed(list(game)):
            if child.tag in TENDER_FIELDS:
                values[child.tag] = child.text or ""
        reads[game.findtext("path") or ""] = {name: value for name, value in values.items() if value.strip(" \t\n\r")}
    return reads


@given(scenarios())
def test_nothing_but_tenders_entries_changes_and_their_other_children_stay(scenario: Scenario):
    out = _merge(scenario).text

    assert out.startswith(HEAD)
    position = len(HEAD)
    for separator, entry in zip(scenario.separators, scenario.entries, strict=True):
        assert out.startswith(separator, position)
        position += len(separator)
        if not entry.owned:
            assert out.startswith(entry.text(), position)
            position += len(entry.text())
            continue
        end = out.index("</game>", position) + len("</game>")
        segment = out[position:end]
        assert segment.startswith(f"<game>\n\t\t<path>{escape(entry.path)}</path>")
        for tag, value in entry.children:
            if tag not in TENDER_FIELDS:
                assert f"<{tag}>{escape(value or '')}</{tag}>" in segment
        position = end
    assert out.endswith(TAIL)
    appended = out[position : len(out) - len(TAIL)]
    expected = [path for path in scenario.absent_owned if any(_as_written(v) for v in scenario.owned[path].values())]
    assert appended.count("\n\t<game>") == len(expected)


@given(scenarios())
def test_every_field_reads_as_the_rule_says(scenario: Scenario):
    merge = _merge(scenario)
    reads = _esde_reads(merge.text)

    for path, desired in scenario.owned.items():
        new = {name: written for name, value in desired.items() if (written := _as_written(value))}
        entry = next((entry for entry in scenario.entries if entry.path == path), None)
        if entry is None:
            assert reads.get(path, {}) == new
            assert merge.written[path] == new
            continue
        current = dict(entry.children)
        expected_read: dict[str, str] = {}
        expected_record: dict[str, str] = {}
        for name in TENDER_FIELDS:
            state = entry.states[name]
            if state in ("edited", "unrecorded"):
                expected_read[name] = current[name] or ""
                if state == "edited":
                    expected_record[name] = scenario.written[path][name]
            elif name in new:
                expected_read[name] = new[name]
                expected_record[name] = new[name]
        assert reads[path] == expected_read
        assert merge.written[path] == expected_record

    for path, record in scenario.written.items():
        if path not in scenario.owned:
            assert merge.written[path] == record


@given(scenarios())
def test_a_second_merge_with_the_same_input_changes_nothing(scenario: Scenario):
    first = _merge(scenario)

    second = merge_gamelist(first.text, scenario.owned, first.written)

    assert isinstance(second, GamelistMerge)
    assert second.changed is False
    assert second.text == first.text
    assert second.written == first.written


@given(scenarios())
def test_the_answer_says_whether_the_text_changed(scenario: Scenario):
    merge = _merge(scenario)

    assert merge.changed is (merge.text != scenario.text())

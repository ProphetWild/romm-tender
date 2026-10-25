"""Tender's fields in an ES-DE ``gamelist.xml``, merged into whatever the file already holds.

Contract: from the file's text, the entries Tender owns and the record of what
it last wrote there, answer the text to write and the new record — changing
only Tender's own fields in Tender's own entries, and leaving every other byte
of the file as it was. ES-DE, its scraper and the user write the same file, so
the merge is a splice over the original text and never a parse and
re-serialisation: a serialiser rewrites the whole file — its indentation,
quoting, entity spelling, and the element ES-DE keeps beside ``<gameList>``.
ElementTree only answers whether the text is XML at all. Pure: reading and
writing the file and its record are the caller's.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# The order ES-DE declares these in (``GAME_METADATA``, es-app/src/MetaData.cpp),
# which is the order it writes an entry's fields in.
TENDER_FIELDS: tuple[str, ...] = ("name", "desc", "releasedate", "developer", "publisher", "genre", "players")

# pugixml's own declaration, as ES-DE saves every gamelist (``doc.save_file``).
_ESDE_DECLARATION = '<?xml version="1.0"?>\n'

_START_TAG = re.compile(r"""<([^\s/>]+)(?:[^>"']|"[^"]*"|'[^']*')*>""")
_END_TAG = re.compile(r"</[^\s>]+\s*>")
_ENTITY = re.compile(r"&(?:#x([0-9A-Fa-f]+)|#([0-9]+)|(lt|gt|amp|quot|apos));")
_PREDEFINED = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}
_DECLARATION = re.compile(r"<\?xml[ \t\r\n]")
# Everything outside XML 1.0's ``Char`` production, which a parser refuses.
_UNWRITABLE = re.compile("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
# pugixml drops text that is only these unless ``parse_ws_pcdata`` is set, and
# ES-DE loads with the defaults, so such a field reads as empty there.
_BLANK = " \t\n\r"

type _Edit = tuple[int, int, str]


@dataclass(frozen=True)
class GamelistMerge:
    """The text to write and the record of what Tender wrote; ``changed`` is False when nothing is to be written."""

    text: str
    written: dict[str, dict[str, str]]
    changed: bool


@dataclass(frozen=True)
class UnreadableGamelist:
    """A file that cannot be read as a gamelist, so nothing is to be written into it; ``reason`` says why."""

    reason: str


@dataclass(frozen=True, slots=True)
class _Element:
    """Where an element lies in the text: its start tag ``[start, open_end)``, its end tag ``[close_start, end)``."""

    tag: str
    start: int
    open_end: int
    close_start: int
    end: int
    children: tuple[_Element, ...]

    @property
    def self_closed(self) -> bool:
        return self.open_end == self.end


def merge_gamelist(
    current: str | None,
    owned: Mapping[str, Mapping[str, str]],
    written: Mapping[str, Mapping[str, str]],
) -> GamelistMerge | UnreadableGamelist:
    """Merge *owned* into the gamelist text *current*, judged against *written*.

    *owned* maps each entry Tender owns, by its ``<path>`` (``./<name>``), to
    the values of its fields (``TENDER_FIELDS``); a field with an empty value is
    not written. *written* is the record a previous merge answered. ``None``,
    or a file holding nothing but whitespace, is no file.

    In each of Tender's entries a field that is absent or empty is written; one
    whose value is what Tender last wrote takes the new value, or goes when the
    new value is empty; any other value is the user's or ES-DE's and stays, and
    the record keeps Tender's last value for it so it is never taken for
    Tender's. An entry the file lacks is appended inside ``<gameList>`` after
    its last element. Nothing else changes: not an entry Tender no longer owns,
    not a child of Tender's entries that is not one of its fields, not a byte
    between elements. The record carries the entries Tender no longer owns as
    they were.

    Raises ``ValueError`` for a field that is not one of Tender's, a path that
    names no file in the system folder, or two paths naming the same file.
    """
    entries = _owned_entries(owned)
    if current is None or not current.lstrip("\ufeff").strip(_BLANK):
        return _new_gamelist(entries, written)
    reason = _why_unreadable(current)
    if reason is not None:
        return UnreadableGamelist(reason)
    return _merge_into(current, entries, written)


def _owned_entries(owned: Mapping[str, Mapping[str, str]]) -> list[tuple[str, str, dict[str, str]]]:
    entries: list[tuple[str, str, dict[str, str]]] = []
    paths_by_file: dict[str, str] = {}
    for path, fields in owned.items():
        unknown = sorted(set(fields) - set(TENDER_FIELDS))
        if unknown:
            raise ValueError(f"not one of Tender's gamelist fields: {unknown}")
        file = _in_system_folder(path)
        if file is None:
            raise ValueError(f"{path!r} is not a path in the system folder")
        if file in paths_by_file:
            raise ValueError(f"{paths_by_file[file]!r} and {path!r} name the same file")
        paths_by_file[file] = path
        values = {name: value for name in TENDER_FIELDS if (value := _writable(fields.get(name, "")))}
        entries.append((path, file, values))
    return entries


def _why_unreadable(text: str) -> str | None:
    body = text.removeprefix("\ufeff")
    if _DECLARATION.match(body):
        declaration_end = body.find("?>")
        if declaration_end == -1:
            return "its XML declaration is not closed"
        body = body[declaration_end + 2 :]
    # ES-DE keeps ``<alternativeEmulator>`` beside ``<gameList>`` rather than in
    # it (``doc.prepend_child``, ``GamelistFileParser::updateGamelist``), so a
    # gamelist it wrote can hold two top-level elements, which XML forbids and
    # pugixml reads anyway; the wrapper lets ElementTree read that file too. A
    # DOCTYPE cannot stand inside it, so no entity one declares is expanded —
    # ElementTree would expand it where pugixml reads it as text.
    try:
        document = ET.fromstring(f"<document>{body}</document>")
    except ET.ParseError as error:
        return f"it is not well-formed XML ({error})"
    if document.find("gameList") is None:
        return "it holds no gameList element"
    return None


def _new_gamelist(
    entries: list[tuple[str, str, dict[str, str]]], written: Mapping[str, Mapping[str, str]]
) -> GamelistMerge:
    record = _carried(written, entries)
    appended: list[str] = []
    for path, _file, values in entries:
        record[path] = dict(values)
        if values:
            appended.append(_entry_text(path, values, "\n\t", "\n\t\t", "\n"))
    if not appended:
        return GamelistMerge(text=f"{_ESDE_DECLARATION}<gameList />\n", written=record, changed=False)
    return GamelistMerge(
        text=f"{_ESDE_DECLARATION}<gameList>{''.join(appended)}\n</gameList>\n", written=record, changed=True
    )


def _merge_into(
    text: str, entries: list[tuple[str, str, dict[str, str]]], written: Mapping[str, Mapping[str, str]]
) -> GamelistMerge:
    newline = "\r\n" if "\r\n" in text else "\n"
    game_list = next(element for element in _scan(text) if element.tag == "gameList")
    entry_break = newline + "\t"
    if game_list.children:
        entry_break = _break_before(text, game_list.open_end, game_list.children[0], entry_break, newline)
    model = next((element for element in game_list.children if element.tag == "game" and element.children), None)
    child_break = entry_break + "\t"
    if model is not None:
        child_break = _break_before(text, model.open_end, model.children[0], child_break, newline)

    games = _games_by_file(text, game_list)
    record = _carried(written, entries)
    edits: list[_Edit] = []
    appended: list[str] = []
    for path, file, values in entries:
        game = games.get(file)
        if game is None:
            record[path] = dict(values)
            if values:
                appended.append(_entry_text(path, values, entry_break, child_break, newline))
            continue
        game_edits, record[path] = _merge_entry(text, game, values, written.get(path, {}), child_break, newline)
        edits += game_edits
    if appended:
        edits.append(_appending(text, game_list, "".join(appended), newline))
    merged = _apply(text, edits)
    return GamelistMerge(text=merged, written=record, changed=merged != text)


def _games_by_file(text: str, game_list: _Element) -> dict[str, _Element]:
    """Each ``<game>`` by the file its ``<path>`` names, the last one where two name the same file.

    ``parseGamelist`` replaces a game's metadata with that of each entry naming
    it in turn (es-app/src/GamelistFileParser.cpp), so the last is what ES-DE
    shows. A ``<folder>`` is never Tender's: ES-DE skips one naming a path it
    lists as a game ("Game/folder mismatch"), and every entry Tender owns is.
    """
    games: dict[str, _Element] = {}
    for element in game_list.children:
        if element.tag != "game":
            continue
        path = next((child for child in element.children if child.tag == "path"), None)
        value = None if path is None else _text_of(text, path)
        file = None if value is None else _in_system_folder(value)
        if file is not None:
            games[file] = element
    return games


def _merge_entry(
    text: str,
    game: _Element,
    values: dict[str, str],
    last: Mapping[str, str],
    default_break: str,
    newline: str,
) -> tuple[list[_Edit], dict[str, str]]:
    first_of_each: dict[str, _Element] = {}
    for child in game.children:
        if child.tag in TENDER_FIELDS:
            first_of_each.setdefault(child.tag, child)
    edits: list[_Edit] = []
    added: list[tuple[str, str]] = []
    record: dict[str, str] = {}
    for name in TENDER_FIELDS:
        new = values.get(name, "")
        previous = last.get(name)
        child = first_of_each.get(name)
        current = None if child is None else _text_of(text, child)
        if child is None or (current is not None and not current.strip(_BLANK)):
            if new:
                if child is None:
                    added.append((name, new))
                else:
                    edits.append(_filled(text, child, new, newline))
                record[name] = new
        elif current is not None and current == previous:
            if not new:
                edits.append(_removed(text, child))
                continue
            if new != current:
                edits.append((child.open_end, child.close_start, _escape(new, newline)))
            record[name] = new
        elif previous is not None:
            record[name] = previous
    if added:
        child_break = _break_before(text, game.open_end, game.children[0], default_break, newline)
        insertion = "".join(f"{child_break}<{name}>{_escape(value, newline)}</{name}>" for name, value in added)
        edits.append((game.children[-1].end, game.children[-1].end, insertion))
    return edits, record


def _filled(text: str, child: _Element, value: str, newline: str) -> _Edit:
    if child.self_closed:
        opening = text[child.start : child.end - 2].rstrip(_BLANK) + ">"
        return (child.start, child.end, f"{opening}{_escape(value, newline)}</{child.tag}>")
    return (child.open_end, child.close_start, _escape(value, newline))


def _removed(text: str, child: _Element) -> _Edit:
    """The child with its own line, where nothing else stands on that line."""
    line_break = text.rfind("\n", 0, child.start)
    if line_break == -1 or text[line_break + 1 : child.start].strip(" \t"):
        return (child.start, child.end, "")
    start = line_break - 1 if line_break > 0 and text[line_break - 1] == "\r" else line_break
    return (start, child.end, "")


def _appending(text: str, game_list: _Element, entries: str, newline: str) -> _Edit:
    if game_list.self_closed:
        opening = text[game_list.start : game_list.end - 2].rstrip(_BLANK) + ">"
        return (game_list.start, game_list.end, f"{opening}{entries}{newline}</{game_list.tag}>")
    if game_list.children:
        end = game_list.children[-1].end
        return (end, end, entries)
    closing = newline if game_list.open_end == game_list.close_start else ""
    return (game_list.open_end, game_list.open_end, entries + closing)


def _entry_text(path: str, values: dict[str, str], entry_break: str, child_break: str, newline: str) -> str:
    parts = [entry_break, "<game>", child_break, "<path>", _escape(path, newline), "</path>"]
    for name, value in values.items():
        parts += [child_break, f"<{name}>", _escape(value, newline), f"</{name}>"]
    parts += [entry_break, "</game>"]
    return "".join(parts)


def _carried(
    written: Mapping[str, Mapping[str, str]], entries: list[tuple[str, str, dict[str, str]]]
) -> dict[str, dict[str, str]]:
    owned_paths = {path for path, _file, _values in entries}
    return {path: dict(fields) for path, fields in written.items() if path not in owned_paths}


def _apply(text: str, edits: list[_Edit]) -> str:
    parts: list[str] = []
    position = 0
    for start, end, replacement in sorted(edits, key=lambda edit: (edit[0], edit[1])):
        parts += [text[position:start], replacement]
        position = end
    parts.append(text[position:])
    return "".join(parts)


def _break_before(text: str, after: int, element: _Element, default: str, newline: str) -> str:
    """The break that sets *element* off from what precedes it since *after*, read from the text.

    A newline and the indentation of *element*'s line, or the whitespace before
    it on a line it shares; *default* where something else stands there.
    """
    line_break = text.rfind("\n", after, element.start)
    if line_break == -1:
        inline = text[after : element.start]
        return default if inline.strip(" \t") else inline
    indentation = text[line_break + 1 : element.start]
    return default if indentation.strip(" \t") else newline + indentation


def _in_system_folder(path: str) -> str | None:
    """The file *path* names inside the system folder, or ``None`` where it names none there.

    ES-DE's own reading of a gamelist path (es-core/src/utils/FileSystemUtil.cpp):
    ``getGenericPath`` turns ``\\`` into ``/``, folds repeated slashes and
    drops a trailing one; ``resolveRelativePath`` resolves only a path starting
    ``./``, so anything else, surrounding whitespace included, is not the
    system folder's file; ``getCanonicalPath`` drops ``.`` and resolves ``..``.
    Its following of symlinks is the one part a pure reading cannot do.
    """
    generic = re.sub("/{2,}", "/", path.replace("\\", "/")).rstrip("/")
    if not generic.startswith("./"):
        return None
    parts: list[str] = []
    for part in generic[2:].split("/"):
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return "/".join(parts) or None


def _text_of(text: str, element: _Element) -> str | None:
    """The element's text as ES-DE reads it, or ``None`` where markup inside it makes it no value Tender judges."""
    content = text[element.open_end : element.close_start]
    if "<" in content:
        return None
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return _ENTITY.sub(_entity_value, normalized)


def _entity_value(match: re.Match[str]) -> str:
    hexadecimal, decimal, name = match.groups()
    if hexadecimal:
        return chr(int(hexadecimal, 16))
    if decimal:
        return chr(int(decimal))
    return _PREDEFINED[name]


def _writable(value: str) -> str:
    """*value* as a parser reads it back once written, or ``""`` where nothing of it would be read.

    A parser reads every CR LF and lone CR as LF (XML 1.0, "End-of-Line
    Handling"), so the record holds the value with LF, as the next merge reads it.
    """
    cleaned = _UNWRITABLE.sub("", value).replace("\r\n", "\n").replace("\r", "\n")
    return cleaned if cleaned.strip(_BLANK) else ""


def _escape(value: str, newline: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", newline)


def _scan(text: str) -> tuple[_Element, ...]:
    """The top-level elements of well-formed *text*, each with its children, by their offsets.

    Raises ``ValueError`` where a tag cannot be read, which text ElementTree
    accepted never holds.
    """
    top: list[_Element] = []
    open_elements: list[tuple[str, int, int, list[_Element]]] = []
    position = text.find("<")
    while position != -1:
        if text.startswith("<!--", position):
            position = text.index("-->", position) + 3
        elif text.startswith("<![CDATA[", position):
            position = text.index("]]>", position) + 3
        elif text.startswith("<?", position):
            position = text.index("?>", position) + 2
        elif text.startswith("</", position):
            end = _tag_at(_END_TAG, text, position).end()
            tag, start, open_end, children = open_elements.pop()
            siblings = open_elements[-1][3] if open_elements else top
            siblings.append(_Element(tag, start, open_end, position, end, tuple(children)))
            position = end
        else:
            start_tag = _tag_at(_START_TAG, text, position)
            end = start_tag.end()
            if text[end - 2] == "/":
                siblings = open_elements[-1][3] if open_elements else top
                siblings.append(_Element(start_tag.group(1), position, end, end, end, ()))
            else:
                open_elements.append((start_tag.group(1), position, end, []))
            position = end
        position = text.find("<", position)
    return tuple(top)


def _tag_at(pattern: re.Pattern[str], text: str, position: int) -> re.Match[str]:
    match = pattern.match(text, position)
    if match is None:
        raise ValueError(f"no tag can be read at offset {position}")
    return match

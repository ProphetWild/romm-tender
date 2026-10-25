"""A stand-in's predicted shape held against what the download's own unpack path really leaves on disk.

Every case builds the files RomM would serve under ``tmp_path``, hands them to
the download's :class:`~services.downloads.unpack.DownloadUnpacker` exactly as
:class:`~services.downloads.DownloadService` does — the real
:class:`~adapters.download_file.DownloadFileAdapter`, the names the service
derives, the same ``finish_single`` / ``finish_multi`` call — and asserts that
:func:`~domain.esde_stand_in.predict_stand_in`, asked about the same RomM
payload, answers the entry that landed. A stand-in placed at the predicted path
is then exactly where the download lands.
"""

from __future__ import annotations

import io
import logging
import os
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_retrodeck_folders import FakeRetroDeckFolders

from adapters.download_file import DownloadFileAdapter
from domain.esde_stand_in import FILE_SHAPE, FOLDER_SHAPE, StandInShape, predict_stand_in
from domain.rom_files import TMP_EXT, ZIP_TMP_EXT, is_multi_file_download, safe_extract_dir_name, safe_local_file_name
from domain.served_shape import is_zip_name
from services.downloads.unpack import DownloadUnpacker, DownloadUnpackerConfig

_SYSTEM = "psx"


@dataclass
class _Landed:
    file_path: str
    rom_dir: str | None


class _Recorder:
    """The install recorder's seam: what the unpack recorded, the way the real one is handed it."""

    def __init__(self) -> None:
        self.landed: _Landed | None = None

    def do_record_install(self, *, rom_id, rom_detail, file_path, rom_dir, system, cleanup):
        self.landed = _Landed(file_path=file_path, rom_dir=rom_dir)
        return (file_path, None)

    def do_resolve_launch_bake(self, rom_id: int, rom_detail: dict[str, Any], file_path: str) -> tuple[int | None, str]:
        raise AssertionError("an unpack bakes no launch command")

    def do_record_applied_launch_options(self, rom_id: int, launch_options: str) -> None:
        raise AssertionError("an unpack records no launch command")


def _rom_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "roms" / _SYSTEM
    folder.mkdir(parents=True)
    return folder


def _download(tmp_path: Path, rom: dict[str, Any], payload: bytes, *, m3u_supported: bool) -> _Landed:
    """Run *payload* — what RomM serves for *rom* — through the download's unpack path, as the service hands it over."""
    folder = _rom_folder(tmp_path)
    recorder = _Recorder()
    unpacker = DownloadUnpacker(
        config=DownloadUnpackerConfig(
            download_file_store=DownloadFileAdapter(),
            retrodeck_folders=FakeRetroDeckFolders(roms=str(tmp_path / "roms")),
            install_recorder=recorder,
            m3u_support=lambda system_name: m3u_supported,
            logger=logging.getLogger("conformance"),
        )
    )
    target_path = str(folder / safe_local_file_name(rom).name)
    # ``DownloadService._extract_dir_name``: a folder only for what may be unpacked.
    may_unpack = is_multi_file_download(rom) or is_zip_name(target_path)
    extract_dir = str(folder / safe_extract_dir_name(rom).name) if may_unpack else None
    if is_multi_file_download(rom):
        Path(target_path + ZIP_TMP_EXT).write_bytes(payload)
        unpacker.finish_multi(
            9, rom, target_path, str(extract_dir), _SYSTEM, progress_callback=lambda *_: None, reserved_bytes=0
        )
    else:
        Path(target_path + TMP_EXT).write_bytes(payload)
        unpacker.finish_single(
            9, rom, target_path, extract_dir, _SYSTEM, progress_callback=lambda *_: None, reserved_bytes=0
        )
    assert recorder.landed is not None
    entry = recorder.landed.rom_dir or recorder.landed.file_path
    assert os.listdir(folder) == [os.path.basename(entry)], "the download left more than its one entry"
    return recorder.landed


def _listed_as(landed: _Landed) -> StandInShape | None:
    """The entry ES-DE lists for what landed: a file, a folder holding its launch file under its own name, or none."""
    if landed.rom_dir is None:
        return StandInShape(name=os.path.basename(landed.file_path), kind=FILE_SHAPE)
    name = os.path.basename(landed.rom_dir)
    if os.path.dirname(landed.file_path) == landed.rom_dir and os.path.basename(landed.file_path) == name:
        return StandInShape(name=name, kind=FOLDER_SHAPE, launch_name=name)
    return None


def _zip_of(members: dict[str, bytes], *, encode: bool = False) -> bytes:
    """A zip holding *members*; *encode* writes their names URL-encoded, as RomM writes a multi-file ROM's."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in members.items():
            archive.writestr(urllib.parse.quote(name) if encode else name, data)
    return out.getvalue()


def _multi_rom(members: dict[str, bytes], **extra: Any) -> dict[str, Any]:
    """RomM's detail for a ROM folder ``Game`` holding *members*, each named by its path inside it."""
    files = []
    for rel, data in members.items():
        directory, name = os.path.split(rel)
        files.append(
            {
                "file_name": name,
                "file_path": "/".join(p for p in ("romm/library/psx/Game", directory) if p),
                "file_size_bytes": len(data),
            }
        )
    return {
        "id": 9,
        "fs_name": "Game",
        "fs_name_no_ext": "Game",
        "full_path": "romm/library/psx/Game",
        "has_multiple_files": True,
        "files": files,
        **extra,
    }


def _zip_rom(members: dict[str, bytes] | None, payload: bytes, **extra: Any) -> dict[str, Any]:
    """RomM's detail for a single ``Game.zip``; *members* is what RomM lists inside it, ``None`` for nothing."""
    entry: dict[str, Any] = {"file_name": "Game.zip", "file_size_bytes": len(payload)}
    if members is not None:
        entry["archive_members"] = [{"name": name, "size": len(data)} for name, data in members.items()]
    return {
        "id": 9,
        "fs_name": "Game.zip",
        "fs_name_no_ext": "Game",
        "fs_size_bytes": len(payload),
        "has_simple_single_file": True,
        "files": [entry],
        **extra,
    }


_CUE_SET = {"Game.cue": b'FILE "Game.bin" BINARY\n', "Game.bin": b"\x00" * 4000}
_TWO_DISCS = {
    "Game (Disc 1).cue": b"FILE x BINARY\n",
    "Game (Disc 1).bin": b"\x00" * 900,
    "Game (Disc 2).cue": b"FILE y BINARY\n",
    "Game (Disc 2).bin": b"\x00" * 900,
}


class TestEveryShapeLandsWhereItsStandInStands:
    def test_a_plain_single_file(self, tmp_path):
        rom = {"id": 9, "fs_name": "Game (USA).gba", "has_simple_single_file": True}

        landed = _download(tmp_path, rom, b"\x00" * 64, m3u_supported=True)

        assert predict_stand_in(rom, m3u_supported=True) == _listed_as(landed)
        assert _listed_as(landed) == StandInShape(name="Game (USA).gba", kind=FILE_SHAPE)

    def test_a_nested_single_file(self, tmp_path):
        rom = {
            "id": 9,
            "fs_name": "Game",
            "has_nested_single_file": True,
            "files": [{"file_name": "Game.chd", "file_size_bytes": 64}],
        }

        landed = _download(tmp_path, rom, b"\x00" * 64, m3u_supported=True)

        assert predict_stand_in(rom, m3u_supported=True) == _listed_as(landed)
        assert _listed_as(landed) == StandInShape(name="Game.chd", kind=FILE_SHAPE)

    def test_a_name_the_download_has_to_coerce(self, tmp_path):
        rom = {"id": 9, "fs_name": "../../Game.gba", "has_simple_single_file": True}

        landed = _download(tmp_path, rom, b"\x00" * 64, m3u_supported=True)

        assert predict_stand_in(rom, m3u_supported=True) == _listed_as(landed)

    def test_a_single_zip_whose_members_hold_no_disc_image(self, tmp_path):
        members = {"Game.gba": b"\x00" * 64}
        payload = _zip_of(members)
        rom = _zip_rom(members, payload)

        landed = _download(tmp_path, rom, payload, m3u_supported=True)

        assert predict_stand_in(rom, m3u_supported=True) == _listed_as(landed)
        assert _listed_as(landed) == StandInShape(name="Game.zip", kind=FILE_SHAPE)

    def test_a_single_zip_with_no_listing_and_no_disc_image(self, tmp_path):
        payload = _zip_of({"Game.gba": b"\x00" * 64})
        rom = _zip_rom(None, payload)

        landed = _download(tmp_path, rom, payload, m3u_supported=True)

        assert predict_stand_in(rom, m3u_supported=True) == _listed_as(landed)

    @pytest.mark.parametrize("m3u_supported", [True, False])
    def test_a_single_zip_holding_a_cue_sheet(self, tmp_path, m3u_supported):
        payload = _zip_of(_CUE_SET)
        rom = _zip_rom(_CUE_SET, payload)

        landed = _download(tmp_path, rom, payload, m3u_supported=m3u_supported)

        assert predict_stand_in(rom, m3u_supported=m3u_supported) == _listed_as(landed)
        expected = "Game.m3u" if m3u_supported else "Game.cue"
        assert _listed_as(landed) == StandInShape(name=expected, kind=FOLDER_SHAPE, launch_name=expected)

    def test_a_single_zip_wrapping_its_disc_in_a_folder(self, tmp_path):
        members = {f"Game/{name}": data for name, data in _CUE_SET.items()}
        members["__MACOSX/Game/._Game.cue"] = b"\x00" * 4
        payload = _zip_of(members)
        rom = _zip_rom(members, payload)

        landed = _download(tmp_path, rom, payload, m3u_supported=True)

        assert predict_stand_in(rom, m3u_supported=True) == _listed_as(landed)
        assert _listed_as(landed) == StandInShape(name="Game.m3u", kind=FOLDER_SHAPE, launch_name="Game.m3u")

    def test_a_multi_disc_rom(self, tmp_path):
        rom = _multi_rom(_TWO_DISCS, fs_name_no_ext="Final Fantasy VII")

        landed = _download(tmp_path, rom, _zip_of(_TWO_DISCS, encode=True), m3u_supported=True)

        assert predict_stand_in(rom, m3u_supported=True) == _listed_as(landed)
        assert _listed_as(landed) == StandInShape(
            name="Final Fantasy VII.m3u", kind=FOLDER_SHAPE, launch_name="Final Fantasy VII.m3u"
        )

    def test_a_multi_file_rom_with_its_own_playlist(self, tmp_path):
        members = {"Zelda (USA).m3u": b"Zelda (USA).nsp\n", "Zelda (USA).nsp": b"\x00" * 500}
        rom = _multi_rom(members)

        landed = _download(tmp_path, rom, _zip_of(members, encode=True), m3u_supported=True)

        assert predict_stand_in(rom, m3u_supported=True) == _listed_as(landed)
        assert _listed_as(landed) == StandInShape(
            name="Zelda (USA).m3u", kind=FOLDER_SHAPE, launch_name="Zelda (USA).m3u"
        )

    def test_a_base_file_beside_its_updates(self, tmp_path):
        members = {
            "Zelda.nsp": b"\x00" * 900,
            "update/Zelda_update.nsp": b"\x00" * 100,
            "dlc/Zelda_dlc.nsp": b"\x00" * 100,
        }
        rom = _multi_rom(members)

        landed = _download(tmp_path, rom, _zip_of(members, encode=True), m3u_supported=False)

        assert predict_stand_in(rom, m3u_supported=False) == _listed_as(landed)
        assert _listed_as(landed) == StandInShape(name="Zelda.nsp", kind=FOLDER_SHAPE, launch_name="Zelda.nsp")

    def test_a_launch_file_in_a_subfolder_lands_as_no_one_game(self, tmp_path):
        members = {"disc/Game.cue": _CUE_SET["Game.cue"], "disc/Game.bin": _CUE_SET["Game.bin"]}
        rom = _multi_rom(members)

        landed = _download(tmp_path, rom, _zip_of(members, encode=True), m3u_supported=False)

        assert _listed_as(landed) is None
        assert predict_stand_in(rom, m3u_supported=False) is None

    def test_a_folder_boot_layout_lands_as_no_one_game(self, tmp_path):
        members = {
            "PS3_DISC.SFB": b"\x00" * 8,
            "PS3_GAME/PARAM.SFO": b"\x00" * 16,
            "PS3_GAME/USRDIR/EBOOT.BIN": b"\x00" * 64,
        }
        rom = _multi_rom(members)

        landed = _download(tmp_path, rom, _zip_of(members, encode=True), m3u_supported=True)

        assert _listed_as(landed) is None
        assert predict_stand_in(rom, m3u_supported=True) is None


class TestTheOnePredictionThatMayMiss:
    """A single zip RomM lists no members for (docs/specs/esde-library.md, D5)."""

    def test_holding_a_disc_it_lands_as_the_folder_the_listing_would_have_named(self, tmp_path):
        payload = _zip_of(_CUE_SET)
        unlisted = _zip_rom(None, payload)
        listed = _zip_rom(_CUE_SET, payload)

        landed = _download(tmp_path, unlisted, payload, m3u_supported=True)

        assert predict_stand_in(unlisted, m3u_supported=True) == StandInShape(name="Game.zip", kind=FILE_SHAPE)
        assert _listed_as(landed) != predict_stand_in(unlisted, m3u_supported=True)
        assert _listed_as(landed) == predict_stand_in(listed, m3u_supported=True)

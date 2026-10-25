"""Tests for ``domain/esde_media.py`` — which RomM file fills which ES-DE media folder, and how it is named."""

from __future__ import annotations

from typing import Any

import pytest

from domain.esde_media import (
    IMAGE_LOOKUP_ORDER,
    MANUAL_LOOKUP_ORDER,
    MEDIA_FOLDERS,
    ON_SELECTION_FOLDERS,
    SYNC_FOLDERS,
    VIDEO_LOOKUP_ORDER,
    CachedMedia,
    accepted_extensions,
    evictions,
    extension_for,
    lookup_order,
    media_fingerprint,
    media_link_path,
    media_sources,
    media_stem,
    sniff_extension,
)

RES = "/assets/romm/resources"
COVER = f"{RES}/roms/7/42/cover/big.png?ts=2026-10-01 12:00:00.123456+00:00"
OWN_SHOTS = f"{RES}/roms/7/42/screenshots"


def _rom(**overrides: Any) -> dict[str, Any]:
    rom: dict[str, Any] = {"id": 42, "platform_id": 7, "updated_at": "2026-10-01T12:00:00Z"}
    rom.update(overrides)
    return rom


class TestTheFolders:
    def test_tender_fills_eleven_folders_and_never_custom(self):
        assert MEDIA_FOLDERS == (
            "covers",
            "marquees",
            "screenshots",
            "titlescreens",
            "3dboxes",
            "backcovers",
            "physicalmedia",
            "fanart",
            "miximages",
            "videos",
            "manuals",
        )
        assert "custom" not in MEDIA_FOLDERS

    def test_the_images_are_fetched_at_sync_and_the_videos_and_manuals_on_selection(self):
        assert MEDIA_FOLDERS[:9] == SYNC_FOLDERS
        assert ON_SELECTION_FOLDERS == ("videos", "manuals")


class TestTheCover:
    def test_the_large_cover_is_taken_with_its_query(self):
        assert media_sources(_rom(path_cover_large=COVER, path_cover_small=f"{RES}/small.png"))["covers"] == COVER

    def test_the_small_cover_stands_in_for_a_missing_large_one(self):
        assert (
            media_sources(_rom(path_cover_large="", path_cover_small=f"{RES}/small.png"))["covers"]
            == f"{RES}/small.png"
        )

    def test_no_cover_answers_none(self):
        assert "covers" not in media_sources(_rom(path_cover_large=None, path_cover_small=""))


class TestTheMarquee:
    def test_it_is_the_logo(self):
        rom = _rom(ss_metadata={"logo_path": "roms/7/42/logo/logo.png", "marquee_path": "roms/7/42/marquee/m.png"})

        assert media_sources(rom)["marquees"] == f"{RES}/roms/7/42/logo/logo.png"

    def test_screenscrapers_screen_marquee_is_never_es_de_s_marquee(self):
        assert "marquees" not in media_sources(_rom(ss_metadata={"marquee_path": "roms/7/42/marquee/m.png"}))

    def test_a_gamelists_marquee_stands_in_for_a_missing_logo(self):
        rom = _rom(ss_metadata={"logo_path": ""}, gamelist_metadata={"marquee_path": "roms/7/42/marquee/wheel.png"})

        assert media_sources(rom)["marquees"] == f"{RES}/roms/7/42/marquee/wheel.png"

    def test_a_logo_in_any_block_comes_before_the_gamelists_marquee(self):
        rom = _rom(gamelist_metadata={"logo_path": "roms/7/42/logo/gl.png", "marquee_path": "roms/7/42/marquee/w.png"})

        assert media_sources(rom)["marquees"] == f"{RES}/roms/7/42/logo/gl.png"


class TestTheScreenshot:
    def test_the_first_from_the_roms_own_screenshots_folder_is_taken(self):
        rom = _rom(
            merged_screenshots=[
                f"{RES}/roms/7/42/title_screen/title.png",
                f"{OWN_SHOTS}/0.jpg",
                f"{OWN_SHOTS}/1.jpg",
            ]
        )

        assert media_sources(rom)["screenshots"] == f"{OWN_SHOTS}/0.jpg"

    def test_another_roms_screenshots_folder_is_not_its_own(self):
        rom = _rom(merged_screenshots=[f"{RES}/roms/7/420/screenshots/0.jpg", f"{OWN_SHOTS}/3.jpg"])

        assert media_sources(rom)["screenshots"] == f"{OWN_SHOTS}/3.jpg"

    def test_the_first_is_taken_when_none_lies_in_its_own_folder(self):
        rom = _rom(merged_screenshots=[f"{RES}/roms/7/42/libretro/title.png", f"{RES}/roms/7/42/libretro/logo.png"])

        assert media_sources(rom)["screenshots"] == f"{RES}/roms/7/42/libretro/title.png"

    def test_the_first_is_taken_when_the_rom_names_no_folder_of_its_own(self):
        rom = _rom(id=None, merged_screenshots=[f"{RES}/a.png", f"{OWN_SHOTS}/0.jpg"])

        assert media_sources(rom)["screenshots"] == f"{RES}/a.png"

    @pytest.mark.parametrize("shots", [None, [], "roms/7/42/screenshots/0.jpg", [None, ""], {"0": "a.png"}])
    def test_no_usable_screenshot_answers_none(self, shots):
        assert "screenshots" not in media_sources(_rom(merged_screenshots=shots))


class TestTheMetadataBlocks:
    @pytest.mark.parametrize(
        ("folder", "key"),
        [
            ("titlescreens", "title_screen_path"),
            ("3dboxes", "box3d_path"),
            ("backcovers", "box2d_back_path"),
            ("physicalmedia", "physical_path"),
            ("fanart", "fanart_path"),
            ("miximages", "miximage_path"),
        ],
    )
    def test_each_folder_reads_its_path_from_screenscraper_then_the_gamelist_then_launchbox(self, folder, key):
        def sources(**blocks: dict[str, Any] | None) -> str | None:
            return media_sources(_rom(**blocks)).get(folder)

        ss = {key: "roms/7/42/ss.png"}
        gl = {key: "roms/7/42/gl.png"}
        lb = {key: "roms/7/42/lb.png"}
        assert sources(ss_metadata=ss, gamelist_metadata=gl, launchbox_metadata=lb) == f"{RES}/roms/7/42/ss.png"
        assert sources(ss_metadata={key: ""}, gamelist_metadata=gl, launchbox_metadata=lb) == f"{RES}/roms/7/42/gl.png"
        assert sources(ss_metadata=None, gamelist_metadata={}, launchbox_metadata=lb) == f"{RES}/roms/7/42/lb.png"
        assert sources() is None

    def test_a_value_that_is_not_text_is_passed_over(self):
        rom = _rom(ss_metadata={"box3d_path": 7}, gamelist_metadata={"box3d_path": "roms/7/42/box3d.png"})

        assert media_sources(rom)["3dboxes"] == f"{RES}/roms/7/42/box3d.png"

    @pytest.mark.parametrize("block", ["roms/7/42/box3d.png", ["box3d_path"], 7, True])
    def test_a_block_that_is_not_a_mapping_answers_nothing(self, block):
        assert "3dboxes" not in media_sources(_rom(ss_metadata=block))

    def test_the_url_beside_a_path_is_never_answered(self):
        rom = _rom(ss_metadata={"box3d_url": "https://screenscraper.fr/box3d.png"})

        assert "3dboxes" not in media_sources(rom)


class TestTheVideoAndTheManual:
    def test_they_are_the_roms_own_paths(self):
        rom = _rom(path_video="roms/7/42/video/video.mp4", path_manual="roms/7/42/manual/42.pdf")

        assert media_sources(rom)["videos"] == f"{RES}/roms/7/42/video/video.mp4"
        assert media_sources(rom)["manuals"] == f"{RES}/roms/7/42/manual/42.pdf"

    def test_a_rom_without_either_answers_neither(self):
        sources = media_sources(_rom(path_video=None, path_manual=""))

        assert "videos" not in sources
        assert "manuals" not in sources


class TestWhichPathsAreAnswered:
    def test_a_relative_path_takes_rom_ms_resources_prefix(self):
        assert media_sources(_rom(path_manual="roms/7/42/manual/42.pdf"))["manuals"] == f"{RES}/roms/7/42/manual/42.pdf"

    def test_an_absolute_path_is_kept(self):
        assert (
            media_sources(_rom(path_manual=f"{RES}/roms/7/42/manual/42.pdf"))["manuals"]
            == f"{RES}/roms/7/42/manual/42.pdf"
        )

    @pytest.mark.parametrize(
        "value",
        [
            "https://screenscraper.fr/media.png",
            "http://romm.local/assets/romm/resources/a.png",
            "file:///romm/resources/a.png",
            "launchbox-file:///Images/a.png",
            "data:image/png;base64,AAAA",
            "roms/7/42/../../../config.yml",
            f"{RES}/roms/7/42/..",
            "   ",
        ],
    )
    def test_no_url_and_no_path_climbing_out_is_ever_answered(self, value):
        rom = _rom(path_manual=value, path_cover_large=value, ss_metadata={"box3d_path": value})

        assert media_sources(rom) == {}

    def test_the_folders_are_answered_in_es_de_s_folder_order(self):
        rom = _rom(
            path_manual="m.pdf",
            path_video="v.mp4",
            path_cover_large=COVER,
            ss_metadata={"fanart_path": "f.png", "box3d_path": "b.png", "logo_path": "l.png"},
        )

        assert list(media_sources(rom)) == ["covers", "marquees", "3dboxes", "fanart", "videos", "manuals"]


class TestSniffing:
    @pytest.mark.parametrize(
        ("head", "extension"),
        [
            (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", ".png"),
            (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00", ".jpg"),
            (b"\xff\xd8\xff\xe1\x00\x10Exif\x00", ".jpg"),
            (b"RIFF\x24\x00\x00\x00WEBPVP8 ", ".webp"),
            (b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2", ".mp4"),
            (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom", ".mp4"),
            (b"\x00\x00\x00\x1cftypM4V \x00\x00\x00\x01M4V M4A ", ".mp4"),
            (b"\x00\x00\x00\x18ftypavc1\x00\x00\x00\x00avc1isom", ".mp4"),
            (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3", ".pdf"),
        ],
    )
    def test_the_bytes_say_what_the_file_is(self, head, extension):
        assert sniff_extension(head) == extension

    @pytest.mark.parametrize(
        "head",
        [
            b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic",
            b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1",
            b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  ",
            b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A mp42",
            b"GIF89a\x01\x00\x01\x00",
            b"RIFF\x24\x00\x00\x00AVI LIST",
            b"<html><body>Not found</body></html>",
            b"BM\x00\x00",
            b"",
            b"\x89PN",
        ],
    )
    def test_bytes_es_de_does_not_read_or_tender_does_not_write_answer_none(self, head):
        assert sniff_extension(head) is None


def _ebml(doctype: bytes, *, before: bytes = b"") -> bytes:
    """An EBML header as mkvmerge and FFmpeg write it: EBMLVersion, ReadVersion, MaxIDLength, MaxSizeLength, DocType."""
    body = b"\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04\x42\xf3\x81\x08" + before
    body += b"\x42\x82" + bytes([0x80 | len(doctype)]) + doctype + b"\x42\x87\x81\x04\x42\x85\x81\x02"
    return b"\x1a\x45\xdf\xa3" + bytes([0x80 | len(body)]) + body + b"\x18\x53\x80\x67"


class TestSniffingMatroska:
    def test_a_webm_doctype_is_webm(self):
        assert sniff_extension(_ebml(b"webm")) == ".webm"

    def test_a_matroska_doctype_is_mkv(self):
        assert sniff_extension(_ebml(b"matroska")) == ".mkv"

    def test_a_doctype_after_an_element_of_a_wider_size_is_found(self):
        void = b"\xec\x40\x03\x00\x00\x00"
        assert sniff_extension(_ebml(b"webm", before=void)) == ".webm"

    def test_a_doctype_value_spelling_webm_inside_another_element_is_not_read_as_the_doctype(self):
        decoy = b"\x42\x86\x87\x42\x82\x84webm"
        assert sniff_extension(_ebml(b"matroska", before=decoy)) == ".mkv"

    @pytest.mark.parametrize(
        "head",
        [
            b"\x1a\x45\xdf\xa3",
            b"\x1a\x45\xdf\xa3\x00",
            b"\x1a\x45\xdf\xa3\x40",
            b"\x1a\x45\xdf\xa3\x82\x00\x00",
            b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81",
            b"\x1a\x45\xdf\xa3\x9f\x42\x86\x00",
            b"\x1a\x45\xdf\xa3\x8a\x42\x82\x88webm",
        ],
    )
    def test_a_header_that_ends_before_its_doctype_is_mkv(self, head):
        assert sniff_extension(head) == ".mkv"


class TestWhatEachFolderTakes:
    def test_es_de_s_lookup_orders(self):
        """``FileData.h``'s ``sImageExtensions`` and ``sVideoExtensions``; ``FileData::getManualPath``'s ``.pdf``."""
        assert IMAGE_LOOKUP_ORDER == (".png", ".jpg", ".webp")
        assert VIDEO_LOOKUP_ORDER == (".mp4", ".mkv", ".avi", ".wmv", ".mov", ".webm", ".m4v")
        assert MANUAL_LOOKUP_ORDER == (".pdf",)

    @pytest.mark.parametrize("folder", SYNC_FOLDERS)
    def test_an_image_folder(self, folder):
        assert lookup_order(folder) == IMAGE_LOOKUP_ORDER
        assert accepted_extensions(folder) == (".png", ".jpg", ".webp")

    def test_the_video_folder(self):
        assert lookup_order("videos") == VIDEO_LOOKUP_ORDER
        assert accepted_extensions("videos") == (".mp4", ".mkv", ".webm")

    def test_the_manual_folder(self):
        assert lookup_order("manuals") == MANUAL_LOOKUP_ORDER
        assert accepted_extensions("manuals") == (".pdf",)

    @pytest.mark.parametrize("folder", ["custom", "Covers", ""])
    def test_a_folder_tender_does_not_fill_is_a_broken_invariant(self, folder):
        with pytest.raises(ValueError, match="media folder"):
            lookup_order(folder)  # pyright: ignore[reportArgumentType]
        with pytest.raises(ValueError, match="media folder"):
            accepted_extensions(folder)  # pyright: ignore[reportArgumentType]

    def test_the_extension_for_bytes_is_answered_only_where_the_folder_takes_it(self):
        png = b"\x89PNG\r\n\x1a\n"
        pdf = b"%PDF-1.4"

        assert extension_for("covers", png) == ".png"
        assert extension_for("videos", png) is None
        assert extension_for("manuals", pdf) == ".pdf"
        assert extension_for("screenshots", pdf) is None
        assert extension_for("covers", b"GIF89a") is None


class TestNaming:
    @pytest.mark.parametrize(
        ("name", "is_directory", "stem"),
        [
            ("Chrono Trigger (USA).zip", False, "Chrono Trigger (USA)"),
            ("Final Fantasy VII.m3u", True, "Final Fantasy VII.m3u"),
            ("Super Mario Bros. 3 (USA) (Rev 1).nes", False, "Super Mario Bros. 3 (USA) (Rev 1)"),
            ("README", False, "README"),
            ("game.tar.gz", False, "game.tar"),
            (".hidden", False, ""),
        ],
    )
    def test_the_stem_is_es_de_s(self, name, is_directory, stem):
        """``Utils::FileSystem::getStem``: the name less its last extension, a directory's whole name."""
        assert media_stem(name, is_directory=is_directory) == stem

    @pytest.mark.parametrize("name", ["", "sub/Chrono.zip"])
    def test_a_name_that_is_not_an_entry_name_is_a_broken_invariant(self, name):
        with pytest.raises(ValueError, match="entry name"):
            media_stem(name, is_directory=False)

    def test_the_link_path(self):
        path = media_link_path(
            "/home/deck/retrodeck/ES-DE/downloaded_media", "psx", "covers", "Final Fantasy VII.m3u", ".png"
        )

        assert path == "/home/deck/retrodeck/ES-DE/downloaded_media/psx/covers/Final Fantasy VII.m3u.png"

    def test_a_shadowing_extension_es_de_reads_has_a_path_too(self):
        assert media_link_path("/m", "snes", "videos", "Chrono", ".avi") == "/m/snes/videos/Chrono.avi"

    @pytest.mark.parametrize(
        ("system", "stem", "extension"),
        [
            ("", "Chrono", ".png"),
            ("..", "Chrono", ".png"),
            ("snes/../..", "Chrono", ".png"),
            ("snes", "", ".png"),
            ("snes", ".", ".png"),
            ("snes", "a/b", ".png"),
            ("snes", "Chrono\x00", ".png"),
            ("snes", "Chrono", ".pdf"),
            ("snes", "Chrono", "png"),
        ],
    )
    def test_a_part_that_would_leave_the_folder_or_that_es_de_does_not_read_is_a_broken_invariant(
        self, system, stem, extension
    ):
        with pytest.raises(ValueError):
            media_link_path("/m", system, "covers", stem, extension)


class TestTheFingerprint:
    def test_the_same_path_and_time_give_the_same_fingerprint(self):
        assert media_fingerprint(COVER, "2026-10-01T12:00:00Z") == media_fingerprint(COVER, "2026-10-01T12:00:00Z")

    def test_a_new_path_or_a_new_time_gives_a_new_one(self):
        fingerprint = media_fingerprint(COVER, "2026-10-01T12:00:00Z")

        assert media_fingerprint(COVER + "x", "2026-10-01T12:00:00Z") != fingerprint
        assert media_fingerprint(COVER, "2026-10-02T12:00:00Z") != fingerprint
        assert media_fingerprint(COVER, None) != fingerprint

    def test_no_two_pairs_run_together(self):
        assert media_fingerprint("a", "b c") != media_fingerprint("a b", "c")
        assert media_fingerprint("a", None) != media_fingerprint("a", "null")


class TestEviction:
    def test_nothing_goes_while_everything_fits(self):
        entries = [CachedMedia("a", 3, 1.0), CachedMedia("b", 4, 2.0)]

        assert evictions(entries, 7) == []

    def test_the_least_recently_selected_go_first_until_the_rest_fits(self):
        entries = [CachedMedia("new", 4, 30.0), CachedMedia("old", 4, 10.0), CachedMedia("mid", 4, 20.0)]

        assert evictions(entries, 5) == ["old", "mid"]

    def test_an_entry_larger_than_the_cap_goes_however_recent_it_is(self):
        entries = [CachedMedia("old", 2, 10.0), CachedMedia("huge", 9, 30.0), CachedMedia("mid", 2, 20.0)]

        assert evictions(entries, 8) == ["huge"]

    def test_a_tie_is_broken_by_key(self):
        entries = [CachedMedia("b", 4, 1.0), CachedMedia("a", 4, 1.0)]

        assert evictions(entries, 4) == ["a"]

    def test_a_cap_of_nothing_evicts_everything_but_empty_files(self):
        entries = [CachedMedia("a", 1, 1.0), CachedMedia("empty", 0, 2.0)]

        assert evictions(entries, 0) == ["a"]

    def test_no_entries_evict_nothing(self):
        assert evictions([], 10) == []

    def test_a_negative_size_or_cap_is_a_broken_invariant(self):
        with pytest.raises(ValueError):
            evictions([CachedMedia("a", -1, 1.0)], 10)
        with pytest.raises(ValueError):
            evictions([], -1)

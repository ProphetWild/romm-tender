"""Tests for ``domain/esde_scan_media.py`` — which ES-DE media folders RomM's server is not set to collect."""

from __future__ import annotations

import pytest

from domain.esde_media import MEDIA_FOLDERS
from domain.esde_scan_media import SCAN_MEDIA_FOR_FOLDER, folders_not_collected

EVERY_VALUE = (
    "logo",
    "screenshot",
    "title_screen",
    "box3d",
    "box2d_back",
    "physical",
    "fanart",
    "miximage",
    "video",
    "manual",
)


class TestTheTable:
    def test_every_folder_tender_fills_but_covers_names_what_romm_must_collect(self):
        assert set(SCAN_MEDIA_FOR_FOLDER) == set(MEDIA_FOLDERS) - {"covers"}

    @pytest.mark.parametrize(
        ("folder", "values"),
        [
            ("marquees", ("logo",)),
            ("screenshots", ("screenshot",)),
            ("titlescreens", ("title_screen",)),
            ("3dboxes", ("box3d",)),
            ("backcovers", ("box2d_back",)),
            ("physicalmedia", ("physical",)),
            ("fanart", ("fanart",)),
            ("miximages", ("miximage",)),
            ("videos", ("video", "video_normalized")),
            ("manuals", ("manual",)),
        ],
    )
    def test_each_folder_s_scan_media_values_are_the_spec_s(self, folder, values):
        assert SCAN_MEDIA_FOR_FOLDER[folder] == values


class TestWhatIsNotCollected:
    def test_romm_s_default_leaves_everything_but_screenshots_and_manuals_out(self):
        assert folders_not_collected(["box2d", "screenshot", "manual"]) == (
            "marquees",
            "titlescreens",
            "3dboxes",
            "backcovers",
            "physicalmedia",
            "fanart",
            "miximages",
            "videos",
        )

    def test_everything_collected_leaves_nothing_out(self):
        assert folders_not_collected(EVERY_VALUE) == ()

    def test_covers_are_never_listed_since_romm_always_stores_them(self):
        assert "covers" not in folders_not_collected([])

    def test_nothing_collected_lists_every_folder_but_covers_in_the_folders_order(self):
        assert folders_not_collected([]) == tuple(folder for folder in MEDIA_FOLDERS if folder != "covers")

    @pytest.mark.parametrize("video", ["video", "video_normalized"])
    def test_either_video_value_collects_videos(self, video):
        assert "videos" not in folders_not_collected([video])

    def test_a_value_romm_has_and_es_de_has_no_folder_for_changes_nothing(self):
        assert folders_not_collected([*EVERY_VALUE, "bezel", "box2d_side"]) == ()

    def test_values_are_matched_exactly(self):
        assert "marquees" in folders_not_collected(["LOGO", " logo"])

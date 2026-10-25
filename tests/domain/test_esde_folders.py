"""Tests for ``domain/esde_folders.py`` — ES-DE's own folders below RetroDECK's home."""

from __future__ import annotations

import os

from domain.esde_folders import EsdeFolders, esde_folders_under


class TestEsdeFoldersUnder:
    def test_each_folder_lies_in_es_de_s_folder_below_the_home(self):
        home = os.path.join(os.sep, "run", "media", "sdcard", "retrodeck")

        assert esde_folders_under(home) == EsdeFolders(
            gamelists=os.path.join(home, "ES-DE", "gamelists"),
            scripts=os.path.join(home, "ES-DE", "scripts"),
            downloaded_media=os.path.join(home, "ES-DE", "downloaded_media"),
        )

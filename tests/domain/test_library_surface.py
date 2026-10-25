"""Tests for ``domain/library_surface.py`` — the library surface's vocabulary and the two questions asked of it."""

from typing import get_args

import pytest

from domain.library_surface import (
    DEFAULT_LIBRARY_SURFACE,
    LIBRARY_SURFACES,
    LibrarySurface,
    creates_steam_shortcuts,
    library_surface,
    writes_esde,
)


class TestTheVocabulary:
    def test_the_values_are_the_types_own(self):
        assert get_args(LibrarySurface) == LIBRARY_SURFACES

    def test_the_default_is_steam(self):
        assert DEFAULT_LIBRARY_SURFACE == "steam"


class TestReadingASetting:
    @pytest.mark.parametrize("surface", ["steam", "esde", "both"])
    def test_a_named_surface_reads_as_itself(self, surface):
        assert library_surface(surface) == surface

    @pytest.mark.parametrize("value", [None, "", "ESDE", "Steam", " esde", "shortcuts", 0, 1, True, ["esde"], {}])
    def test_anything_else_reads_as_steam(self, value):
        assert library_surface(value) == "steam"


class TestWhatASurfaceDoes:
    @pytest.mark.parametrize(("surface", "expected"), [("steam", True), ("esde", False), ("both", True)])
    def test_whether_it_creates_steam_shortcuts(self, surface, expected):
        assert creates_steam_shortcuts(surface) is expected

    @pytest.mark.parametrize(("surface", "expected"), [("steam", False), ("esde", True), ("both", True)])
    def test_whether_it_writes_esde(self, surface, expected):
        assert writes_esde(surface) is expected

"""``EsdeProcessAdapter`` over a process table laid out under ``tmp_path``: whether an ES-DE process runs."""

from __future__ import annotations

import pytest

from adapters.esde_process import EsdeProcessAdapter


def _process(root, pid: str, *, comm: str | None = None, cmdline: bytes | None = None) -> None:
    folder = root / pid
    folder.mkdir()
    if comm is not None:
        (folder / "comm").write_text(comm + "\n", encoding="utf-8")
    if cmdline is not None:
        (folder / "cmdline").write_bytes(cmdline)


@pytest.fixture
def proc(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    (root / "self").mkdir()
    (root / "meminfo").write_text("MemTotal: 1 kB\n", encoding="utf-8")
    _process(root, "1", comm="systemd", cmdline=b"/sbin/init\0splash\0")
    _process(root, "512", comm="steam", cmdline=b"/home/deck/.steam/steam/ubuntu12_32/steam\0")
    return root


class TestWhetherEsdeRuns:
    def test_none_of_the_processes_being_es_de_answers_no(self, proc):
        assert EsdeProcessAdapter(proc_root=str(proc))() is False

    def test_a_process_named_es_de_answers_yes(self, proc):
        _process(proc, "2048", comm="es-de", cmdline=b"/app/bin/es-de\0--home\0/home/deck\0")

        assert EsdeProcessAdapter(proc_root=str(proc))() is True

    def test_the_name_is_matched_whatever_its_case(self, proc):
        _process(proc, "2048", comm="ES-DE")

        assert EsdeProcessAdapter(proc_root=str(proc))() is True

    def test_a_process_that_renamed_itself_is_known_by_the_program_it_runs(self, proc):
        _process(proc, "2048", comm="SDLAudioP2", cmdline=b"/app/bin/ES-DE\0--no-splash\0")

        assert EsdeProcessAdapter(proc_root=str(proc))() is True

    def test_a_process_whose_name_cannot_be_read_is_known_by_its_command_line(self, proc):
        _process(proc, "2048", cmdline=b"es-de\0")

        assert EsdeProcessAdapter(proc_root=str(proc))() is True

    def test_only_the_program_itself_counts_not_its_arguments_or_a_longer_name(self, proc):
        _process(proc, "2048", comm="bash", cmdline=b"/bin/bash\0/app/bin/es-de\0")
        _process(proc, "2049", comm="es-de-helper", cmdline=b"/usr/bin/es-de-helper\0")

        assert EsdeProcessAdapter(proc_root=str(proc))() is False

    def test_a_process_that_is_gone_mid_scan_is_passed_over(self, proc):
        _process(proc, "2048")

        assert EsdeProcessAdapter(proc_root=str(proc))() is False

    def test_a_folder_that_names_no_process_is_passed_over(self, proc):
        (proc / "es-de").mkdir()
        (proc / "es-de" / "comm").write_text("es-de\n", encoding="utf-8")

        assert EsdeProcessAdapter(proc_root=str(proc))() is False


class TestAProcessTableThatCannotBeRead:
    def test_it_answers_that_es_de_runs(self, tmp_path):
        assert EsdeProcessAdapter(proc_root=str(tmp_path / "missing"))() is True

    def test_a_file_where_the_table_should_be_answers_that_es_de_runs(self, tmp_path):
        (tmp_path / "proc").write_text("", encoding="utf-8")

        assert EsdeProcessAdapter(proc_root=str(tmp_path / "proc"))() is True

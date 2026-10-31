# test_brew_path.py - Tests for system-first PATH ordering (Bluefin parity)
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import subprocess
import tavern.brew_env as be


class TestDemoteBrewDirs:
    def test_moves_linuxbrew_after_system_bins(self):
        input_path = (
            '/home/linuxbrew/.linuxbrew/bin:'
            '/home/linuxbrew/.linuxbrew/sbin:'
            '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
        )
        expected = (
            '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:'
            '/home/linuxbrew/.linuxbrew/bin:'
            '/home/linuxbrew/.linuxbrew/sbin'
        )
        assert be.demote_brew_dirs(input_path) == expected

    def test_moves_homebrew_mac_after_system_bins(self):
        input_path = '/opt/homebrew/bin:/opt/homebrew/sbin:/usr/bin:/bin'
        expected = '/usr/bin:/bin:/opt/homebrew/bin:/opt/homebrew/sbin'
        assert be.demote_brew_dirs(input_path) == expected

    def test_idempotent(self):
        path = '/usr/bin:/bin:/home/linuxbrew/.linuxbrew/bin'
        first = be.demote_brew_dirs(path)
        second = be.demote_brew_dirs(first)
        assert first == path
        assert second == path

    def test_deduplicates_entries(self):
        path = '/usr/bin:/home/linuxbrew/.linuxbrew/bin:/usr/bin:/bin'
        assert be.demote_brew_dirs(path) == '/usr/bin:/bin:/home/linuxbrew/.linuxbrew/bin'

    def test_preserves_custom_user_dirs(self):
        path = (
            '/home/user/mybin:'
            '/home/linuxbrew/.linuxbrew/bin:'
            '/usr/bin:'
            '/home/user/my-homebrew-notes/bin'
        )
        # /home/user/my-homebrew-notes/bin is not an exact brew bin/sbin dir, so it stays
        expected = (
            '/home/user/mybin:'
            '/usr/bin:'
            '/home/user/my-homebrew-notes/bin:'
            '/home/linuxbrew/.linuxbrew/bin'
        )
        assert be.demote_brew_dirs(path) == expected

    def test_empty_or_none(self):
        assert be.demote_brew_dirs('') == ''
        assert be.demote_brew_dirs(None) == ''

    def test_custom_brew_dirs(self):
        custom = ['/custom/brew/bin', '/custom/brew/sbin']
        path = '/custom/brew/bin:/usr/bin:/bin'
        assert be.demote_brew_dirs(path, brew_dirs=custom) == '/usr/bin:/bin:/custom/brew/bin'


class TestCommandEnv:
    def test_command_env_reorders_path(self):
        base = {
            'PATH': '/home/linuxbrew/.linuxbrew/bin:/usr/bin:/bin',
            'CUSTOM_VAR': 'hello',
        }
        env = be.command_env(base)
        assert env['CUSTOM_VAR'] == 'hello'
        assert env['PATH'] == '/usr/bin:/bin:/home/linuxbrew/.linuxbrew/bin'


class TestFlatpakPathReorderSnippet:
    def test_build_command_in_flatpak_contains_reorder_snippet(self):
        cmd = be.build_command(['install', 'curl'], in_flatpak=True)
        assert cmd[0] == 'flatpak-spawn'
        assert '--host' in cmd
        joined = ' '.join(cmd)
        assert 'shellenv' in joined
        assert be._FLATPAK_PATH_REORDER_SNIPPET in joined
        assert 'brew install curl' in joined

    def test_flatpak_uses_discovered_host_executable_for_shellenv_and_command(self, tmp_path):
        brew = tmp_path / "brew's bin" / 'brew'
        brew.parent.mkdir()
        brew.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$BREW_LOG"\n')
        brew.chmod(0o755)
        log = tmp_path / 'calls'
        cmd = be.build_command(['install', 'evil;touch bad'], in_flatpak=True, brew_bin=str(brew))
        result = subprocess.run(['/bin/bash', '-c', cmd[-1]], cwd=tmp_path,
                                env={'PATH': '/usr/bin:/bin', 'BREW_LOG': str(log)},
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert log.read_text().splitlines() == ['shellenv', 'install', 'evil;touch bad']
        assert not (tmp_path / 'bad').exists()

    def test_custom_prefix_is_demoted_after_shellenv(self, tmp_path):
        prefix = tmp_path / 'custom'
        brew = prefix / 'bin' / 'brew'
        brew.parent.mkdir(parents=True)
        brew.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = shellenv ]; then\n'
            f'  echo "export HOMEBREW_PREFIX=\"{prefix}\""\n'
            f'  echo "export PATH=\"{prefix}/bin:{prefix}/sbin:/usr/bin:/bin\""\n'
            'else\n  printf "%s" "$PATH" > "$BREW_LOG"\nfi\n'
        )
        brew.chmod(0o755)
        log = tmp_path / 'path'
        cmd = be.build_command(['list'], in_flatpak=True, brew_bin=str(brew))
        result = subprocess.run(['/bin/bash', '-c', cmd[-1]],
                                env={'PATH': '/usr/bin:/bin', 'BREW_LOG': str(log)},
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert log.read_text() == f'/usr/bin:/bin:{prefix}/bin:{prefix}/sbin'

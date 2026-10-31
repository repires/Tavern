# test_brew_env.py - Tests for Homebrew environment discovery (src/brew_env.py)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# brew_env computes where Homebrew lives and how Tavern should invoke it, and
# sets the env vars that stop Homebrew's auto-update/ask prompts from hanging
# subprocess-driven install/remove/upgrade operations. It previously had no
# direct tests; these cover the Flatpak detection, the candidate/PATH search
# order, and the fallback contract — all without a Homebrew install or a
# display server.

import os
import subprocess

import pytest
import tavern.brew_env as be


# ─── env var setup ───────────────────────────────────────────────────────────

class TestHomebrewEnvVars:
    def test_no_auto_update_disabled(self):
        assert os.environ.get('HOMEBREW_NO_AUTO_UPDATE') == '1'

    def test_api_auto_update_secs_bounded(self):
        assert os.environ.get('HOMEBREW_API_AUTO_UPDATE_SECS') == '604800'

    def test_no_install_ask_disabled(self):
        assert os.environ.get('HOMEBREW_NO_INSTALL_ASK') == '1'


# ─── Flatpak detection ────────────────────────────────────────────────────────

class TestIsFlatpak:
    def test_false_without_marker(self, monkeypatch):
        monkeypatch.setattr(os.path, 'exists', lambda p: p != '/.flatpak-info')
        assert be._is_flatpak() is False

    def test_true_with_flatpak_marker(self, monkeypatch):
        monkeypatch.setattr(os.path, 'exists', lambda p: p == '/.flatpak-info')
        assert be._is_flatpak() is True


# ─── brew executable discovery ────────────────────────────────────────────────

class TestFindBrew:
    @pytest.fixture(autouse=True)
    def native(self, monkeypatch):
        monkeypatch.setattr(be, 'IN_FLATPAK', False)

    def test_prefers_first_candidate(self, monkeypatch):
        monkeypatch.setattr(os.path, 'isfile', lambda p: p == '/home/linuxbrew/.linuxbrew/bin/brew')
        monkeypatch.setattr(os, "access", lambda p, m: p == '/home/linuxbrew/.linuxbrew/bin/brew')
        assert be._find_brew() == '/home/linuxbrew/.linuxbrew/bin/brew'

    def test_second_candidate_used_when_first_missing(self, monkeypatch):
        def isfile(p):
            return p == '/opt/homebrew/bin/brew'
        monkeypatch.setattr(os.path, 'isfile', isfile)
        monkeypatch.setattr(os, "access", lambda p, m: p == '/opt/homebrew/bin/brew')
        assert be._find_brew() == '/opt/homebrew/bin/brew'

    def test_mac_candidate_used(self, monkeypatch):
        monkeypatch.setattr(os.path, 'isfile', lambda p: p == '/usr/local/bin/brew')
        monkeypatch.setattr(os, "access", lambda p, m: p == '/usr/local/bin/brew')
        assert be._find_brew() == '/usr/local/bin/brew'

    def test_candidate_ignored_when_not_executable(self, monkeypatch):
        # File exists but is not executable -> must fall through to PATH.
        monkeypatch.setattr(os.path, 'isfile', lambda p: p == '/opt/homebrew/bin/brew')
        monkeypatch.setattr(os, "access", lambda p, m: False)
        fake = subprocess.CompletedProcess(args=['which', 'brew'], returncode=1, stdout='', stderr='')
        monkeypatch.setattr(subprocess, 'run', lambda *a, **k: fake)
        assert be._find_brew() == 'brew'

    def test_path_fallback_finds_brew(self, monkeypatch):
        monkeypatch.setattr(os.path, 'isfile', lambda p: False)
        fake = subprocess.CompletedProcess(
            args=['which', 'brew'], returncode=0, stdout='/usr/local/bin/brew\n', stderr='')
        monkeypatch.setattr(subprocess, 'run', lambda *a, **k: fake)
        assert be._find_brew() == '/usr/local/bin/brew'

    def test_path_fallback_raises_returns_bare_brew(self, monkeypatch):
        monkeypatch.setattr(os.path, 'isfile', lambda p: False)

        def boom(*a, **k):
            raise OSError('which not available')
        monkeypatch.setattr(subprocess, 'run', boom)
        assert be._find_brew() == 'brew'

    def test_path_fallback_empty_stdout_uses_bare_brew(self, monkeypatch):
        """Edge case from #73: a successful `which` that prints nothing must not
        yield an empty BREW_BIN — the fallback contract is the bare 'brew'."""
        monkeypatch.setattr(os.path, 'isfile', lambda p: False)
        fake = subprocess.CompletedProcess(
            args=['which', 'brew'], returncode=0, stdout='', stderr='')
        monkeypatch.setattr(subprocess, 'run', lambda *a, **k: fake)
        assert be._find_brew() == 'brew'


class TestFlatpakFindBrew:
    @pytest.fixture(autouse=True)
    def flatpak(self, monkeypatch):
        monkeypatch.setattr(be, 'IN_FLATPAK', True)
        # Sandbox visibility must never determine host availability.
        monkeypatch.setattr(os.path, 'isfile', lambda p: False)

    def test_discovers_host_brew_and_refreshes(self, monkeypatch):
        path = '/home/linuxbrew/.linuxbrew/bin/brew'
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout=path + '\n', stderr='')

        monkeypatch.setattr(subprocess, 'run', run)
        monkeypatch.setattr(be, 'BREW_BIN', 'brew')
        assert be.refresh_brew_bin() == path
        assert be.is_brew_available()
        assert not be.should_offer_homebrew_install()
        assert calls[0][0][:4] == ['flatpak-spawn', '--host', 'bash', '-c']
        assert calls[0][1]['timeout'] == 5

    @pytest.mark.parametrize('status,output', [
        (1, ''), (0, ''), (0, 'brew\n'), (0, '/one\n/two\n'),
    ])
    def test_failed_or_invalid_probe_reports_missing(self, monkeypatch, status, output):
        monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(
            a[0], status, stdout=output, stderr=''))
        assert be._find_brew() == 'brew'
        assert be.should_offer_homebrew_install(be._find_brew())

    @pytest.mark.parametrize('error', [OSError('missing spawn'), subprocess.TimeoutExpired('spawn', 5)])
    def test_probe_errors_report_missing(self, monkeypatch, error):
        def fail(*args, **kwargs):
            raise error
        monkeypatch.setattr(subprocess, 'run', fail)
        assert be._find_brew() == 'brew'

    def test_host_probe_finds_home_install_via_host_shell(self, monkeypatch, tmp_path):
        brew = tmp_path / '.linuxbrew' / 'bin' / 'brew'
        brew.parent.mkdir(parents=True)
        brew.write_text('#!/bin/sh\nexit 0\n')
        brew.chmod(0o755)
        real_run = subprocess.run

        def host_run(argv, **kwargs):
            assert argv[:3] == ['flatpak-spawn', '--host', 'bash']
            script = argv[4].replace('/home/linuxbrew/.linuxbrew/bin/brew', '/nonexistent/tavern-brew')
            script = script.replace('/opt/homebrew/bin/brew', '/nonexistent/tavern-brew')
            script = script.replace('/usr/local/bin/brew', '/nonexistent/tavern-brew')
            return real_run(['/bin/bash', '-c', script],
                            env={'HOME': str(tmp_path), 'PATH': '/usr/bin:/bin'}, **kwargs)

        monkeypatch.setattr(subprocess, 'run', host_run)
        assert be._find_brew() == str(brew)
        brew.chmod(0o644)
        assert be._find_brew() == 'brew'

    def test_host_path_fallback(self, monkeypatch, tmp_path):
        brew = tmp_path / 'brew'
        brew.write_text('#!/bin/sh\nexit 0\n')
        brew.chmod(0o755)
        real_run = subprocess.run

        def host_run(argv, **kwargs):
            script = argv[4].replace('/home/linuxbrew/.linuxbrew/bin/brew', '/nonexistent/tavern-brew')
            script = script.replace('/opt/homebrew/bin/brew', '/nonexistent/tavern-brew')
            script = script.replace('/usr/local/bin/brew', '/nonexistent/tavern-brew')
            return real_run(['/bin/bash', '-c', script],
                            env={'HOME': str(tmp_path), 'PATH': str(tmp_path)}, **kwargs)

        monkeypatch.setattr(subprocess, 'run', host_run)
        assert be._find_brew() == str(brew)

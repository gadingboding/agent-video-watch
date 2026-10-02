#!/usr/bin/env python3
"""Base preflight, backend configuration, and an opt-in managed WhisperX install."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from config import (CONFIG_DIR, CONFIG_FILE, ConfigError, DETAILS, get_config,
                    load_api_key, read_env_file, write_settings)
from runtime import configure_stdio, diagnostic, run_text

REQUIRED_BINARIES = ['ffmpeg', 'ffprobe']
_PERM_WARNED: set[str] = set()
ENV_TEMPLATE = '''# /watch configuration. No shell interpolation; last assignment wins.
# Native captions always come first. Optional fallback: auto|minimax|groq|openai|none.
# auto checks MINIMAX_API_KEY, GROQ_API_KEY, then OPENAI_API_KEY.
# The first-run skill wizard sets WATCH_DETAIL and WATCH_ASR_BACKEND.

# MiniMax ASR: set MINIMAX_REGION to 'cn' (api.minimax.cn, default) or 'global' (api.minimax.io)
MINIMAX_API_KEY=
MINIMAX_REGION=cn

GROQ_API_KEY=
OPENAI_API_KEY=
'''


def _which(name):
    path = shutil.which(name)
    if not path:
        fallback = Path.home() / '.local' / 'bin' / name
        if fallback.is_file():
            return str(fallback)
    return path


def _check_binaries():
    missing = [name for name in REQUIRED_BINARIES if not _which(name)]
    if not (_which('uvx') or _which('uv') or _which('yt-dlp')):
        missing.append('uvx')
    return missing


def _check_file_permissions(path: Path) -> None:
    # Native Windows mode bits do not describe NTFS ACLs. WSL Linux homes do.
    if os.name == 'nt' or platform.system() == 'Windows' or str(path) in _PERM_WARNED:
        return
    try:
        if path.stat().st_mode & 0o044:
            _PERM_WARNED.add(str(path))
            print(f'[watch] WARNING: {path} is readable by other users. Run: chmod 600 "{path}"', file=sys.stderr)
    except OSError:
        pass


def _have_api_key():
    backend, key = load_api_key()
    return bool(key), backend


def is_first_run():
    return read_env_file(CONFIG_FILE).get('SETUP_COMPLETE') != 'true'


def _scaffold_env():
    if CONFIG_FILE.exists():
        read_env_file(CONFIG_FILE)  # Fail safely before modifying bad config.
        return False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(ENV_TEMPLATE)
    return True


def _write_setup_complete():
    _scaffold_env()
    write_settings({'SETUP_COMPLETE': 'true'}, CONFIG_FILE)


def _brew_pkg(missing):
    pkgs = []
    for name in missing:
        if name in ('ffmpeg', 'ffprobe'):
            pkgs.append('ffmpeg')
        elif name == 'uvx':
            pkgs.append('uv')
        else:
            pkgs.append(name)
    return list(dict.fromkeys(pkgs))


def _install_step(cmd, step):
    print(f"[setup] {step}: {' '.join(map(str, cmd))}", file=sys.stderr, flush=True)
    try:
        result = subprocess.run(list(map(str, cmd)))
    except OSError as exc:
        raise SystemExit(f'{step}: cannot run {cmd[0]} ({type(exc).__name__}); install or repair that executable, then retry.') from None
    if result.returncode:
        raise SystemExit(f'{step} failed (exit {result.returncode}): retry setup after checking the error above. '
                         'For WhisperX, allow at least 3 GB free disk and 8 GB RAM; package/model downloads need network access.')


def _install_macos(missing):
    if not _which('brew'):
        return False, 'Homebrew is missing. Install it from https://brew.sh, then rerun setup.'
    _install_step(['brew', 'install', *_brew_pkg(missing)], 'Install media dependencies')
    return True, 'Installed media dependencies with Homebrew.'


def _install_hint_linux(missing):
    hints = []
    if 'ffmpeg' in _brew_pkg(missing):
        hints.append('apt: sudo apt install ffmpeg (or the equivalent package for your distribution)')
    if 'uvx' in missing:
        hints.append('Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh')
    return '\n'.join(hints)


def _install_hint_windows(missing):
    hints = []
    if 'ffmpeg' in _brew_pkg(missing):
        hints.append('winget install --id Gyan.FFmpeg --exact')
    if 'uvx' in missing:
        hints.append('winget install --id astral-sh.uv --exact')
    return '\n'.join(hints) + '\nReopen your terminal/agent after PATH changes.'


def _probe(cmd):
    try:
        result = run_text(cmd, timeout=20)
        output = result.stdout or result.stderr
        return {'ok': result.returncode == 0, 'version_line': output.splitlines()[0] if output else None, 'output': diagnostic(output, 2500)}
    except SystemExit as exc:
        return {'ok': False, 'output': str(exc)}


def _status(detailed=False):
    missing = _check_binaries()
    _check_file_permissions(CONFIG_FILE)
    cfg = get_config()
    binaries_required = True
    has_key, detected = _have_api_key()
    chosen = cfg['whisper_backend']
    backend = detected if chosen == 'auto' else chosen
    backend_ready = bool(load_api_key(backend)[1]) if backend in ('minimax', 'groq', 'openai') else chosen == 'none'
    blocked = bool(missing)
    result = {'status': 'needs_install' if blocked else 'ready', 'can_proceed': not blocked,
              'binaries_required': binaries_required,
              'first_run': is_first_run(), 'setup_complete': not is_first_run(),
              'missing_binaries': missing, 'whisper_backend': backend, 'asr_backend': backend, 'configured_backend': chosen,
              'has_api_key': has_key, 'backend_ready': backend_ready,
              'config_file': str(CONFIG_FILE),
              'watch_detail': cfg['detail'], 'platform': platform.system()}
    if detailed:
        from download import ytdlp_cmd
        tools = {}
        for name in REQUIRED_BINARIES:
            path = _which(name)
            tools[name] = {'path': path, **(_probe([path, '-version']) if path else {'ok': False})}
        runner = ytdlp_cmd()
        ytdlp_probe = _probe([*runner, '--version'])
        tools['yt-dlp'] = {'runner': ' '.join(runner), 'ok': ytdlp_probe.get('ok', False), **ytdlp_probe}
        result['tools'] = tools
        result['youtube'] = {
            'deno': _which('deno'), 'node': _which('node'),
            'ejs': 'bundled via uvx (yt-dlp-ejs)' if any('uv' in p for p in runner[:2]) else 'unknown',
            'impersonation': _probe([*runner, '--ignore-config', '--list-impersonate-targets']) if ytdlp_probe.get('ok') else None,
            'update_hint': 'Executed via uvx (yt-dlp[default,curl-cffi]); isolated and updated automatically.',
        }
    return result


def cmd_check():
    # Optional credentials never block base watch.
    status = _status()
    if status['can_proceed']:
        return 0
    print(f"[watch] Missing {', '.join(status['missing_binaries'])}. Run python3 \"{Path(__file__).resolve()}\".", file=sys.stderr)
    return 2


def cmd_json():
    print(json.dumps(_status(detailed=True), indent=2))
    return 0


def cmd_install(backend=None, detail=None):
    missing = _check_binaries()
    if missing:
        system = platform.system()
        if system == 'Darwin':
            ok, message = _install_macos(missing)
            print(f'[setup] {message}', file=sys.stderr)
            if not ok or _check_binaries():
                return 2
        else:
            print(_install_hint_windows(missing) if system == 'Windows' else _install_hint_linux(missing), file=sys.stderr)
            return 2
    _scaffold_env()
    if detail:
        write_settings({'WATCH_DETAIL': detail}, CONFIG_FILE)
    if backend:
        write_settings({'WATCH_ASR_BACKEND': backend, 'WATCH_WHISPER_BACKEND': backend}, CONFIG_FILE)
        if backend in ('minimax', 'groq', 'openai') and not load_api_key(backend)[1]:
            print(f'[setup] Base watch is ready. Add {backend.upper()}_API_KEY privately to {CONFIG_FILE}, then rerun --backend {backend}.', file=sys.stderr)
            return 3
        _write_setup_complete()
    print(f'[setup] Base watch is ready. Configuration: {CONFIG_FILE}')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--json', action='store_true')
    parser.add_argument('--backend', choices=['auto', 'minimax', 'groq', 'openai', 'none'])
    parser.add_argument('--detail', choices=sorted(DETAILS))
    args = parser.parse_args()
    try:
        if args.check:
            return cmd_check()
        if args.json:
            return cmd_json()
        return cmd_install(args.backend, args.detail)
    except (ConfigError, OSError, ValueError) as exc:
        print(f'[setup] {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    configure_stdio()
    raise SystemExit(main())

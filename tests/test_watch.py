"""End-to-end routing of --detail through watch.py on a local clip."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WATCH = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts" / "watch.py"


def _run(clip: Path, *args: str, env_extra: dict | None = None) -> str:
    env = dict(os.environ)
    env.pop("WATCH_DETAIL", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(WATCH), str(clip), "--no-whisper", *args],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_efficient_uses_keyframe_engine(cut_clip: Path):
    out = _run(cut_clip, "--detail", "efficient")
    assert "(keyframe" in out
    assert "**Detail:** efficient" in out


def test_balanced_uses_scene_engine(cut_clip: Path):
    out = _run(cut_clip, "--detail", "balanced")
    assert "(scene" in out
    assert "**Detail:** balanced" in out


def test_token_burner_uses_scene_engine(cut_clip: Path):
    out = _run(cut_clip, "--detail", "token-burner")
    assert "(scene" in out


def test_transcript_skips_frames(cut_clip: Path):
    out = _run(cut_clip, "--detail", "transcript")
    assert "skipped" in out
    assert "frame_0000.jpg" not in out


def test_flag_overrides_env(cut_clip: Path):
    out = _run(cut_clip, "--detail", "efficient", env_extra={"WATCH_DETAIL": "balanced"})
    assert "(keyframe" in out


def test_default_is_balanced(cut_clip: Path):
    out = _run(cut_clip)  # no flag, WATCH_DETAIL cleared
    assert "**Detail:** balanced" in out
    assert "(scene" in out


def test_timestamps_add_cue_frames_to_detail(cut_clip: Path):
    out = _run(cut_clip, "--detail", "balanced", "--timestamps", "1,3")
    assert "reason=transcript-cue" in out
    assert "reason=scene-change" in out  # detail frames still present (additive)


def test_timestamps_with_transcript_detail_is_cue_only(cut_clip: Path):
    out = _run(cut_clip, "--detail", "transcript", "--timestamps", "1,3")
    assert "reason=transcript-cue" in out
    assert "reason=scene-change" not in out
    assert "reason=keyframe" not in out


def _frame_lines(out: str) -> int:
    return sum(1 for line in out.splitlines() if "/frames/frame_" in line.replace("\\", "/") and "(t=" in line)


def test_dedup_collapses_static_by_default(static_clip: Path):
    out = _run(static_clip)  # solid blue → identical frames collapse to one
    assert "near-duplicate" in out
    assert _frame_lines(out) == 1


def test_no_dedup_preserves_static_frames(static_clip: Path):
    out = _run(static_clip, "--no-dedup")
    assert "near-duplicate" not in out
    assert _frame_lines(out) > 1


import pytest
import watch
from transcribe import Segments


def _caption_context(tmp_path, body):
    media = tmp_path / 'video.mp4'
    media.write_bytes(b'video')
    subtitle = tmp_path / 'video.vtt'
    subtitle.write_text('WEBVTT\n\n' + body)
    return media


def test_silent_focus_never_calls_asr(monkeypatch, tmp_path, capsys):
    media = _caption_context(tmp_path, '00:00.000 --> 00:10.000\nhello\n')
    monkeypatch.setattr(watch, 'get_metadata', lambda *a: {'duration_seconds': 30, 'has_audio': True, 'has_video': False})
    monkeypatch.setattr(watch, 'transcribe_video', lambda *a, **kw: pytest.fail('a silent focus is not a missing track'))
    monkeypatch.setattr(sys, 'argv', ['watch', str(media), '--detail', 'transcript', '--start', '20', '--end', '30'])
    assert watch.main() == 0
    assert 'no speech in selected range' in capsys.readouterr().out


def test_urls_are_rejected(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['watch', 'https://example.com/video'])
    with pytest.raises(SystemExit) as exc:
        watch.main()
    assert 'URLs are not supported' in str(exc.value)


def test_missing_local_file_is_rejected(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['watch', '/nonexistent/media.mp4'])
    with pytest.raises(SystemExit) as exc:
        watch.main()
    assert 'File not found' in str(exc.value)


def test_audio_only_input_skips_frames(monkeypatch, tmp_path, capsys):
    audio = tmp_path / 'audio.mp3'
    audio.write_bytes(b'dummy')
    monkeypatch.setattr(watch, 'get_metadata', lambda *a: {'duration_seconds': 10, 'has_audio': True, 'has_video': False})
    monkeypatch.setattr(watch, 'load_api_key', lambda *a: ('groq', 'dummy'))
    monkeypatch.setattr(watch, 'transcribe_video', lambda *a, **kw: ([{'start': 0, 'end': 1, 'text': 'spoken'}], 'groq'))
    monkeypatch.setattr(sys, 'argv', ['watch', str(audio)])
    assert watch.main() == 0
    out = capsys.readouterr().out
    assert 'audio report' in out
    assert 'skipped (audio-only input)' in out
    assert 'spoken' in out


def _mock_audio(monkeypatch):
    monkeypatch.setattr(watch, 'resolve_source', lambda src: (Path(src), None))
    monkeypatch.setattr(watch, 'get_metadata', lambda *a: {'duration_seconds': 30, 'has_audio': True, 'has_video': False})


def test_partial_report_includes_missing_intervals(monkeypatch, capsys):
    _mock_audio(monkeypatch)
    monkeypatch.setattr(watch, 'load_api_key', lambda *a: ('groq', 'dummy'))
    monkeypatch.setattr(watch, 'transcribe_video', lambda *a, **kw: (Segments([{'start': 1, 'end': 2, 'text': 'good'}], gaps=[{'start': 10, 'end': 20}]), 'groq'))
    monkeypatch.setattr(sys, 'argv', ['watch', 'video.mp4', '--detail', 'transcript'])
    assert watch.main() == 0
    out = capsys.readouterr().out
    assert 'partial; missing intervals' in out and '00:10 → 00:20' in out


def test_no_whisper_disables_configured_backend(monkeypatch):
    _mock_audio(monkeypatch)
    monkeypatch.setenv('WATCH_WHISPER_BACKEND', 'minimax')
    monkeypatch.setattr(watch, 'transcribe_video', lambda *a, **kw: pytest.fail('fallback disabled'))
    monkeypatch.setattr(sys, 'argv', ['watch', 'video.mp4', '--detail', 'transcript', '--no-whisper'])
    assert watch.main() == 0


def test_backend_failure_exits_with_error(monkeypatch, capsys):
    _mock_audio(monkeypatch)
    monkeypatch.setattr(watch, 'load_api_key', lambda *a: ('minimax', 'key'))
    def fail(*a, **kw):
        assert kw['backend'] == 'minimax'
        raise SystemExit('minimax failed')
    monkeypatch.setattr(watch, 'transcribe_video', fail)
    monkeypatch.setattr(sys, 'argv', ['watch', 'video.mp4', '--detail', 'transcript', '--whisper', 'minimax'])
    assert watch.main() == 1
    assert 'minimax failed' in capsys.readouterr().out


def test_contradictory_flags_rejected(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['watch', 'video.mp4', '--no-whisper', '--whisper', 'minimax'])
    with pytest.raises(SystemExit):
        watch.main()


def test_user_out_dir_and_local_source_preserved(static_clip, tmp_path):
    source = tmp_path / 'source.mp4'
    source.write_bytes(static_clip.read_bytes())
    sentinel = tmp_path / 'keep.txt'
    sentinel.write_text('keep')
    _run(source, '--out-dir', str(tmp_path))
    assert source.read_bytes() == static_clip.read_bytes() and sentinel.read_text() == 'keep'
    assert list(tmp_path.glob('watch-*/frames/frame_*.jpg'))


def test_cli_no_whisper_overrides_invalid_saved_backend(monkeypatch):
    _mock_audio(monkeypatch)
    monkeypatch.setenv('WATCH_WHISPER_BACKEND', 'invalid-old-preference')
    monkeypatch.setattr(sys, 'argv', ['watch', 'video.mp4', '--detail', 'transcript', '--no-whisper'])
    assert watch.main() == 0


def test_watch_extracts_frames(monkeypatch, static_clip, capsys):
    monkeypatch.setattr(sys, 'argv', ['watch', str(static_clip), '--no-whisper'])
    assert watch.main() == 0
    assert '## Frames' in capsys.readouterr().out


def test_no_asr_disables_configured_backend(monkeypatch):
    _mock_audio(monkeypatch)
    monkeypatch.setenv('WATCH_ASR_BACKEND', 'minimax')
    monkeypatch.setattr(watch, 'transcribe_video', lambda *a, **kw: pytest.fail('fallback disabled'))
    monkeypatch.setattr(sys, 'argv', ['watch', 'video.mp4', '--detail', 'transcript', '--no-asr'])
    assert watch.main() == 0


def test_cli_asr_flag(monkeypatch, capsys):
    _mock_audio(monkeypatch)
    monkeypatch.setattr(watch, 'load_api_key', lambda *a: ('minimax', 'key'))
    called_backend = []
    def fake_transcribe(*a, **kw):
        called_backend.append(kw.get('backend'))
        return [{'start': 0, 'end': 1, 'text': 'ok'}], 'minimax'
    monkeypatch.setattr(watch, 'transcribe_video', fake_transcribe)
    monkeypatch.setattr(sys, 'argv', ['watch', 'video.mp4', '--detail', 'transcript', '--asr', 'minimax'])
    assert watch.main() == 0
    assert called_backend == ['minimax']


def test_save_subs_creates_vtt_file(monkeypatch, tmp_path, capsys):
    media = tmp_path / 'clip.mp4'
    media.write_bytes(b'video')
    expected_vtt = tmp_path / 'clip.vtt'
    assert not expected_vtt.exists()

    monkeypatch.setattr(watch, 'get_metadata', lambda *a: {'duration_seconds': 10, 'has_audio': True, 'has_video': False})
    monkeypatch.setattr(watch, 'load_api_key', lambda *a: ('groq', 'dummy'))
    monkeypatch.setattr(watch, 'transcribe_video', lambda *a, **kw: ([{'start': 0, 'end': 2, 'text': 'saved subtitle test'}], 'groq'))
    monkeypatch.setattr(sys, 'argv', ['watch', str(media), '--save-subs'])

    assert watch.main() == 0
    assert expected_vtt.is_file()
    assert 'saved subtitle test' in expected_vtt.read_text(encoding='utf-8')
    out = capsys.readouterr().out
    assert f'Saved subtitles:** `{expected_vtt}`' in out



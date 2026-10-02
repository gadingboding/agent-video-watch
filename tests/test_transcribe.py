"""VTT syntax, independent repeated speech, and source segment validation."""
import pytest
from transcribe import parse_vtt, normalize_segments, format_vtt, save_vtt, format_vtt_timestamp


def parse(tmp_path, body):
    path = tmp_path / 'captions.vtt'
    path.write_bytes(body.encode('utf-8'))  # Exact bytes: text mode on Windows would turn \r\n into \r\r\n.
    return parse_vtt(str(path))


def test_blocks_ids_settings_padding_and_missing_separator(tmp_path):
    out = parse(tmp_path, '\ufeffWEBVTT\r\n\r\nNOTE comment\r\n00:00.000 --> 00:01.000\r\nskip\r\n\r\nSTYLE\r\n::cue {}\r\n\r\nREGION\r\nid:one\r\n\r\ncue-id\r\n00:01.000 --> 00:02.000 align:start\r\nfirst\r\n   \r\n<c>last</c>\r\n00:03.000 --> 00:04.000\r\nnext\r\n')
    assert [s['text'] for s in out] == ['first  last', 'next']


def test_valid_long_hours_and_invalid_minutes(tmp_path):
    out = parse(tmp_path, 'WEBVTT\n\n100:02:03.004 --> 100:02:04.000\nx\n\n00:60.000 --> 01:01.000\ninvalid\n')
    assert len(out) == 1 and out[0]['start'] == 360123.004


def test_overlapping_prefix_only_and_mixed_karaoke(tmp_path):
    out = parse(tmp_path, 'WEBVTT\n\n00:01.000 --> 00:03.000\nThank you\n\n00:02.000 --> 00:04.000\nThank you all\n\n00:04.000 --> 00:05.000\nThank you all\n\n00:05.000 --> 00:06.000\nplain <00:05.100><c>spoken</c> ending &lt;literal&gt;\n')
    assert [s['text'] for s in out] == ['Thank you all', 'Thank you all', 'plain spoken ending <literal>']


@pytest.mark.parametrize('segment', [
    {'start': float('nan'), 'end': 1, 'text': 'hi'}, {'start': 2, 'end': 1, 'text': 'hi'},
    {'start': True, 'end': 1, 'text': 'hi'}, {'start': 0, 'end': 1, 'text': ' '},
    {'start': 0, 'end': float('inf'), 'text': 'hi'},
])
def test_rejects_invalid_segments(segment):
    with pytest.raises((ValueError, TypeError)):
        normalize_segments({'segments': [segment]})


def test_overlapping_separate_display_regions_are_not_merged(tmp_path):
    out = parse(tmp_path, 'WEBVTT\n\n00:00.000 --> 00:02.000 line:10%\nHello\n\n00:01.000 --> 00:03.000 line:90%\nHello\n')
    assert len(out) == 2
    assert all(set(s) == {'start', 'end', 'text'} for s in out)


def test_format_vtt_timestamp():
    assert format_vtt_timestamp(0) == "00:00:00.000"
    assert format_vtt_timestamp(1.5) == "00:00:01.500"
    assert format_vtt_timestamp(65.123) == "00:01:05.123"
    assert format_vtt_timestamp(3661.05) == "01:01:01.050"


def test_save_and_parse_vtt_roundtrip(tmp_path):
    segments = [
        {"start": 1.25, "end": 4.5, "text": "Hello world"},
        {"start": 5.0, "end": 8.125, "text": "This is a test"},
    ]
    vtt_path = tmp_path / "test.vtt"
    save_vtt(segments, vtt_path)
    assert vtt_path.is_file()
    parsed = parse_vtt(str(vtt_path))
    assert parsed == [
        {"start": 1.25, "end": 4.5, "text": "Hello world"},
        {"start": 5.0, "end": 8.125, "text": "This is a test"},
    ]

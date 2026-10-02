#!/usr/bin/env python3
"""/watch entry point: analyze local video or audio files, extract frames, parse transcript.

Prints a markdown report to stdout listing frame paths + transcript. Claude
then Reads each frame path to see the video.
"""
from __future__ import annotations

import argparse
import math
import shutil
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from config import ConfigError, frame_cap, get_config  # noqa: E402
from frames import MAX_FPS, auto_fps, auto_fps_focus, extract_at_timestamps, extract_keyframes, extract_scene_or_uniform, format_time, get_metadata, merge_frames, parse_time, parse_timestamps, validate_controls  # noqa: E402
from transcribe import filter_range, format_transcript, parse_vtt, save_vtt  # noqa: E402
from asr import load_api_key, transcribe_video  # noqa: E402
from runtime import configure_stdio  # noqa: E402


def resolve_source(source: str) -> tuple[Path, Path | None]:
    """Validate source path and find optional companion subtitle file.

    Returns (media_path, subtitle_path).
    Rejects URLs and nonexistent files.
    """
    if source.startswith(("http://", "https://")) or "://" in source:
        raise SystemExit("URLs are not supported; please provide a local video or audio file.")
    p = Path(source).expanduser().resolve()
    if not p.is_file():
        raise SystemExit(f"File not found: {p}")

    subtitle_path = None
    for candidate in (p.with_suffix(".vtt"), p.parent / f"{p.name}.vtt"):
        if candidate.is_file():
            subtitle_path = candidate
            break

    return p, subtitle_path


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="watch",
        description="Analyze a local video or audio file, extract auto-scaled frames, and surface the transcript.",
    )
    ap.add_argument("source", help="Local video or audio file path")
    ap.add_argument("--max-frames", type=int, default=None, help="Override frame cap")
    ap.add_argument("--resolution", type=int, default=512, help="Frame width in pixels (default 512)")
    ap.add_argument("--fps", type=float, default=None, help="Override auto-fps")
    ap.add_argument(
        "--detail",
        choices=["transcript", "efficient", "balanced", "token-burner"],
        default=None,
        help="Fidelity/speed dial: transcript (no frames), efficient (fast keyframes, cap 50), "
             "balanced (scene, cap 100), token-burner (scene, uncapped).",
    )
    ap.add_argument(
        "--timestamps",
        type=str,
        default=None,
        help="Comma-separated absolute timestamps (SS, MM:SS, HH:MM:SS) to grab a frame at, "
             "e.g. transcript-flagged 'look here' moments. Added on top of the detail frames "
             "(reserved against the cap); with --detail transcript these become the only frames.",
    )
    ap.add_argument("--start", type=str, default=None, help="Range start (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument("--end", type=str, default=None, help="Range end (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument("--out-dir", type=str, default=None, help="Working directory (default: tmp)")
    ap.add_argument(
        "--no-asr",
        "--no-whisper",
        dest="no_asr",
        action="store_true",
        help="Disable all external ASR transcription fallbacks; native captions still work.",
    )
    ap.add_argument(
        "--asr",
        "--whisper",
        dest="asr",
        choices=["groq", "openai", "minimax"],
        default=None,
        help="Select the fallback ASR backend for this run (groq, openai, minimax); native captions still come first.",
    )
    ap.add_argument(
        "--no-dedup",
        action="store_true",
        help="Disable near-duplicate frame removal. Keeps visually identical "
             "frames (static screen recordings, held slides) instead of collapsing them.",
    )
    ap.add_argument(
        "--save-subs",
        action="store_true",
        default=None,
        help="Save transcribed subtitles to a companion .vtt file next to the source media (default)",
    )
    ap.add_argument(
        "--no-save-subs",
        action="store_true",
        default=False,
        help="Disable automatic saving of transcribed subtitles to a companion .vtt file",
    )
    ap.add_argument("--sub-lang", default=None, help="Exact caption language preference (default auto/native)")
    ap.add_argument("--question", default=None,
                    help="The user's question about the video or audio.")
    args = ap.parse_args()
    args.no_whisper = args.no_asr
    args.whisper = args.asr
    if args.no_asr and args.asr:
        ap.error("--no-asr conflicts with --asr")
    if args.save_subs and args.no_save_subs:
        ap.error("--save-subs conflicts with --no-save-subs")

    config = get_config(backend_override="none" if args.no_asr else args.asr)
    detail = args.detail or config["detail"]
    if args.no_save_subs:
        save_subs = False
    elif args.save_subs:
        save_subs = True
    else:
        save_subs = config.get("save_subs", True)
    max_frames = args.max_frames if args.max_frames is not None else frame_cap(detail)
    budget_cap = max_frames if max_frames is not None else 100
    start_sec, end_sec = parse_time(args.start), parse_time(args.end)
    validate_controls(args.resolution, max_frames, start_sec, end_sec, args.fps)
    cue_timestamps = parse_timestamps(args.timestamps)
    backend_choice = "none" if args.no_asr else (args.asr or config.get("asr_backend") or config.get("whisper_backend"))

    media_path, subtitle_path = resolve_source(args.source)
    video_path = str(media_path)
    title = media_path.name

    parent = Path(args.out_dir).expanduser().resolve() if args.out_dir else None
    if parent:
        parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="watch-", dir=parent))
    print(f"[watch] working dir: {work}", file=sys.stderr)

    errors = []
    all_segments = []
    track_available = False
    transcript_source = None
    visual_error = None
    gaps = []

    if subtitle_path:
        try:
            all_segments = parse_vtt(str(subtitle_path))
            track_available = bool(all_segments)
            transcript_source = f"local captions ({subtitle_path.name})"
        except (OSError, ValueError) as exc:
            errors.append(f"Caption parsing failed: {exc}")

    meta = {"duration_seconds": 0.0, "width": None, "height": None, "codec": None,
            "has_audio": False, "has_video": False}
    try:
        meta = get_metadata(video_path)
    except SystemExit as exc:
        visual_error = f"Visuals/audio metadata unavailable: {exc}"
        errors.append(visual_error)

    full_duration = meta["duration_seconds"]
    if full_duration > 0 and start_sec is not None and start_sec >= full_duration:
        raise SystemExit(f"--start {start_sec:.1f}s is past end of media ({full_duration:.1f}s)")
    effective_start = start_sec or 0.0
    effective_end = min(end_sec, full_duration) if end_sec is not None and full_duration > 0 else end_sec or full_duration
    effective_duration = max(0.0, effective_end - effective_start)
    focused = start_sec is not None or end_sec is not None
    if focused:
        fps, target = auto_fps_focus(effective_duration, max_frames=budget_cap)
    else:
        fps, target = auto_fps(effective_duration, max_frames=budget_cap)
    if args.fps is not None:
        fps = min(args.fps, MAX_FPS)
        target = max(1, int(round(fps * effective_duration)))

    frames, cue_frames = [], []
    frame_meta = {"engine": "none", "candidate_count": 0, "selected_count": 0, "fallback": False}
    cue_meta = {}
    detail_budget = max_frames
    is_video = bool(meta.get("has_video", bool(meta.get("width"))))

    if video_path and is_video:
        try:
            if cue_timestamps:
                cue_frames, cue_meta = extract_at_timestamps(
                    video_path, work / "frames", cue_timestamps, resolution=args.resolution,
                    max_frames=max_frames, start_seconds=start_sec, end_seconds=effective_end or end_sec)
            detail_budget = None if max_frames is None else max_frames - len(cue_frames)
            if detail != "transcript" and detail_budget != 0:
                kwargs = dict(resolution=args.resolution, max_frames=detail_budget,
                              start_seconds=start_sec, end_seconds=effective_end or end_sec, dedup=not args.no_dedup)
                if detail == "efficient":
                    frames, frame_meta = extract_keyframes(video_path, work / "frames", **kwargs)
                else:
                    frames, frame_meta = extract_scene_or_uniform(video_path, work / "frames", fps=fps, target_frames=target, **kwargs)
        except SystemExit as exc:
            visual_error = f"Visual extraction unavailable: {exc}"
            errors.append(visual_error)
    elif video_path and not is_video and not visual_error and (detail != "transcript" or cue_timestamps):
        pass  # Audio-only input; skipping frames naturally

    if cue_frames:
        frames = merge_frames(frames, cue_frames)

    transcript_state = "missing"
    if not track_available and backend_choice != "none" and video_path and meta.get("has_audio"):
        backend, api_key = load_api_key(None if backend_choice == "auto" else backend_choice)
        if backend:
            try:
                all_segments, used_backend = transcribe_video(video_path, work / "audio.mp3", backend=backend, api_key=api_key)
                gaps = getattr(all_segments, "gaps", [])
                track_available = True
                transcript_state = "no speech" if not all_segments else "available"
                if used_backend == "minimax":
                    transcript_source = "minimax (asr-1.0)"
                else:
                    transcript_source = f"whisper ({used_backend})"
            except SystemExit as exc:
                transcript_state = "failed"
                errors.append(f"Transcription failed: {exc}")
        else:
            transcript_state = f"unavailable: no matching API key for {backend_choice}"
    elif not track_available and backend_choice == "none":
        transcript_state = "fallback disabled; no captions available"
    elif not track_available and video_path and not meta.get("has_audio"):
        transcript_state = "no audio stream" if not visual_error else "audio metadata unavailable"

    newly_saved_subs = None
    existing_sub_path = subtitle_path
    if save_subs:
        if existing_sub_path:
            print(f"[watch] companion subtitles already exist: {existing_sub_path}", file=sys.stderr)
        elif all_segments:
            target_vtt = media_path.with_suffix(".vtt") if media_path.suffix.lower() != ".vtt" else media_path.parent / f"{media_path.name}.vtt"
            if target_vtt.is_file():
                existing_sub_path = target_vtt
                print(f"[watch] companion subtitles already exist: {target_vtt}", file=sys.stderr)
            else:
                try:
                    save_vtt(all_segments, target_vtt)
                    newly_saved_subs = target_vtt
                    print(f"[watch] saved subtitles to {target_vtt}", file=sys.stderr)
                except OSError as exc:
                    errors.append(f"Failed to save subtitles to {target_vtt}: {exc}")

    transcript_segments = filter_range(all_segments, start_sec, end_sec) if focused else all_segments
    transcript_text = format_transcript(transcript_segments)
    if track_available:
        transcript_state = "available" if transcript_segments else ("no speech in selected range" if focused and all_segments else "no speech")
    for error in errors:
        print(f"[watch] {error}", file=sys.stderr)

    media_kind = "video" if is_video else "audio"

    print()
    print(f"# watch: {media_kind} report")
    print()
    print(f"- **Source:** {args.source}")
    if video_path:
        print(f"- **Local media:** `{video_path}`")
    if newly_saved_subs:
        print(f"- **Saved subtitles:** `{newly_saved_subs}`")
    elif existing_sub_path:
        print(f"- **Companion subtitles:** `{existing_sub_path}`")
    if visual_error:
        print(f"- **Visual status:** {visual_error}")
    if errors:
        print("- **Result:** partial evidence" if frames or transcript_segments else "- **Result:** unavailable evidence")
    print(f"- **Title:** {title}")
    print(f"- **Duration:** {format_time(full_duration)} ({full_duration:.1f}s)")
    if focused:
        print(
            f"- **Focus range:** {format_time(effective_start)} → {format_time(effective_end)} "
            f"({effective_duration:.1f}s)"
        )
    if meta.get("width") and meta.get("height"):
        print(f"- **Resolution:** {meta['width']}x{meta['height']} ({meta.get('codec') or 'unknown codec'})")
    range_mode = "focused" if focused else "full"
    print(f"- **Detail:** {detail}")
    detail_count = frame_meta.get("selected_count", 0)
    if not is_video:
        print("- **Frames:** skipped (audio-only input)")
    elif detail != "transcript":
        cap_label = "unlimited" if detail_budget is None else str(detail_budget)
        engine = frame_meta.get("engine", "scene")
        fallback = " fallback" if frame_meta.get("fallback") else ""
        deduped = frame_meta.get("deduped_count", 0)
        dedup_note = f", {deduped} near-duplicate{'s' if deduped != 1 else ''} dropped" if deduped else ""
        print(
            f"- **Frames:** {detail_count} selected from {frame_meta.get('candidate_count', detail_count)} "
            f"candidates ({engine}{fallback}{dedup_note}, {range_mode} range, budget {target}, cap {cap_label})"
        )
    elif not cue_frames:
        print("- **Frames:** skipped (transcript detail)")
    if cue_frames:
        dropped = cue_meta.get("dropped_out_of_window", 0)
        drop_note = f", {dropped} dropped outside range" if dropped else ""
        print(
            f"- **Cue frames:** {len(cue_frames)} at transcript-flagged timestamps "
            f"(transcript-cue{drop_note})"
        )
    if frames:
        print(f"- **Frame size:** max {args.resolution}px wide, max 1998px tall")
    if transcript_segments:
        in_range = " in range" if focused else ""
        print(
            f"- **Transcript:** {len(transcript_segments)} segments{in_range} "
            f"(via {transcript_source or 'captions'})"
        )
    else:
        print(f"- **Transcript:** {transcript_state}")
    if gaps:
        print("- **Transcript status:** partial; missing intervals:")
        for gap in gaps:
            end = gap["end"] if gap["end"] is not None else full_duration
            print(f"  - {format_time(gap['start'])} → {format_time(end)}")

    if is_video and detail == "token-burner" and len(frames) > 250:
        print()
        print(
            f"> **Warning:** token-burner detail selected {len(frames)} frames. "
            "This may use a large number of image tokens."
        )

    if is_video and not focused and full_duration > 600 and detail not in ("transcript", "token-burner"):
        mins = int(full_duration // 60)
        print()
        print(
            f"> **Warning:** This is a {mins}-minute video. Frame coverage is sparse at this length "
            f"under `{detail}` detail — its cap spreads thin across the full clip. For better results, "
            "re-run with `--start HH:MM:SS --end HH:MM:SS` to zoom into a section, or use "
            "`--detail token-burner` to keep every scene-change frame across the whole video."
        )

    print()
    print("## Frames")
    print()
    if frames:
        print(f"Frames live at: `{work / 'frames'}`")
        print()
        print(
            "**Read each frame path below with the Read tool to view the image.** "
            "Frames are in chronological order; `t=MM:SS` is the absolute timestamp in the source video."
        )
        print()
        for frame in frames:
            print(
                f"- `{frame['path']}` "
                f"(t={format_time(frame['timestamp_seconds'])}, reason={frame.get('reason', 'selected')})"
            )
    else:
        print("_No frames extracted._")

    print()
    print("## Transcript")
    print()
    if transcript_text:
        label = transcript_source or "captions"
        if focused:
            print(f"_Source: {label}. Filtered to {format_time(effective_start)} → {format_time(effective_end)}:_")
        else:
            print(f"_Source: {label}._")
        print()
        print("```")
        print(transcript_text)
        print("```")
    else:
        print(f"_{transcript_state.capitalize()}._")
        if detail == "transcript" and not track_available:
            print("_Re-run with `--detail balanced` for visual evidence, or configure a transcription backend with setup.py._")
    if errors:
        print()
        print("## Unavailable evidence")
        for error in errors:
            print(f"- {error}")

    print()
    print("---")
    print(f"_Work dir: `{work}` — delete when done._")

    return 1 if errors and not frames and not transcript_segments and not track_available else 0


if __name__ == "__main__":
    configure_stdio()
    try:
        raise SystemExit(main())
    except (ConfigError, OSError) as exc:
        raise SystemExit(str(exc)) from None

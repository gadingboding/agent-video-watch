# WhatDifference

本项目相较于 upstream 主线（`bradautomates/claude-video`）进行了以下两项变更：

### 1. 完全移除 Gemini 模式，仅保留纯本地运行
- 彻底移除 Google Gemini 云端多模态视频分析，不向 Google 上传任何视频文件，仅保留本地抽帧与字幕转录。
- 删除了 `skills/watch/scripts/gemini.py` 与 `tests/test_gemini.py`。
- 移除了 `watch.py`、`setup.py`、`config.py` 中的 `--engine` 参数、Gemini 依赖检测、`GEMINI_API_KEY` 及相关配置。
- 清理了 `SKILL.md`、`README.md` 与测试用例中的 Gemini 相关内容。

### 2. `yt-dlp` 改为直接通过 `uvx` 运行（免本地预装）
- 无需本地预装 `yt-dlp`，改为在需要时直接通过 `uvx --quiet --from "yt-dlp[default,curl-cffi]" yt-dlp` 运行。
- `download.py` 中新增 `ytdlp_cmd()` 优先调用 `uvx`。
- `setup.py` 中的基础前置依赖由系统安装 `yt-dlp` 改为依赖 `uvx`。

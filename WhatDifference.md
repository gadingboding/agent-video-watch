# WhatDifference

本项目相较于 upstream 主线（`bradautomates/claude-video`）进行了以下变更：

### 1. 完全移除 Gemini 模式，仅保留纯本地运行
- 彻底移除 Google Gemini 云端多模态视频分析，不向 Google 上传任何视频文件，仅保留本地抽帧与字幕转录。
- 删除了 `skills/watch/scripts/gemini.py` 与 `tests/test_gemini.py`。
- 移除了 `watch.py`、`setup.py`、`config.py` 中的 `--engine` 参数、Gemini 依赖检测、`GEMINI_API_KEY` 及相关配置。
- 清理了 `SKILL.md`、`README.md` 与测试用例中的 Gemini 相关内容。

### 2. 彻底移除网络下载功能（yt-dlp），仅支持本地视频/音频作为输入
- 彻底移除 `yt-dlp` 下载、URL 解析与远程字幕获取逻辑，删除了 `skills/watch/scripts/download.py` 与 `tests/test_download.py`。
- 移除了 `--cookies`、`--cookies-from-browser`、`WATCH_COOKIES_FILE`、`WATCH_COOKIES_FROM_BROWSER` 相关参数与配置。
- 仅允许本地视频或音频文件作为输入（若传入 URL 会直接提示拒绝执行）。
- 增加了对纯音频输入（如 `.mp3`, `.wav`, `.m4a` 等）的原生支持：自动检测无画面流时智能跳过抽帧阶段，仅执行语音转写（ASR）。
- 系统前置二进制依赖大幅简化：仅需系统自带或安装 `ffmpeg` 与 `ffprobe`，无需再依赖 `yt-dlp` 或 `uvx`。

### 3. ASR（语音识别）服务重构与 MiniMax ASR 支持
- **移除本地 WhisperX 及其重型依赖**：
  - 移除了 `skills/watch/scripts/local_whisperx.py` 以及 `tests/test_local_whisperx.py`。
  - 移除了安装流程中对 PyTorch、CUDA、HuggingFace 模型以及 `whisperx` 虚拟环境（约 1.5GB+）的预下载和配置逻辑，保持插件环境极轻量。
- **将 `whisper.py` 重构重命名为 `asr.py`**：
  - 核心脚本重构为 [skills/watch/scripts/asr.py](file:///home/aana/projects/agent-video-watch/skills/watch/scripts/asr.py)，测试文件迁移为 [tests/test_asr.py](file:///home/aana/projects/agent-video-watch/tests/test_asr.py)。
  - 不再局限于 Whisper，通用抽象支持语音识别（ASR）各类后端。
- **新增 MiniMax ASR (`asr-1.0`) 支持**：
  - 针对 MiniMax ASR 单次音频不可超过 500 秒的硬性限制，增加了智能时长切片逻辑（默认 300s/切片），由本地切分多段音频后上传转录，并在客户端完成时间戳平移与字幕重组拼接。
  - 支持国内（`api.minimax.cn`，默认）与海外（`api.minimax.io`）双域名区域切换，支持通过 `MINIMAX_REGION`（`cn` 或 `global`）或 `MINIMAX_BASE_URL` / `MINIMAX_ENDPOINT` 自由配置。
  - 针对 MiniMax 国内与海外账户 Key 不互通的问题，增加了 401 专属排障友好提示。
- **保留云端 Whisper 支持**：
  - 保留 Groq（`whisper-large-v3`）与 OpenAI（`whisper-1`）云端转录支持。
- **CLI 参数与环境变量全面适配并保持向后兼容**：
  - 命令行新增 `--asr <minimax|groq|openai>` 与 `--no-asr` 参数，同时保留旧的 `--whisper` 与 `--no-whisper` 作为别名无缝兼容。
  - 环境变量及配置文件新增 `WATCH_ASR_BACKEND`，同时回退读取兼容旧的 `WATCH_WHISPER_BACKEND`。

### 4. 新增字幕持久化保存支持（`--save-subs`）
- **支持将转写结果保存为伴随字幕文件**：
  - 新增 `--save-subs` 命令行参数（并支持通过配置文件/环境变量 `WATCH_SAVE_SUBS=true` 启用）。
  - 当通过 ASR 成功转写后，自动将完整转写内容以标准 WebVTT 格式导出为同名 `<media_name>.vtt` 文件，保存在媒体文件的同级目录下，并在分析报告中明确展示保存路径。
- **自动复用与避免重复消耗 API**：
  - 由于系统原生支持优先检测并加载同名伴随字幕文件（`.vtt`），一旦生成并保存，后续再次分析同一音视频时将自动直接读取本地字幕，不再重复调用云端 ASR，大幅节省 API 费用和分析等待时间。


# H05 ASR 幽灵字幕交接检查点

更新时间：2026-10-29（Asia/Shanghai）

## 当前状态

- 工作树：`H:\Users\jacky\.codex\worktrees\tts-timeout\subtitle_translator`
- 分支：`codex/h05-asr-context`
- 设计提交：`1e854e8 docs: design ASR context and subtitle segmentation`
- 本分支从 `a99fdb198f99cab45f9f1d5fb7d4b9a75eb5ed92` 创建，与 H06 隔离。
- H06/TTS 超时分支为 `codex/tts-timeout-progress`，已推送至 `f096da4`。
- 本轮仅完成设计、隔离实验和调研，没有修改应用实现。

## 编号与目标

- H05：ASR 幻觉/幽灵字幕。
- H06：Qwen3-TTS 超过 180 秒后的超时问题。

H05 目标：让本地 faster-whisper 一次听更长上下文，再依据词级时间戳切成正常长度字幕；不使用固定短音频块决定识别上下文，也不通过短语黑名单静默删除文本。

## 用户已确认的设计

1. 本地 faster-whisper 内部识别上下文固定为 30 秒。
2. 启用词级时间戳。
3. 删除用户界面和持久化配置中的 `ASR_CHUNK_LENGTH`。
4. 新设置名为“字幕分片时间”，内部键拟用 `SUBTITLE_SEGMENT_SECONDS`。
5. 默认字幕分片时间 8 秒。
6. 旧 6/8 秒 `ASR_CHUNK_LENGTH` 迁移为相同数值的字幕分片时间。
7. 结合标点和词级时间戳切分过长结果。
8. 远程 OpenAI 兼容 ASR 暂不改变，不能假定它稳定提供词级时间戳。

设计文档：`docs/superpowers/specs/2026-10-29-asr-context-and-subtitle-segmentation-design.md`

注意：VLC 调研完成并补充设计后，还需用户再次确认，不能直接开始实现。

## 已完成的隔离实验

实验目录：

- `I:\AI Coding\codex\subtitle_translator\.codex-test-runs\ghost-text-20261017`
- `I:\AI Coding\codex\subtitle_translator\.codex-test-runs\isolated-upgrade-20261024`

结论：

- 旧 8 分 30 秒样本：113 行，22 次 `Thanks for watching.`，1 处重叠。
- 30 秒窗口 + 词级时间戳：94 行，0 个该幽灵短语、0 重叠、0 连续重复，并保留真实内容。
- 完整结果：`ghost-text-20261017/window30_words_full.json`。
- `first40-window30_words.json` 移除虚假开场，从 22.18 秒真实语音开始。
- `tail-window30_words.json` 移除孤立尾词 `You`，保留真实语音。
- CTranslate2 4.8.3 与 4.8.1 在两份输入上文本序列相同，升级本身不是解决方案。
- `hallucination_silence_threshold=1/2` 虽修正一个边界样本，但在长样本中改变切分并引入短重复，不应全局启用。
- 现有应用仅标记疑似幽灵行，并不阻止生成。
- 定向测试曾通过 25 项及 9 个子测试。
- 完整基线 pytest 的最终输出没有保留，不能宣称通过；实现前需重跑。

## VLC 调研进度

- VideoLAN 官方新闻在 FOSDEM 2026 展示“AI based live subtitles generator”，更像演示/开发中功能。
- 尚未确认官方 VLC 3 桌面版已正式内置 AI 转录。
- 官方草案 MR：`https://code.videolan.org/videolan/vlc/-/merge_requests/5155`
- 标题：`Draft: stt: add speech-to-text capability with whisper`。
- 第三方插件常见 whisper.cpp/WhisperX、VAD、词级时间戳、关闭 previous text、按停顿/句末切成约 6 秒字幕；只能作为第三方对照，不能描述成 VLC 官方实现。

曾尝试抓取官方 MR 至 `I:\AI Coding\codex\subtitle_translator\.codex-test-runs\vlc-stt-mr5155`，因网络耗时过长已安全中断；未取得 MR ref，也没有遗留 Git 进程。

## 下一步

1. `T-H05-01`：读取 VLC 官方 STT MR 源码或 diff，核对音频缓冲、上下文窗口、Whisper 参数、字幕 cue 生成及 seek/epoch/discontinuity 处理。
2. 将真正相关发现补充到设计文档并提交。
3. 请用户复核最终设计。
4. 确认后用 writing-plans 编写实现计划。
5. 按 TDD 实现迁移、前后端设置和分片逻辑。
6. 运行完整测试、构建和真实样本回归；全部通过后提交。
7. 审查并推送本分支，不擅自合并主线。

## 强制规则

- 每次改动都要新增或更新相关测试。
- 交付前所有测试和验证必须通过。
- 每次改动完成后必须创建 Git commit。
- 保留用户已有改动，不执行破坏性 Git 操作。

## 新对话接手提示词

请使用 `$project-handoff` 接手 Video Translator 的 H05 幽灵字幕工作流。先读取 `H:\Users\jacky\.codex\worktrees\tts-timeout\subtitle_translator\handoff\h05-asr-context\CURRENT.md`，核对分支 `codex/h05-asr-context`、HEAD 和工作树状态，然后从 `T-H05-01` 的 VLC 官方 STT MR 源码调研继续。复用已有隔离样本与依赖对照，不要重做已排除的 CTranslate2 升级和全局 hallucination threshold 路径；完成调研后更新设计、提交并请用户复核。

# H05 ASR 幽灵字幕交接检查点

更新时间：2026-10-30（Asia/Shanghai）

## 当前状态

- 工作树：`H:\Users\jacky\.codex\worktrees\tts-timeout\subtitle_translator`
- 分支：`codex/h05-asr-context`
- 当前 HEAD：`cfbab21889f34e6196ad707c2376485922f2a70a`
- 设计提交：`1e854e8`；VLC 调研提交：`d6f7b2b`；实现计划：`b5ec26c`。
- 本分支从 `a99fdb198f99cab45f9f1d5fb7d4b9a75eb5ed92` 创建，与 H06 隔离。
- H06/TTS 超时分支为 `codex/tts-timeout-progress`，已推送至 `f096da4`。
- Tasks 1–4 已实现、测试、提交并逐项独立审查通过；Task 5 文档、最终验证和真实样本重跑尚未开始。

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

设计已由用户确认。执行计划：`docs/superpowers/plans/2026-10-29-h05-asr-context-and-subtitle-segmentation.md`。

## 实现进度

- Task 1 `23b955d`：配置迁移到版本 2；`ASR_CHUNK_LENGTH` 替换为 `SUBTITLE_SEGMENT_SECONDS`；默认 8、范围 1–30；中英文设置文案完成。全量 990 passed，独立审查通过。
- Task 2 `f142255`：新增共享 30 秒上下文常量和纯词时间戳分片函数；45 项聚焦测试、全量 1035 passed，独立审查通过。
- Task 3 `385ecd7`：进程内本地 ASR 使用 30 秒、`word_timestamps=True` 并调用分片；远程 ASR 保持不变。全量 1038 passed，独立审查通过。
- Task 4 `cfbab21`：worker 独立传入字幕分片时间、使用同一分片函数并序列化结果。全量 1039 passed，独立审查通过。
- 每次全量 Python 测试仅有两条既有依赖弃用警告；Node 基线 32/32、Task 1 后 33/33，Vite 构建成功。
- SDD 恢复账本（Git 忽略）：`.superpowers/sdd/2026-10-29-h05-asr-context-and-subtitle-segmentation/progress.md`。

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

1. 从计划 Task 5 继续：更新 `README.md`、`README.zh-CN.md`、`CHANGELOG.md`，运行目标测试、Python 全量、Node 全量及 Vite 构建并提交。
2. 重跑 8 分 30 秒真实样本；确认 decoded-audio SHA `994db05695f9395f2a45456ec88d298fc90a56c6c76d251af60118311277edab`，统计幽灵短语、重叠、连续重复和真实对白保留情况。
3. 执行 SDD 全分支最终审查；发现问题按最终审查流程修复和复核。
4. 更新本交接记录、推送分支；不要自动合并主线。

## 强制规则

- 每次改动都要新增或更新相关测试。
- 交付前所有测试和验证必须通过。
- 每次改动完成后必须创建 Git commit。
- 保留用户已有改动，不执行破坏性 Git 操作。

## 新对话接手提示词

请使用 `$project-handoff` 接手 Video Translator 的 H05 幽灵字幕工作流。先读取 `H:\Users\jacky\.codex\worktrees\tts-timeout\subtitle_translator\handoff\h05-asr-context\CURRENT.md` 和 SDD 账本，核对分支 `codex/h05-asr-context`、HEAD `cfbab21` 与工作树状态，然后从实现计划 Task 5 继续。Tasks 1–4 已完成并逐项审查通过，不要重做；完成文档、全量验证和真实样本回归后执行全分支最终审查，不要自动合并主线。

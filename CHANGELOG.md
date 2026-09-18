# Changelog

## Unreleased

- Harden the local-only HTTP boundary against cross-origin requests and unexpected hosts.
- Ignore proxy headers in launchers, isolate uploaded/generated content and limit request bodies.
- Keep original media display names while storing uploads under unique names.
- Restrict translation files to application outputs and validate filename components.
- Fix video suffix ranges and reject invalid seek ranges.
- Update vulnerable frontend build dependencies and block old pip/wheel in new public builds.
- Clarify deployment, privacy and update boundaries in the English security policy.

## 0.1.0-alpha.1 - 2026-09-17

- Publish the Windows x64 public core with user-installed upstream dependencies.
- Add the English setup menu, pinned source verification and Qwen transactional installation.
- Preserve the saved local interface language after browser and application restart.
- Include accepted Qwen CUDA/CPU routes, Edge Chinese/English/Japanese/Korean and the RX 5700 XT Qwen Vulkan route.

## 0.1.0-alpha.0 - 2026-08-16

- 公开首个源码测试版，默认翻译路线迁移到 LM Studio。
- 完成 Windows 干净环境安装、80 项后端测试和前端生产构建验证。
- 使用 15 分钟真实视频验证转写、翻译、字幕、配音、取消、重试和资源释放。
- 重构处理面板并补充桌面与移动端浏览器回归。
- 增加公开安装文档、示例配置、MIT License、贡献与安全说明和 GitHub Actions CI。

## 2026-08-11

- 完成项目级单 GPU FIFO 编排与阶段租约。
- 将 faster-whisper 放入独立 worker，阶段结束后可靠释放 CUDA/CTranslate2 显存。
- 接入 Ollama 原生模型预加载、驻留确认和显式卸载。
- 新增完整处理流水线及统一进度、取消、失败和恢复状态。
- 本地 Kokoro 模型资产迁移到仓库级 `models/kokoro/`。
- 韩语自动路由到 Edge TTS，并增加在线服务隐私与断网提示。
- 修复日语 G2P、510 音素越界和配音文本时长适配问题。
- 修复跨语言复用旧 WAV/MP4 的断点续跑问题。
- 默认应用端口由 Windows 保留范围内的 `8080` 调整为 `8769`。
- 中文、日语、韩语等多语言完整流水线通过自动化与 UI 人工验收。

## 2026-06-18

- 实现字幕烧录到视频功能。
- 完善双语字幕预览、编辑、样式控制、导出和移动端体验。
- 修复路径遍历、命令注入、SSRF 等安全问题。

## 2026-06-15

- 为 ASR 模型和 FFmpeg 路径添加浏览按钮。
- 重构设置面板。

## 2026-06-14

- 修复转录文件冲突问题。
- 添加导出文件名对话框。

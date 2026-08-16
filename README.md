<p align="center">
  <img src="app/frontend/public/subtitle-companion.svg" alt="Subtitle Companion" width="96" />
</p>

# Subtitle Translator

本地优先的视频字幕翻译与多语言配音工具。它将视频转写、LLM 翻译、字幕导出、硬字幕渲染和配音视频生成整合到一个浏览器界面中。

> 当前状态：`v0.1.0-alpha.0` 公开测试版。主路线已经通过 Windows 干净环境、自动化测试和 15 分钟真实视频验证，但目前仍需要用户自行准备 Python、Node.js、FFmpeg、LM Studio 和模型，不是免安装软件。

## 功能

- 使用 faster-whisper 本地转写，长视频自动分块
- 使用 LM Studio 本地模型翻译，兼容 Ollama 和 OpenAI-compatible API
- 导出 SRT、VTT、ASS，支持双语字幕和样式调整
- 将字幕烧录到视频
- 使用本地 Kokoro 生成中文、英语、日语等语言配音
- 韩语自动使用 Microsoft Edge 在线 TTS
- 输出配音 WAV 和合成 MP4
- SSE 实时进度、取消、失败阶段重试和任务恢复
- 单 GPU FIFO 调度，在 Whisper、LM Studio 和配音阶段之间释放资源
- 英文/简体中文界面，桌面和移动端布局

## 已验证环境

- Windows 10/11
- Python 3.11
- Node.js 20
- FFmpeg
- NVIDIA GPU 推荐；CPU 模式可以运行，但转写和生成速度会明显降低
- LM Studio 0.4 系列、GGUF llama.cpp Runtime 和本地模型

Linux、macOS、AMD GPU、Intel GPU 和 Docker 尚未作为正式发布路径验证，欢迎测试和提交反馈。

## 安装

### 1. 获取源码

```powershell
git clone https://github.com/<YOUR_ACCOUNT>/subtitle-translator.git
cd subtitle-translator
```

也可以从 GitHub 下载 Source code ZIP 并解压。

### 2. 创建 Python 环境

推荐使用 Miniconda：

```powershell
conda create -n subtitle-translator python=3.11 -y
conda activate subtitle-translator
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows 会安装预编译的 `pyopenjtalk-plus`，不需要 Visual Studio/NMake 编译日语 G2P。

### 3. 构建前端

```powershell
cd app/frontend
npm ci
npm run build
cd ../..
```

### 4. 安装 FFmpeg

确保下面的命令可以运行：

```powershell
ffmpeg -version
ffprobe -version
```

如果不想加入 PATH，可启动应用后在 Settings 中填写 `FFMPEG_PATH`。

### 5. 准备 LM Studio

1. 安装并至少启动一次 [LM Studio](https://lmstudio.ai/)。
2. 确认 CLI 可用：

   ```powershell
   lms --help
   ```

3. 查看或安装 GGUF llama.cpp Runtime：

   ```powershell
   lms runtime ls
   lms runtime get
   lms runtime select
   ```

4. 下载一个适合显存/内存容量的指令模型：

   ```powershell
   lms get --gguf
   lms ls --llm
   ```

应用默认连接 `http://127.0.0.1:1234/v1`，会通过 `lms` 启动本地服务、加载模型，并在翻译结束后卸载。可以在 Settings → Services & diagnostics 中选择模型。

更多命令见 [LM Studio CLI 文档](https://lmstudio.ai/docs/cli) 和 [Runtime 文档](https://lmstudio.ai/docs/cli/runtime/runtime)。

## 启动

确保 Conda 环境已经激活，然后运行：

```powershell
.\start.bat
```

浏览器访问 <http://127.0.0.1:8769/>。

如果端口已被占用：

```powershell
$env:APP_PORT = "9000"
.\start.bat
```

## 首次使用

1. 打开 Settings → Services & diagnostics。
2. 确认 LLM provider 为 `lm_studio`，选择已经下载的模型。
3. 保持 `ASR_MODEL_PATH` 为空时，faster-whisper 会根据 `ASR_MODEL_SIZE` 下载模型；默认是 `base`。
4. 保持 Kokoro 路径为空时，第一次配音会下载模型到 `models/kokoro/`。
5. 上传视频，选择目标语言，等待转写完成。
6. 在 Process 中选择 Subtitles 或 Dubbing，然后从 Export 下载产物。

首次下载 Whisper、Kokoro 或 LM Studio 模型需要联网，并需要足够的磁盘空间。

## 配置

默认配置可以直接启动。需要覆盖配置时：

```powershell
Copy-Item config.example.yaml config.yaml
```

`config.yaml` 已加入 `.gitignore`，因为它可能包含 API Key 和本机路径。也可以在应用设置界面修改配置。

常用字段：

- `LLM_PROVIDER`: `lm_studio`、`ollama` 或 `openai_compatible`
- `LM_STUDIO_MODEL`、`LM_STUDIO_CLI_PATH`、`LM_STUDIO_TTL_SECONDS`
- `ASR_MODEL_SIZE`、`ASR_MODEL_PATH`、`ASR_API_URL`
- `DEVICE_PREFERENCE`: `auto`、`gpu` 或 `cpu`
- `COMPUTE_TYPE`: `auto`、`float16` 或 `int8`
- `TTS_MODE`: `kokoro`、`edge` 或 `speaches`
- `KOKORO_MODEL_PATH`、`KOKORO_VOICES_PATH`
- `FFMPEG_PATH`

完整示例见 [`config.example.yaml`](config.example.yaml)。

## 本地处理与联网说明

| 功能 | 默认行为 |
| --- | --- |
| Whisper 转写 | 模型下载完成后在本机运行 |
| LM Studio 翻译 | 在本机 LM Studio 运行 |
| Kokoro 配音 | 模型下载完成后在本机运行 |
| 韩语 Edge TTS | 文本会发送到 Microsoft 在线语音服务 |
| Speaches / OpenAI-compatible | 数据发送到用户配置的服务地址 |

上传的视频、字幕、配音和任务历史默认保存在本机，不会由本项目主动上传到云服务。使用在线 provider 前，请自行确认其隐私条款。不要将应用绑定到公网地址；默认服务仅监听 `127.0.0.1`。

## 常见问题

### `No LM Runtime found for model format 'gguf'`

运行 `lms runtime ls`。如果没有兼容 Runtime，使用 `lms runtime get` 安装，然后用 `lms runtime select` 选择适合设备的 llama.cpp Runtime。也可以在 LM Studio 中按 `Ctrl+Shift+R` 打开 Runtime 页面。

### 找不到 `lms`

先启动一次 LM Studio，再重新打开 PowerShell 并运行 `lms --help`。也可以在设置中为 `LM_STUDIO_CLI_PATH` 填写完整路径。

### 找不到 FFmpeg

确认 `ffmpeg -version` 和 `ffprobe -version` 可运行，或者在设置中指定 `ffmpeg.exe`。

### 第一次转写或配音很慢

首次运行可能正在下载模型。查看启动窗口日志，并确认网络、磁盘空间和代理设置正常。

### CPU 模式内存不足或速度过慢

尝试较小的 Whisper/LLM 模型，将 `DEVICE_PREFERENCE` 设置为 `cpu`，并将 `COMPUTE_TYPE` 设置为 `int8`。长视频仍可能需要较长时间。

## 已知限制

- 当前没有 Windows 安装器或便携版。
- 模型质量、显存占用和翻译速度取决于用户选择的模型。
- 本地 Kokoro 普通话音色存在模型自身的声调和自然度限制。
- 韩语配音依赖 Edge TTS 网络服务。
- Speaches 语音克隆仍是实验性兼容路径，未纳入主路线验收。
- 同时批量处理多个视频和一次生成多个目标语言仍在规划中。

## 开发与验证

后端静态检查：

```powershell
python -m compileall -q app
```

前端构建：

```powershell
cd app/frontend
npm ci
npm run build
```

GitHub Actions 会在 Windows、Python 3.11 和 Node.js 20 上运行 Python 编译/导入冒烟、生产依赖审计和前端构建。GPU、真实模型和长视频流程仍需人工验证。

开发约定见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## 运行时目录

以下内容不会提交到 Git：

- `config.yaml`
- `app/history.json`
- `models/`
- `app/uploads/`、`app/output/`、`app/temp/`
- 根目录 `uploads/`、`output/`、`temp/`
- `.codex-backups/`、`.codex-test-runs/`

## 参与贡献

请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。安全问题请按 [`SECURITY.md`](SECURITY.md) 私下报告。

## License

本项目使用 [MIT License](LICENSE)。

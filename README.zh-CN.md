<p align="center">
  <img src="app/frontend/public/subtitle-companion.svg" alt="Video Translator" width="96" />
</p>

# Video Translator

[English](README.md)

让闲置显卡派上用场：使用本地 AI 模型翻译视频、生成配音，无需按分钟支付云端翻译或语音 API 费用。

Video Translator 在同一浏览器界面中完成视频转写、字幕翻译、多语言配音、字幕渲染和视频导出。Standard 版按 MIT 许可证免费使用。选择本地 Whisper、本地翻译模型以及 Kokoro 或 Qwen3-TTS，可在自己的机器上完成处理。

本地推理使用自己的 GPU 或 CPU，无需调用付费云端翻译或语音 API；仍需合适的硬件、磁盘空间、电力，以及首次安装模型和依赖时的网络连接。模型和上游软件条款仍适用；可选在线服务可能收费。

> 当前 Windows 便携版：[`v0.1.0-alpha.1`](https://github.com/jacky0jia/video_translator/releases/tag/v0.1.0-alpha.1)。客机安装、设置重启保留已通过验收，当前源码 CI 通过。仍处于 Alpha 阶段，质量和速度取决于模型与硬件。

## 利用现有显卡运行本地模型

可在显卡闲置时手动运行任务。应用按顺序调度转写、翻译和配音，通过单 GPU FIFO 调度及阶段间模型释放减少显存争用；不会自动检测闲置状态，也不保证不影响其它应用。

NVIDIA CUDA 和 CPU 路线已验收，AMD RX 5700 XT 的 Qwen Vulkan 配音路线也已通过。AMD 结果仅适用于 Qwen worker，不代表所有阶段都支持 AMD GPU 加速。模型需适合自己的显存与内存；CPU 可用但较慢。

## Windows 便携主体包

从 [Releases](https://github.com/jacky0jia/video_translator/releases/tag/v0.1.0-alpha.1) 下载完整 Windows x64 ZIP 并解压，双击 `start-portable.bat` 启动、`install-upstream.bat` 打开依赖安装菜单。主体自带 Python、已构建前端、ASR small 和八个参考音色，无需另装 Python/Node.js。

FFmpeg、Kokoro/loader、Qwen 模型及 runtime 由用户从固定上游来源安装，菜单展示条款、进度并校验 SHA-256；Qwen 在设置页继续预检安装，路径自动填写。菜单 5 可校验已安装组件。默认本地翻译路线需另装 LM Studio 和指令模型，也可使用 Ollama 或其它兼容服务。

主说明与脚本默认英文，中文为附加翻译；参见[英文便携安装指导](packaging/UPSTREAM-INSTALL.md)。不要将增加上游下载后的安装目录重新打包并沿用原始主体的发行结论。

## 功能

- 使用 faster-whisper 本地转写，长视频自动分块
- 使用 LM Studio 本地模型翻译，兼容 Ollama 和 OpenAI-compatible API
- 导出 SRT、VTT、ASS，支持译文、双语或原文字幕
- 渲染带样式的硬字幕视频
- 使用本地 Kokoro 为支持的语言生成配音
- 可选择 Microsoft Edge 在线 TTS 生成中、英、日、韩配音
- 使用应用私有 Qwen3-TTS 1.7B Base 运行时和内置音色生成本地配音
- 输出配音 WAV 和合成 MP4
- SSE 实时进度、取消、失败阶段重试和任务恢复
- 单 GPU FIFO 调度，在 Whisper、LM Studio 和配音阶段之间释放模型
- 英语和简体中文界面，支持桌面和移动端布局

## 版本

本仓库包含开源的 **Standard** 版。Standard 提供完整的基础转写、翻译、字幕、配音和导出流程。

未来将单独提供 **Supporter Edition**，计划加入实验性声音克隆、音色设计、可复用角色音色和可安装视觉主题。其私有实现和构建产物不会存放在本公开仓库。目前还没有可下载的 Supporter Edition。

## 已验证环境

- Windows 10/11
- Python 3.11
- Node.js 20
- FFmpeg
- 推荐 NVIDIA GPU；CPU 模式可以运行，但转写和生成速度明显较慢
- LM Studio 0.4 系列、GGUF llama.cpp Runtime 和本地指令模型

AMD RX 5700 XT 已通过 Windows 上的 Qwen Vulkan 配音、安装和回滚验证。其它 AMD/Intel 显卡以及 Linux、macOS、Docker 尚未成为已验收发布路径；不据此宣称 Whisper 或翻译服务的通用 AMD GPU 加速。

## 安装

### 1. 获取源码

```powershell
git clone https://github.com/jacky0jia/video_translator.git
cd video_translator
```

也可以从 GitHub 下载 Source code ZIP 并解压。

### 2. 创建 Python 环境

推荐使用 Miniconda：

```powershell
conda create -n video-translator python=3.11 -y
conda activate video-translator
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows 使用预编译的 `pyopenjtalk-plus`，日语 G2P 不需要 Visual Studio 或 NMake。

### 3. 构建前端

```powershell
cd app/frontend
npm ci
npm run build
cd ../..
```

### 4. 安装 FFmpeg

确认以下命令可以运行：

```powershell
ffmpeg -version
ffprobe -version
```

如果 FFmpeg 不在 `PATH` 中，可以在应用设置里配置 `FFMPEG_PATH`。

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

4. 下载适合显存或内存容量的指令模型：

   ```powershell
   lms get --gguf
   lms ls --llm
   ```

Video Translator 默认连接 `http://127.0.0.1:1234/v1`。应用可以通过 `lms` 启动本地 LM Studio 服务、加载所选模型，并在翻译结束后卸载。请在 **Settings → Services & diagnostics** 中选择模型。

更多信息见 [LM Studio CLI 文档](https://lmstudio.ai/docs/cli) 和 [Runtime 文档](https://lmstudio.ai/docs/cli/runtime/runtime)。

### Qwen3-TTS 状态

本地 Qwen 路线只使用 `Qwen3-TTS-12Hz-1.7B-Base-GGUF`。GGUF 可以复用 LM Studio 已下载的文件，语音合成则由应用私有、固定哈希的 `llama-tts` worker 执行。应用自带八个来自 LibriVox 公版内容的参考样本，并把它们表现为固定内置音色；Standard 不会暴露样本路径、上传、替换或托管式声音克隆功能。

配置经过审查的私有 `llama-tts` 运行包后即可使用 Qwen 配音。它不依赖 LM Studio 语音 API，也不需要 Python、Torch 或官方 `qwen-tts` Python 包。Qwen 模式支持的十种语言（包括韩语）全部在本地 worker 中处理。

## 启动应用

激活 Conda 环境后运行：

```powershell
.\start.bat
```

浏览器访问 <http://127.0.0.1:8769/>。

如需使用其他端口：

```powershell
$env:APP_PORT = "9000"
.\start.bat
```

## 首次使用

1. 打开 **Settings → Services & diagnostics**。
2. 保持 `lm_studio` 为 LLM provider，并选择已经下载的模型。
3. 便携主体已配置 ASR small；源码安装时，`ASR_MODEL_PATH` 为空会使用 `ASR_MODEL_SIZE`，默认 `base`，可能需要下载。
4. 便携主体需先在安装菜单准备 FFmpeg 和所选 Kokoro/Qwen 依赖；源码安装时，Kokoro 空路径可能在首次配音下载模型。
5. 上传视频并选择目标语言。
6. 转写完成后，在 Process 中选择 **Subtitles** 或 **Dubbing**，然后从 Export 下载产物。

第一次下载 Whisper、Kokoro 或 LM Studio 模型需要联网和足够的磁盘空间。

## 配置

默认设置可以启动应用。如需覆盖：

```powershell
Copy-Item config.example.yaml config.yaml
```

`config.yaml` 已被 Git 忽略，因为它可能包含 API Key 和本机路径。也可以在应用设置中修改配置。

设置窗口由后端 Settings Schema 生成。默认先显示基础字段；运行时、分块、采样率和本地模型路径等选项位于 **显示高级设置**。切换 Provider 只改变字段可见性，不会清除被隐藏的 Provider 配置。API Key 会保持掩码，除非用户明确清空。

常用字段：

- `LLM_PROVIDER`：`lm_studio`、`ollama` 或 `openai_compatible`
- `LM_STUDIO_MODEL`、`LM_STUDIO_CLI_PATH`、`LM_STUDIO_TTL_SECONDS`
- `ASR_MODEL_SIZE`、`ASR_MODEL_PATH`、`ASR_API_URL`
- `DEVICE_PREFERENCE`：`auto`、`gpu` 或 `cpu`
- `COMPUTE_TYPE`：`auto`、`float16` 或 `int8`
- `TTS_MODE`：标准设置界面为 `kokoro`、`edge`、`qwen`；`speaches` 仅为旧兼容路线
- `QWEN_AUTO_CPU_FALLBACK`：GPU worker 不可用时允许回退 CPU
- `UI_LANGUAGE`：本机保存的界面语言（`en` 或 `zh`）
- `DUB_SAMPLE_RATE`：标准化 mono PCM 输出采样率（默认 `22050`）
- `KOKORO_MODEL_PATH`、`KOKORO_VOICES_PATH`
- `FFMPEG_PATH`

完整示例见 [`config.example.yaml`](config.example.yaml)。

## 本地处理和联网说明

| 功能 | 默认行为 |
| --- | --- |
| Whisper 转写 | 模型下载后在本机运行 |
| LM Studio 翻译 | 在用户本机的 LM Studio 中运行 |
| Qwen3-TTS 配音 | 使用经过审查的私有 `llama-tts` 运行包和固定哈希 GGUF 的可选本地 Provider |
| Kokoro 配音 | 模型下载后在本机运行 |
| Edge TTS（中、英、日、韩） | 将字幕文本发送到 Microsoft 在线语音服务 |
| Speaches / OpenAI-compatible | 将数据发送到用户配置的服务地址 |

上传视频、字幕、配音产物和任务历史默认保存在本机。Video Translator 不会主动把它们上传到项目运营的云服务。启用在线 provider 前，请检查对应服务的隐私条款。

不要把开发服务器暴露到公网；默认只监听 `127.0.0.1`。

## 常见问题

### `No LM Runtime found for model format 'gguf'`

运行 `lms runtime ls`。如果没有兼容 Runtime，使用 `lms runtime get` 安装，再用 `lms runtime select` 选择 GGUF llama.cpp Runtime。也可以在 LM Studio 中按 `Ctrl+Shift+R` 打开 Runtime 页面。

### 找不到 `lms`

先启动一次 LM Studio，重新打开 PowerShell，然后运行 `lms --help`。也可以在设置中填写完整的 `LM_STUDIO_CLI_PATH`。

### 找不到 FFmpeg

确认 `ffmpeg -version` 和 `ffprobe -version` 可以运行，或者在设置中选择 `ffmpeg.exe`。

### 第一次转写或配音很慢

模型可能仍在下载。检查启动窗口，并确认网络、磁盘空间和代理设置正常。

### CPU 模式过慢或内存不足

选择更小的 Whisper 和 LLM 模型，把 `DEVICE_PREFERENCE` 设为 `cpu`，并使用 `COMPUTE_TYPE=int8`。长视频仍可能需要较长时间。

## 已知限制

- 已提供 Windows x64 便携 ZIP，尚无 MSI/EXE 安装器。
- 质量、内存占用和翻译速度取决于选择的模型。
- Kokoro 普通话音色存在模型自身的声调和自然度限制。
- Edge 配音需要联网，并会将字幕文本发送到 Microsoft 在线语音服务。
- Qwen3-TTS 1.7B Base 可以复用 LM Studio 下载的 GGUF；合成由已配置的私有 `llama-tts` worker 执行。
- Speaches 声音克隆仍是实验性兼容路径，不属于已验收主流程。
- 多视频批处理以及一次转写生成多个目标语言仍在规划中。

## 开发与验证

后端编译：

```powershell
python -m compileall -q app
```

前端构建：

```powershell
cd app/frontend
npm ci
npm run build
```

GitHub Actions 在 Windows、Python 3.11 和 Node.js 20 上运行 Python 编译/导入冒烟、生产依赖审计和前端构建。GPU、真实模型和长视频仍需人工验证。

贡献说明见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## 运行时目录

以下本地数据不会提交：

- `config.yaml`
- `app/history.json`
- `models/`
- `app/uploads/`、`app/output/` 和 `app/temp/`
- 根目录 `uploads/`、`output/` 和 `temp/`
- `.codex-backups/` 和 `.codex-test-runs/`

## 参与贡献与安全

贡献前请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。安全问题请按 [`SECURITY.md`](SECURITY.md) 私下报告。

## 许可证与第三方软件

Video Translator 源代码使用 [MIT License](LICENSE)。依赖、模型和外部工具保留各自的许可证与条款。重新分发二进制包前，请阅读[第三方声明](THIRD-PARTY-NOTICES.zh-CN.md)。

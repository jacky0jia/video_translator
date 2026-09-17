# Video Translator Windows x64 公开发行版

本包包含应用、Python、前端、ASR small 模型及八个参考音色。FFmpeg、Kokoro 模型与音色、espeakng-loader 和 Qwen 模型/runtime 由你从上游另行安装。首次使用需要联网；应用不会在启动时自动安装这些组件。

完整解压后双击 `start-portable.bat`，打开 http://127.0.0.1:8769/ 。翻译需要单独安装 LM Studio，并在设置页选择翻译模型。配置、上传、输出和历史保存在本目录；移动目录前先停止应用。用 Ctrl+C 停止；端口冲突时设置 `APP_PORT`。

## 菜单式安装与校验（推荐）

双击 `install-upstream.bat`，或在本目录运行 `runtime\python.exe -B install_upstream.py`，按菜单选择组件。安装前展示固定上游来源与许可，输入 `y` 后开始；下载显示已传输 MiB，已知总长度时显示粗略百分比，然后校验 SHA-256。已有匹配文件直接复用。

FFmpeg、Kokoro 选项会安装并检查文件哈希及应用识别。Qwen 选项下载模型和所选 runtime，下载完成后进入应用设置页：脚本的固定下载路径自动填写，可手动修改；选择设备并执行预检、安装。随后回菜单选择“检查已安装组件”，可输入 `ffmpeg kokoro` 只检查这两项，或回车检查全部。目录外报告默认写为 `upstream-install-report.json`。

Qwen 的“下载完成”与“安装完成”是两个步骤；目录内有 GGUF/ZIP 不代表已完成 runtime 安装。下列命令行操作继续可用。

## 安装 FFmpeg（转写音频提取、视频烧录和配音合成必需）

在本目录打开 PowerShell，先查看固定来源和许可：

```powershell
.\runtime\python.exe -B .\install_upstream.py ffmpeg
```

阅读 [Gyan 发行页](https://github.com/GyanD/codexffmpeg/releases/tag/2026-01-05-git-2892815c45) 和 [FFmpeg 许可说明](https://www.ffmpeg.org/legal.html)，确认后安装：

```powershell
.\runtime\python.exe -B .\install_upstream.py ffmpeg --accept-upstream-terms
```

脚本直接从上游下载固定 ZIP、校验 SHA-256，将两个 EXE 与上游 LICENSE/README 放入 `app/ffmpeg/`。也可自行下载并放入此目录，或在设置页填写已安装 FFmpeg 的完整路径（同目录需要 ffprobe）。不要用我们的发行包转存这些下载文件后重新发布。

## 可选 Kokoro

阅读 [模型与音色发行页](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0)、[基础模型卡与署名](https://huggingface.co/hexgrad/Kokoro-82M) 和 [loader 包页面](https://pypi.org/project/espeakng-loader/0.2.4/)。loader 自身尚未找到明确许可证声明，bundled eSpeak NG 的 GPL 是另一部分的条款；由用户下载不会产生新的授权。

```powershell
.\runtime\python.exe -B .\install_upstream.py kokoro
.\runtime\python.exe -B .\install_upstream.py kokoro --accept-upstream-terms
```

脚本直接下载已验收的 ONNX、54 音色文件及 Windows x64 loader wheel，逐个校验哈希，并用本包 Python 安装 loader（不解析或升级其他依赖）。重新启动后在设置页显式选择 Kokoro；没有安装时不会切换到其他 Provider。

## 可选 Qwen（CUDA/CPU 或 Vulkan）

阅读 [GGUF 来源](https://huggingface.co/ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF/tree/ca27d74bc954b73dadab5b71ca265d87fc861a7c)、[基础模型卡](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base) 和 [llama.cpp b10792](https://github.com/ggml-org/llama.cpp/releases/tag/b10792) 的适用条款，再运行所需下载：

```powershell
.\runtime\python.exe -B .\install_upstream.py qwen-model --accept-upstream-terms
# NVIDIA 或 CPU 路线（CPU 也使用这套固定运行包）
.\runtime\python.exe -B .\install_upstream.py qwen-cuda --accept-upstream-terms
# 已验收 RX 5700 XT 的 Vulkan 路线，按需选择
.\runtime\python.exe -B .\install_upstream.py qwen-vulkan --accept-upstream-terms
```

在应用设置页 Qwen 安装区域，固定下载位置的 llama ZIP、CUDA ZIP（Vulkan 不需要）和 `upstream-downloads/qwen-model` 路径自动填写；使用其他位置时可手动修改。执行预检与安装，再选择 Qwen。下载脚本不替代现有事务安装、升级回滚、文件白名单或哈希检查。也可以使用已有相同哈希的 GGUF 和上游压缩包。

下载失败时可稍后重试；哈希不匹配时脚本停止，不采用未校验文件。已有不同版本文件不会被覆盖，先将它们移走。首次使用下载后的组件前应重新启动应用。

HTTPS 下载同时使用系统信任证书和随包 certifi CA 集合，保持证书链与主机名校验。如果仍提示 `CERTIFICATE_VERIFY_FAILED`，请检查虚机日期时间；使用代理时请让管理员检查代理证书链与系统信任配置，并保留错误原文。不要关闭 SSL 校验。

## 发行范围

安装后，可在应用目录运行以下检查（报告写到目录外，不进行下载或合成）：

```powershell
.\runtime\python.exe -B .\verify_upstream_install.py --report ..\upstream-install-report.json
# 只安装了部分组件时，明确选择检查范围
.\runtime\python.exe -B .\verify_upstream_install.py --components ffmpeg qwen --report ..\upstream-install-report.json
```

检查已验收版本的 FFmpeg、模型、Qwen runtime 和 loader 文件哈希，再用本包 Python 确认应用识别所选组件。Kokoro 检查需要保留脚本下载的 loader wheel，作为安装文件的哈希依据。使用其他版本或自定义外部目录可能不能通过这份固定版本检查，不能把报告当作所有版本的验收结果。此报告不自动证明机器是干净 Windows，也不替代下载网络与安装过程的实际观察。

随包组件的许可证清单位于 `licenses/THIRD-PARTY-MANIFEST.json`。外部下载固定 URL、版本和哈希位于 `upstream-dependencies.json`。缺少完整转换溯源不等于模型禁止使用；它们仍适用上游许可和使用条件。下载后的目录不再是原始公开发行包，不能沿用原包的零 blocker 声明重新分发。Edge 在线服务同样适用其服务与隐私条款。

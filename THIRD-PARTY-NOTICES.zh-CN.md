# 第三方声明

[English](THIRD-PARTY-NOTICES.md)

本文记录 Video Translator 在 Phase 0 进行的许可证检查。它是审计摘要，不构成法律意见，也不能代替二进制分发时必须附带的完整许可证文本。

## 应用许可证

公开主体版将 FFmpeg、Kokoro 模型/音色、espeakng-loader 和 Qwen 模型/runtime
交由用户从上游安装，原始公开包不包含这些文件。实际随包依赖以
`licenses/THIRD-PARTY-MANIFEST.json` 为准；下载文件仍适用上游条款。
固定来源、哈希和安装步骤随主体包提供。

Video Translator 原创源代码使用 [MIT License](LICENSE)。第三方软件、模型、字体、音色和在线服务继续适用各自的许可证与服务条款。

## Python 直接依赖

| 依赖 | 本次检查识别的许可证 | 上游项目 |
| --- | --- | --- |
| FastAPI | MIT | [fastapi/fastapi](https://github.com/fastapi/fastapi) |
| Uvicorn | BSD-3-Clause | [encode/uvicorn](https://github.com/encode/uvicorn) |
| faster-whisper | MIT | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| Pydantic / pydantic-settings | MIT | [pydantic/pydantic](https://github.com/pydantic/pydantic) |
| PyYAML | MIT | [yaml/pyyaml](https://github.com/yaml/pyyaml) |
| python-dotenv | BSD-3-Clause | [theskumar/python-dotenv](https://github.com/theskumar/python-dotenv) |
| python-multipart | Apache-2.0 | [Kludex/python-multipart](https://github.com/Kludex/python-multipart) |
| HTTPX | BSD-3-Clause | [encode/httpx](https://github.com/encode/httpx) |
| Requests | Apache-2.0 | [psf/requests](https://github.com/psf/requests) |
| SoundFile | BSD-3-Clause | [bastibe/python-soundfile](https://github.com/bastibe/python-soundfile) |
| Misaki | Apache-2.0 | [hexgrad/misaki](https://github.com/hexgrad/misaki) |
| kokoro-onnx | MIT | [thewh1teagle/kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) |
| pyopenjtalk-plus | Python 包装层为 MIT；其中 Open JTalk/HTS 组件和音色数据另有声明 | [tsukumijima/pyopenjtalk-plus](https://github.com/tsukumijima/pyopenjtalk-plus) |
| edge-tts | 大部分文件为 LGPLv3；SRT composer 为 MIT | [rany2/edge-tts 许可证](https://github.com/rany2/edge-tts/blob/master/LICENSE) |

依赖树还包含间接依赖。发布二进制时必须保留安装包内各依赖自带的许可证，并根据实际锁定版本生成完整的间接依赖清单；上面的直接依赖表本身不足以完成二进制分发合规。

## 前端依赖

Phase 0 检查的前端直接依赖 React、React DOM、Vite、Vite React 插件、Tailwind CSS、PostCSS 和 Autoprefixer 均使用 MIT。每次发布仍需按实际 lockfile 盘点其间接依赖。

## 语音模型

- `kokoro-onnx` 上游说明代码为 MIT，Kokoro 模型为 Apache-2.0。下载或重新分发模型时，应在模型文件旁保留许可证和署名信息。
- Qwen3-TTS 是使用 `Qwen3-TTS-12Hz-1.7B-Base-GGUF` 的可选本地 Provider。模型可由用户通过 LM Studio 下载，并作为独立的 Apache-2.0 资产管理；重新分发前必须保留准确的模型卡、许可证、NOTICE、版本和哈希。
- Qwen 合成使用应用私有的 llama.cpp `llama-tts` 运行时。llama.cpp 使用 MIT 许可证，已审查运行包同时包含 LLVM OpenMP 许可证。NVIDIA 构建还包含受 NVIDIA CUDA Toolkit EULA 及其再分发条款约束的 CUDA runtime 和 cuBLAS 文件。准确版本、来源压缩包哈希、文件集合及逐文件哈希记录在 `packaging/llama-tts/`。
- 内置 Qwen 音色参考来自 LibriVox 公版录音。来源网址、朗读者署名、处理说明、适用地域信息和哈希记录在应用音色目录的 `SOURCES.json` 中。分发包含这些音色资源的版本时，必须一并保留该元数据。
- 音色文件和数据集可能拥有不同于推理代码的条款，不能因为代码可以使用就默认所有音色和模型资产都适用同一许可证。

## FFmpeg

2026-08-17 检查的本地 Git 忽略二进制报告如下：

```text
ffmpeg version 2026-01-05-git-2892815c45-essentials_build-www.gyan.dev
configuration: --enable-gpl --enable-version3 ...
```

这是启用 GPLv3 的构建，目前没有被本仓库跟踪。如果未来安装器或便携包分发这个二进制，发布流程必须提供适用的 GPL 文本、版权声明、构建信息及该分发所要求的对应源码获取方式。另一种选择是改用经过重新审查、所启用组件符合预期分发方式的 FFmpeg 构建。

应用把 FFmpeg 作为独立进程调用，并不会消除重新分发 FFmpeg 二进制本身的义务。实现打包时必须再次审查最终使用的精确构建。

## 外部应用和在线服务

- LM Studio 是由用户单独安装的外部应用，本仓库不重新分发；它适用自己的条款。
- `edge-tts` 会连接 Microsoft 在线语音服务。开源客户端许可证不等于获得在线服务使用权，也不保证具体用途符合 Microsoft 当时的服务条款。商业分发前需要重新检查，并保留应用内的联网与隐私提示。
- Ollama、OpenAI-compatible 服务、模型提供商和用户下载的模型继续适用各自的许可证、使用规则和隐私条款。

## 二进制发布检查表

发布安装器或便携包前：

1. 锁定 Python、npm、模型、字体、音色和二进制的精确版本。
2. 生成完整的间接依赖软件物料或许可证清单。
3. 附带所需的 MIT、BSD、Apache、LGPL、GPL 及组件专有许可证文本。
4. 保留上游版权和 NOTICE 文件。
5. 记录下载地址、模型版本和校验和。
6. 重新审查最终 FFmpeg 构建及其启用的编码组件。
7. 重新检查所有默认在线服务的当前条款。
8. 分别检查 Standard 和 Supporter Edition 的发布产物。

最后检查日期：2026-08-17。

# Third-Party Notices

[简体中文](THIRD-PARTY-NOTICES.zh-CN.md)

This document records the Phase 0 license review for Video Translator. It is an audit summary, not legal advice and not a replacement for the complete license texts that must accompany a binary distribution.

## Application license

The public core edition delegates FFmpeg, Kokoro model/voices, espeakng-loader
and the Qwen model/runtime bundle to user downloads from upstream. These files
are absent from that pristine release; the actual shipped dependency licenses
are listed in `licenses/THIRD-PARTY-MANIFEST.json`. Upstream downloads retain
their own terms. Sources, fixed hashes and installation steps accompany the core.

Video Translator's original source code is licensed under the [MIT License](LICENSE). Third-party software, models, fonts, voices, and online services retain their own licenses and terms.

## Direct Python dependencies

| Dependency | License identified during review | Upstream |
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
| pyopenjtalk-plus | MIT wrapper; bundled Open JTalk/HTS components and voice data have additional notices | [tsukumijima/pyopenjtalk-plus](https://github.com/tsukumijima/pyopenjtalk-plus) |
| edge-tts | LGPLv3 for most files; its SRT composer is MIT | [rany2/edge-tts license](https://github.com/rany2/edge-tts/blob/master/LICENSE) |

The dependency graph also contains transitive packages. A release build must preserve the license files shipped by installed packages and produce a version-pinned transitive manifest; this direct-dependency table is not sufficient by itself for binary redistribution.

## Frontend dependencies

The direct frontend packages reviewed in Phase 0—React, React DOM, Vite, the Vite React plugin, Tailwind CSS, PostCSS, and Autoprefixer—are MIT licensed. Their transitive packages must still be inventoried from the exact lockfile used for each release.

## Speech models

- The `kokoro-onnx` upstream project identifies its code as MIT and the Kokoro model as Apache-2.0. Preserve the model license and attribution next to downloaded or redistributed model files.
- Qwen3-TTS is an optional local Provider using `Qwen3-TTS-12Hz-1.7B-Base-GGUF`. The model may be user-downloaded through LM Studio and remains a separate Apache-2.0 artifact. Preserve the exact model card, license, NOTICE, revision, and hashes before redistribution.
- Qwen synthesis uses an application-private llama.cpp `llama-tts` runtime. llama.cpp is MIT licensed; the reviewed package includes its license and the LLVM OpenMP license. The NVIDIA build also contains CUDA runtime and cuBLAS redistribution files subject to the NVIDIA CUDA Toolkit EULA. Exact versions, source archive hashes, included files, and per-file hashes are recorded under `packaging/llama-tts/`.
- Built-in Qwen voice references are derived from LibriVox public-domain recordings. Source URLs, reader attribution, processing notes, jurisdiction information, and hashes are recorded in the application voice catalog's `SOURCES.json`. Preserve that metadata with any distribution containing the voice assets.
- Voice files and datasets can have terms separate from the inference code. Do not assume that a code license automatically covers every voice or model asset.

## FFmpeg

The local Git-ignored binary inspected on 2026-08-17 reports:

```text
ffmpeg version 2026-01-05-git-2892815c45-essentials_build-www.gyan.dev
configuration: --enable-gpl --enable-version3 ...
```

This is a GPLv3-enabled build. It is not tracked by this repository. If a future installer or portable archive distributes this binary, the release process must include the applicable GPL text, copyright notices, build information, and corresponding-source availability required by that distribution. An alternative is to select and review a different FFmpeg build whose enabled components match the desired distribution model.

Invoking FFmpeg as a separate executable does not remove the obligations attached to redistributing the FFmpeg binary itself. The exact build must be audited again when packaging is implemented.

## External applications and online services

- LM Studio is an external application installed separately by the user and is not distributed by this repository. Its own terms apply.
- `edge-tts` connects to Microsoft's online speech service. The open-source client license does not grant rights to the service or guarantee that a particular use complies with Microsoft's current service terms. Review those terms before commercial distribution and keep the in-app network/privacy notice.
- Ollama, OpenAI-compatible endpoints, model providers, and downloaded models remain subject to their own licenses, acceptable-use rules, and privacy terms.

## Binary release checklist

Before publishing an installer or portable archive:

1. Freeze exact Python, npm, model, font, voice, and binary versions.
2. Generate a complete transitive software-bill-of-materials or license manifest.
3. Include the required MIT, BSD, Apache, LGPL, GPL, and component-specific license texts.
4. Preserve upstream copyright and NOTICE files.
5. Record download URLs, model revisions, and checksums.
6. Re-audit the exact FFmpeg build and any codecs it enables.
7. Review the current terms for every online service used by default.
8. Repeat the review for both Standard and Supporter Edition artifacts.

Last reviewed: 2026-08-17.

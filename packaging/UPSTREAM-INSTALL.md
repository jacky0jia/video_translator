# Video Translator for Windows x64

This public core includes the application, Python, frontend, ASR small model and eight reference voices. Install FFmpeg, Kokoro models/voices, espeakng-loader and Qwen models/runtime separately from upstream. Startup does not download or install them automatically.

Extract the whole package, then double-click `start-portable.bat` and open http://127.0.0.1:8769/ . Translation requires a separate LM Studio installation and a translation model selected in Settings. Configuration, uploads, outputs and history stay in this directory. Stop the application before moving it. Press Ctrl+C to stop; set `APP_PORT` if the port is occupied.

## Setup menu (recommended)

Double-click `install-upstream.bat`, or run `runtime\python.exe -B install_upstream.py` in this directory.

1. Install and verify FFmpeg.
2. Install and verify Kokoro.
3. Download Qwen models and CUDA/CPU runtime.
4. Download Qwen models and Vulkan runtime.
5. Verify installed components.
0. Exit.

Review the upstream sources and terms displayed before installation; type `y` to proceed. Downloads display transferred MiB and an approximate percentage when the total size is known, followed by SHA-256 verification. Matching cached files are reused. Different existing files are not overwritten.

FFmpeg and Kokoro options install and verify files and application recognition. Qwen options download and verify installation inputs; **downloaded files are not yet an installed Qwen runtime**. Open Settings, where the three fixed download paths are filled automatically. Select a device, preflight and install. Paths remain editable for files stored elsewhere. Use CPU in a VM without supported GPU access. Return to menu 5 to verify the installed runtime.

Menu 5 accepts `ffmpeg kokoro qwen`, a subset such as `ffmpeg kokoro`, or Enter for all. The report is written outside the application directory as `upstream-install-report.json`.

## Command-line setup

Commands without `--accept-upstream-terms` display sources and terms without downloading.

```powershell
.\runtime\python.exe -B .\install_upstream.py ffmpeg --accept-upstream-terms
.\runtime\python.exe -B .\install_upstream.py kokoro --accept-upstream-terms
.\runtime\python.exe -B .\install_upstream.py qwen-model --accept-upstream-terms
# CUDA or CPU: both use the same pinned runtime archives.
.\runtime\python.exe -B .\install_upstream.py qwen-cuda --accept-upstream-terms
# Optional Vulkan runtime for the accepted RX 5700 XT configuration.
.\runtime\python.exe -B .\install_upstream.py qwen-vulkan --accept-upstream-terms
```

FFmpeg is needed for audio extraction, video burn-in and dubbing assembly. Review the [Gyan release](https://github.com/GyanD/codexffmpeg/releases/tag/2026-01-05-git-2892815c45) and [FFmpeg licensing information](https://www.ffmpeg.org/legal.html). The script keeps FFmpeg/ffprobe, LICENSE and README in `app/ffmpeg/`. You may use an existing installation by setting its full FFmpeg path in Settings; ffprobe must be in the same directory.

For Kokoro, review the [model/voices release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0), [base model card and attribution](https://huggingface.co/hexgrad/Kokoro-82M) and [loader package](https://pypi.org/project/espeakng-loader/0.2.4/). No explicit loader-wrapper license has been found; bundled eSpeak NG has separate GPL terms. User download does not create a new license grant. The script installs the pinned wheel without upgrading other dependencies. Select Kokoro explicitly after restarting; missing components do not cause a provider switch.

For Qwen, review the [fixed GGUF repository](https://huggingface.co/ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF/tree/ca27d74bc954b73dadab5b71ca265d87fc861a7c), [base model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base) and [llama.cpp b10792 release](https://github.com/ggml-org/llama.cpp/releases/tag/b10792). Downloads go to `upstream-downloads/qwen-model`, `qwen-cuda` or `qwen-vulkan`. Settings uses the existing transactional installer, preflight, file allowlist, hashes and rollback support. Keep the two GGUF files together in the model directory. Matching existing files can be reused.

Restart the application after installation. HTTPS uses system trust plus the bundled certifi CA collection, with certificate-chain and hostname checks enabled. If `CERTIFICATE_VERIFY_FAILED` persists, check the guest date/time and ask your administrator to check any proxy certificate chain and trust configuration. Do not disable SSL verification. Hash failures stop installation; move conflicting existing files aside before retrying.

## Verify installed components and distribution scope

```powershell
.\runtime\python.exe -B .\verify_upstream_install.py --report ..\upstream-install-report.json
.\runtime\python.exe -B .\verify_upstream_install.py --components ffmpeg kokoro --report ..\ffmpeg-kokoro-report.json
```

The verifier checks pinned installed files and application recognition, without downloading or synthesizing audio. Keep the downloaded loader wheel as the hash reference. Other versions or external directories may not pass these fixed-version checks. A report does not automatically establish a clean OS, online downloads or functional/hardware acceptance.

Shipped license attachments are listed in `licenses/THIRD-PARTY-MANIFEST.json`; fixed upstream URLs, versions and hashes are in `upstream-dependencies.json`. Incomplete conversion provenance is not a statement that a model prohibits use; upstream terms still apply. A directory augmented with user downloads is no longer the original public core and cannot reuse its zero-blocker claim for redistribution. Do not repackage downloaded dependencies into a public release. Edge also has applicable service and privacy terms.

A Chinese translation is included as `README-PORTABLE.zh-CN.md`.

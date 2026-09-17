<p align="center">
  <img src="app/frontend/public/subtitle-companion.svg" alt="Video Translator" width="96" />
</p>

# Video Translator

[简体中文](README.zh-CN.md)

A local-first application for video transcription, subtitle translation, multilingual dubbing, subtitle rendering, and video export from one browser interface.

> Current status: `v0.1.0-alpha.0`. The primary Windows workflow has passed clean-environment checks, automated tests, and a 15-minute real-video validation. The source setup below requires Python, Node.js, FFmpeg, LM Studio and models. A separately prepared Windows public core includes its own Python and built frontend; follow its bundled setup guide when using that package.

## Portable Windows core

If you received a public core package, start with its English `README.md` or `README-PORTABLE.md`. Double-click `start-portable.bat` to start the application and `install-upstream.bat` for the dependency setup menu. You do not need to install Python or Node.js separately for this package.

FFmpeg, Kokoro models/voices, espeakng-loader and Qwen models/runtime are installed directly from pinned upstream sources. The menu displays upstream terms, download progress and SHA-256 checks. Qwen installation continues in Settings with the download paths filled automatically. Use menu 5 to check installed components. Translation still requires a separate LM Studio installation.

See the [English portable setup guide](packaging/UPSTREAM-INSTALL.md). English is the primary language for setup scripts, README files and release instructions; `.zh-CN.md` files are optional translations. Shipped third-party licenses and attribution retain their upstream text. A directory augmented with downloaded dependencies is not the original public core and must not reuse its distribution readiness claim.

## Features

- Local transcription with faster-whisper and automatic long-video chunking
- Local LLM translation through LM Studio, with optional Ollama and OpenAI-compatible endpoints
- SRT, VTT, and ASS export with translated, bilingual, or original subtitles
- Styled hard-subtitle rendering
- Local Kokoro dubbing for supported languages
- Optional Microsoft Edge online TTS for Chinese, English, Japanese, and Korean
- Private local Qwen3-TTS 1.7B Base runtime with built-in voices
- Dubbed WAV and muxed MP4 output
- Live SSE progress, cancellation, failed-stage retry, and task recovery
- Single-GPU FIFO orchestration with model release between Whisper, LM Studio, and dubbing stages
- English and Simplified Chinese interfaces with desktop and mobile layouts

## Editions

This repository contains the open-source **Standard** edition. Standard includes the complete base transcription, translation, subtitle, dubbing, and export workflow.

A separate **Supporter Edition** is planned for experimental voice cloning, voice design, reusable character voices, and installable visual themes. Its private implementation and builds will not be stored in this public repository. No Supporter Edition download is available yet.

## Verified environment

- Windows 10/11
- Python 3.11
- Node.js 20
- FFmpeg
- NVIDIA GPU recommended; CPU mode works but transcription and generation are much slower
- LM Studio 0.4 series, a GGUF llama.cpp Runtime, and a local instruction model

Linux, macOS, AMD GPU, Intel GPU, and Docker are not accepted release paths yet. Testing and feedback are welcome, but these platforms are not currently supported.

## Installation

### 1. Get the source

```powershell
git clone https://github.com/jacky0jia/video_translator.git
cd video_translator
```

You can also download and extract the source ZIP from GitHub.

### 2. Create the Python environment

Miniconda is recommended:

```powershell
conda create -n video-translator python=3.11 -y
conda activate video-translator
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows, the prebuilt `pyopenjtalk-plus` package is used so Japanese G2P does not require Visual Studio or NMake.

### 3. Build the frontend

```powershell
cd app/frontend
npm ci
npm run build
cd ../..
```

### 4. Install FFmpeg

Make sure both commands are available:

```powershell
ffmpeg -version
ffprobe -version
```

If FFmpeg is not on `PATH`, set `FFMPEG_PATH` from the application settings.

### 5. Prepare LM Studio

1. Install and launch [LM Studio](https://lmstudio.ai/) at least once.
2. Confirm that its CLI is available:

   ```powershell
   lms --help
   ```

3. Inspect or install a GGUF llama.cpp Runtime:

   ```powershell
   lms runtime ls
   lms runtime get
   lms runtime select
   ```

4. Download an instruction model that fits your GPU memory or system RAM:

   ```powershell
   lms get --gguf
   lms ls --llm
   ```

Video Translator connects to `http://127.0.0.1:1234/v1` by default. It can start the local LM Studio server through `lms`, load the selected model, and unload it after translation. Select the model under **Settings → Services & diagnostics**.

See the [LM Studio CLI documentation](https://lmstudio.ai/docs/cli) and [Runtime documentation](https://lmstudio.ai/docs/cli/runtime/runtime) for details.

### Qwen3-TTS status

The local Qwen route targets only `Qwen3-TTS-12Hz-1.7B-Base-GGUF`. LM Studio may provide the downloaded GGUF files, while an application-private, hash-pinned `llama-tts` worker performs synthesis. Eight application-owned, public-domain LibriVox reference samples are presented as fixed built-in voices; Standard does not expose sample paths, uploads, replacement, or managed voice cloning.

Qwen dubbing is available when the reviewed private `llama-tts` runtime bundle is configured. It does not depend on an LM Studio speech API and does not require Python, Torch, or the official `qwen-tts` Python package. Qwen mode keeps all ten supported languages, including Korean, on the local worker.

## Start the application

Activate the Conda environment, then run:

```powershell
.\start.bat
```

Open <http://127.0.0.1:8769/> in a browser.

To use another port:

```powershell
$env:APP_PORT = "9000"
.\start.bat
```

## First run

1. Open **Settings → Services & diagnostics**.
2. Keep `lm_studio` as the LLM provider and select a downloaded model.
3. If `ASR_MODEL_PATH` is empty, faster-whisper downloads the selected `ASR_MODEL_SIZE`; the default is `base`.
4. If the Kokoro paths are empty, the first dubbing task downloads the model into `models/kokoro/`.
5. Upload a video and select a target language.
6. After transcription, choose **Subtitles** or **Dubbing** under Process, then download the artifacts from Export.

The first Whisper, Kokoro, or LM Studio model download requires internet access and sufficient disk space.

## Configuration

The defaults can start the application. To override them:

```powershell
Copy-Item config.example.yaml config.yaml
```

`config.yaml` is ignored by Git because it may contain API keys and machine-specific paths. Settings can also be edited in the application.

The Settings dialog is generated from the backend settings schema. Basic fields are shown first; use **Show advanced settings** for runtime, chunking, sampling, and local model-path controls. Switching providers only changes which fields are visible—it does not erase the hidden provider configuration. API keys remain masked unless explicitly cleared.

Common fields:

- `LLM_PROVIDER`: `lm_studio`, `ollama`, or `openai_compatible`
- `LM_STUDIO_MODEL`, `LM_STUDIO_CLI_PATH`, `LM_STUDIO_TTL_SECONDS`
- `ASR_MODEL_SIZE`, `ASR_MODEL_PATH`, `ASR_API_URL`
- `DEVICE_PREFERENCE`: `auto`, `gpu`, or `cpu`
- `COMPUTE_TYPE`: `auto`, `float16`, or `int8`
- `TTS_MODE`: `kokoro`, `edge`, or `speaches`
- `DUB_SAMPLE_RATE`: normalized mono PCM output rate (default `22050`)
- `KOKORO_MODEL_PATH`, `KOKORO_VOICES_PATH`
- `FFMPEG_PATH`

See [`config.example.yaml`](config.example.yaml) for a complete example.

## Local processing and network use

| Feature | Default behavior |
| --- | --- |
| Whisper transcription | Runs locally after the model is downloaded |
| LM Studio translation | Runs in the user's local LM Studio instance |
| Qwen3-TTS dubbing | Optional local Provider using a reviewed private `llama-tts` runtime and hash-pinned GGUF files |
| Kokoro dubbing | Runs locally after the model is downloaded |
| Korean Edge TTS | Sends subtitle text to Microsoft's online speech service |
| Speaches / OpenAI-compatible providers | Sends data to the user-configured service endpoint |

Uploaded videos, subtitles, dubbing artifacts, and task history remain on the local machine by default. Video Translator does not intentionally upload them to a project-operated cloud service. Review the privacy terms of any online provider you enable.

Do not expose the development server to the public internet. It listens on `127.0.0.1` by default.

## Troubleshooting

### `No LM Runtime found for model format 'gguf'`

Run `lms runtime ls`. If no compatible Runtime is installed, use `lms runtime get`, then select a GGUF llama.cpp Runtime with `lms runtime select`. You can also press `Ctrl+Shift+R` in LM Studio to open its Runtime page.

### `lms` is not found

Launch LM Studio once, reopen PowerShell, and run `lms --help`. You can also set the full `LM_STUDIO_CLI_PATH` in Settings.

### FFmpeg is not found

Confirm that `ffmpeg -version` and `ffprobe -version` work, or select `ffmpeg.exe` in Settings.

### The first transcription or dubbing task is slow

A model may still be downloading. Check the startup console and verify network access, available disk space, and proxy settings.

### CPU mode is too slow or runs out of memory

Use smaller Whisper and LLM models, set `DEVICE_PREFERENCE` to `cpu`, and use `COMPUTE_TYPE=int8`. Long videos can still require substantial processing time.

## Known limitations

- There is no Windows installer or portable release yet.
- Quality, memory use, and translation speed depend on the selected models.
- Kokoro Mandarin voices have model-level tone and naturalness limitations.
- Edge dubbing requires internet access and sends subtitle text to Microsoft speech services.
- Qwen3-TTS 1.7B Base may reuse GGUF files downloaded by LM Studio; synthesis runs through the configured private `llama-tts` worker.
- Speaches voice cloning remains an experimental compatibility path and is not part of the accepted primary workflow.
- Multi-video batch processing and one-transcription-to-multiple-target-language workflows are still planned.

## Development and validation

Backend compilation:

```powershell
python -m compileall -q app
```

Frontend build:

```powershell
cd app/frontend
npm ci
npm run build
```

Unified release checks, including the online production dependency audit:

```powershell
python packaging/release_check.py --production-audit
```

For a clean, committed release candidate, add
`--evidence-output .codex-test-runs/release-evidence.json` to record the commit,
fixed Qwen asset hashes, and exact checks performed.

GitHub Actions runs Python compile/import smoke checks, a production dependency audit, and the frontend build on Windows with Python 3.11 and Node.js 20. GPU, real-model, and long-video validation remain manual.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for contribution guidance.

## Runtime directories

The following local data is not committed:

- `config.yaml`
- `app/history.json`
- `models/`
- `app/uploads/`, `app/output/`, and `app/temp/`
- root-level `uploads/`, `output/`, and `temp/`
- `.codex-backups/` and `.codex-test-runs/`

## Contributing and security

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before contributing. Report security issues privately as described in [`SECURITY.md`](SECURITY.md).

## License and third-party software

Video Translator source code is released under the [MIT License](LICENSE). Dependencies, models, and external tools retain their own licenses and terms. See [Third-Party Notices](THIRD-PARTY-NOTICES.md) before redistributing a binary build.

# Release checklist

## Windows x64 portable acceptance candidate

Build only from a clean tracked worktree. The source inputs below are examples;
use the already accepted local runtime and model directories for the candidate:

```powershell
python packaging/portable_builder.py --output-dir .codex-test-runs/portable-<commit>/VideoTranslator-portable-<commit>-win-x64 --python-runtime <accepted-app>/python --ffmpeg-dir ffmpeg --asr-model models/faster-whisper-small --voices models/voices/librivox_public_domain --kokoro <accepted-app>/models/kokoro --qwen-bundle <accepted-app>/models/qwen3-tts --frontend-dist app/frontend/dist
python packaging/portable_verify.py .codex-test-runs/portable-<commit>/VideoTranslator-portable-<commit>-win-x64
<bundle>\runtime\python.exe -B <bundle>\portable_runtime_smoke.py --output .codex-test-runs/portable-<commit>/qwen-ja-male-smoke.wav
```

After those checks pass, create the ZIP with `create_zip()` from
`packaging/portable_builder.py`, open it with Python `zipfile`, and record its
SHA-256 in ignored local evidence. Copy that exact ZIP to a clean Windows x64
machine for the remaining relocation test.

The normal candidate remains `local_acceptance_only`. Its `licenses/` directory
contains a machine-readable inventory plus the license files found in the exact
Python runtime, production npm dependency tree, and Qwen runtime. Missing files
remain explicit in `licenses/THIRD-PARTY-MANIFEST.json` and
`portable-manifest.json`; this inventory does not turn an acceptance build into
a public release.

For a public build, prepare an external material directory containing
`release-materials.json`, then add
`--release-materials <directory> --public-distribution`. The builder refuses the
build if any installed Python or
production frontend package lacks an attached license text, if the Qwen runtime
has no embedded license, or if an external component is incomplete. The external
manifest uses schema version 1 and contains one entry for each of `ffmpeg`,
`asr-model`, `kokoro-model`, and `qwen-model`. Every entry must provide:

- `license_expression`, `source_url`, and exact `source_revision`;
- `artifacts`, whose SHA-256 set exactly matches the assets being packaged;
- `attachments`, each with `role`, relative `path`, and SHA-256.

FFmpeg requires the roles `license`, `build_info`, and `corresponding_source`.
Each model requires `license` and `model_card`. For the current static GPLv3
FFmpeg build, `corresponding_source` must identify and attach the complete
corresponding-source material for that exact build, not merely the FFmpeg Git
commit. If an installed Python or frontend distribution omitted its license
file, add a supplemental component named `python:<normalized-name>-<version>` or
`frontend:<normalized-name>-<version>` with the same source fields and a
`license` attachment. `qwen-runtime` is the corresponding supplemental ID.
Review the generated manifest before publishing.

### Security checks for new public builds

Run both production and full frontend dependency audits, plus a current PyPI
advisory scan of the actual `python-packages.json`. As of 2026-09-18, public builds
reject installed pip below 26.2.0 and wheel below 0.46.2; pip wheels under
`runtime/Lib/ensurepip/_bundled` must also meet that minimum. Update tools and
bootstrap wheels in a disposable runtime, collect their exact license texts, and
verify the inventory before rebuilding. Preserve the original released ZIP.

Setuptools 80.9.0 is retained for jieba's `pkg_resources` compatibility. Its
GHSA-h35f-9h28-mq5c advisory concerns Unicode exclusion rules when publishing
source distributions, especially on macOS; this Windows ZIP builder does not run
`sdist`. Do not use the bundled runtime to publish source distributions. Review
this exception when the platform, build process or setuptools dependency changes.

Verify HTTP origin/Host checks, upload isolation, preview seeking and subtitle
export on the new candidate. The source fixes are not present in an old portable
ZIP. Unchanged ASR/TTS models and native assets may reuse prior hardware results.

- [ ] Run `python packaging/release_check.py --production-audit`.
- [ ] Run the same command with `--qwen-archives-dir` and `--qwen-model-dir` on an NVIDIA release machine.
- [ ] Confirm production dependency audit has no high-severity findings.
- [ ] Confirm Qwen3-TTS licenses, notices, runtime manifest, and package manifest are included.
- [ ] Install into a clean application directory and verify Qwen preset voices are listed.
- [ ] Complete one Chinese, English, Japanese, and Korean dubbing task.
- [ ] Verify cancellation removes the child process and temporary output.
- [ ] Upgrade from package revision 1 and confirm rollback preserves the old bundle after a simulated failure.
- [ ] Confirm remote clients cannot access install, preflight, or local filesystem operations.
- [ ] Record the release commit and archive/model SHA-256 values.
- [ ] Run the eight-preset synthesis matrix and active-child cancellation/recovery checks described below.
- [ ] Run the same matrix with forced CUDA failure and verify CPU fallback for every preset.

## Extended Qwen runtime acceptance

### Documentation language

Use English for primary README files, setup scripts, progress/error messages and release instructions. The portable `README.md` and `README-PORTABLE.md` are English entry points; `.zh-CN.md` files are optional translations. Keep upstream license and attribution text unchanged, and preserve multilingual synthesis inputs used by acceptance tests. Local project handoffs may remain in the user's conversation language.

### Public core with user-installed upstream dependencies

The selected public edition now uses `--public-distribution --upstream-dependencies`.
The builder omits Gyan FFmpeg, Kokoro model/voices, the complete espeakng-loader
wheel, and the assembled Qwen model/runtime bundle. It audits the copied runtime,
not the original runtime, and independently rejects upstream-only files in the
finished public core. It still requires licenses for every shipped Python/npm
dependency and exact ASR model attachments. This is not a waiver for bundled files.

```powershell
python packaging/portable_builder.py --output-dir .codex-test-runs/public-core --python-runtime C:\ReviewedPython --asr-model models\faster-whisper-small --voices models\voices\librivox_public_domain --release-materials .codex-test-runs\release-materials-20260916 --public-distribution --upstream-dependencies --zip
python packaging/portable_verify.py .codex-test-runs/public-core
```

The core starts before optional dependencies are installed. Settings reports
missing components and links to the included user guide. `install_upstream.py`
shows sources/terms before downloading; user invocation with
`--accept-upstream-terms` downloads pinned artifacts directly from upstream and
checks hashes. Qwen remains installed through the existing local preflight and
transactional installer. No files are mirrored by this project.

Verify the pristine public core first. A directory augmented with user downloads
is not the original distribution artifact and cannot reuse its readiness claim.
Exercise installation in a separate disposable copy, including the missing-loader
error and normal startup without FFmpeg/Qwen. Reuse prior hardware/function
acceptance when the installed assets and related implementation remain unchanged;
do not repeat the CUDA/CPU, Edge, rollback and RX 5700 XT matrices solely because
the delivery profile changed.

```powershell
python packaging/release_check.py --qwen-archives-dir C:\Downloads\llama-b10792 --qwen-model-dir D:\models\qwen --qwen-voice-matrix --qwen-verify-cancellation
python packaging/release_check.py --qwen-archives-dir C:\Downloads\llama-b10792 --qwen-model-dir D:\models\qwen --qwen-voice-matrix --qwen-verify-cancellation --qwen-force-cuda-failure
python packaging/release_check.py --qwen-archives-dir C:\Downloads\llama-b10792 --qwen-model-dir D:\models\qwen --qwen-clean-app-smoke
```

On the accepted AMD host, run the application boundary with the pinned Vulkan
archive and the same model directory:

```powershell
python packaging/release_check.py --qwen-archives-dir C:\Downloads\llama-b10792 --qwen-model-dir D:\models\qwen --qwen-smoke-device vulkan --qwen-voice-matrix --qwen-verify-cancellation --qwen-clean-app-smoke
```

Then upgrade an existing revision 2 CUDA bundle through the local settings UI.
Confirm the installed status reports revision 3 / Vulkan, complete one real
dubbing task, and simulate activation failure once to confirm the revision 2
directory is restored byte-for-byte.

The optional matrix covers female and male presets in Chinese, English, Japanese
and Korean. Cancellation is requested after a child is running, followed by
synthesis using the same worker. Each successful WAV is validated by the worker;
the smoke checks actual device and fallback reason and prints per-sample hashes.
All observed children must be reaped and their temporary synthesis directories
removed. The disposable installation is removed after the run.

These checks verify the runtime boundary, not the complete video dubbing UI or
subjective voice quality. Keep the multilingual dubbing and listening checks
above pending until they have been performed for the release being shipped.

After committing the exact candidate and confirming its tracked worktree is clean,
write a machine-readable record only after all selected checks pass:

```powershell
python packaging/release_check.py --production-audit --evidence-output .codex-test-runs/release-evidence.json
```

Add the Qwen archive/model and extended smoke arguments to that command for final
hardware evidence. The JSON records the exact scope, so omitted real-asset checks
cannot be mistaken for having passed.

The clean application smoke copies only shipped package metadata and preset voices
into an empty disposable application root. It then uses the local preflight,
installation and status APIs, lists all eight Qwen voices through the dubbing API,
and synthesizes through the registered Qwen provider. The directory is removed
after the provider closes.

## Settings browser regression (mock APIs)

With frontend dependencies and `playwright-cli` installed, start Vite in one terminal:

```powershell
cd app/frontend
npm run dev -- --host 127.0.0.1 --port 4178 --strictPort
```

From the repository root in another terminal:

```powershell
playwright-cli -s=qwen-settings open
playwright-cli -s=qwen-settings run-code --filename=tests/browser/qwen-settings.js
playwright-cli -s=qwen-settings close
```

The browser check mocks every API request and covers boolean preference persistence,
English/Chinese runtime status, polling updates, clearing old fallback warnings,
and remote-client isolation. It does not modify backend configuration and is run
separately from `release_check.py`. Stop the Vite process afterward.

# AMD backend candidates for Qwen3-TTS

Reviewed through 2026-09-14 against llama.cpp **b10792 / c5a5535e6**.
Status: **archive inventory and RX 5700 XT matrix passed; application integration implemented; application hardware acceptance pending**.
The application now has a separately attested Vulkan runtime manifest and
transactional installer path. This document and the audit reports themselves
are not runtime manifests or installation inputs.

## RX 5700 XT full-Vulkan baseline

The pinned Vulkan candidate completed one Chinese preset synthesis on an AMD
Radeon RX 5700 XT under Windows 10 LTSC 21H2 with driver 32.0.21045.5002.
`--list-devices` reported `Vulkan0` with 8176 MiB total and 7382 MiB free. The
log proves an 811.93 MiB Vulkan model buffer, 29/29 backbone layers offloaded,
and both CLIP contexts on Vulkan0. It generated a valid 2.00-second mono PCM-16
24 kHz WAV (96,044 bytes, SHA-256
`e5563c0441ca00f5126e4ccc6111dddbcfa0e1c6de210710ca6478955f0929a5`).
The llama timing was 3.18 seconds and the complete audited run took 12.281
seconds. Temporary runtime cleanup succeeded.

This AMD result did not reproduce the GET_ROWS assertion observed with the same
pinned full-Vulkan path on the RTX 3090. It establishes a real AMD baseline.
Evidence is kept locally under `5700xt-test/` and must not be committed.

## RX 5700 XT language and recovery matrix

The same host subsequently passed Chinese, English, Japanese and Korean preset
synthesis, a repeated Chinese request, cancellation, a forced timeout and a
post-timeout recovery synthesis. Every completed request kept the 811.93 MiB
model buffer, all 29 backbone layers and both mmproj/CLIP contexts on Vulkan0.
The logs contain no GET_ROWS assertion, backend error or CPU fallback.

The six completed WAV files are valid mono PCM-16 at 24 kHz, range from 2.32 to
4.16 seconds and from 111,404 to 199,724 bytes. Independent hash and waveform
checks matched the report. The whole matrix took 37.766 seconds, cancellation
and timeout both reaped their child processes, the recovery request succeeded,
and temporary runtime cleanup succeeded. Four-language subjective listening
also passed. Application installation, synthesis, cancellation and upgrade
rollback on the RX 5700 XT remain release gates.

## Official inputs

Both archives are listed in the [pinned upstream release](https://github.com/ggml-org/llama.cpp/releases/tag/b10792).
The SHA-256 values below were obtained from the [release API](https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b10792)
and matched against the downloaded archives locally.

| Candidate | Archive | Compressed bytes | SHA-256 |
|---|---|---:|---|
| Vulkan | `llama-b10792-bin-win-vulkan-x64.zip` | 35,208,125 | `c55e5ce547153d21ff2ab4f19fada11e808f7372db8c57f69ca721d5976b2613` |
| HIP / ROCm | `llama-b10792-bin-win-rocm-10.0-x64.zip` | 244,224,602 | `822cc805833e2f3277b0b8482f79453a6bc8d49f311f9c5856a276ec2c368f3f` |

Both contain `llama-tts.exe`, `mtmd.dll`, common llama/ggml libraries, CPU backend
variants, `libomp.dll`, and `LICENSE-LLVM-OpenMP`. Vulkan adds `ggml-vulkan.dll`.
ROCm adds `ggml-hip.dll`, `amdhip64_7.dll`, `amd_comgr.dll`, and `rocm_kpack.dll`.
The ROCm archive name and HIP DLL suffix are recorded as published; they are not
interchangeable version labels. Neither inventory proves that all transitive
DLL dependencies or redistribution obligations have been resolved.

## Reproduce the static audit

Place the official archives in a local directory and run:

```powershell
python packaging/audit_qwen_backends.py --archives-dir C:\Downloads\llama-b10792 --backend vulkan
python packaging/audit_qwen_backends.py --archives-dir C:\Downloads\llama-b10792 --backend hip
```

The script verifies the pinned archive hash, reads ZIP entries and their hashes,
checks required files, rejects ambiguous paths, and returns a JSON inventory.
It does not extract, execute, install, download, or modify the runtime. A successful
report always has `amd_hardware_validated=false` and `production_ready=false`.
Downloaded binaries and local reports stay outside Git.

## Integration decision

Investigate Vulkan first because its candidate archive is substantially smaller.
This is an engineering prioritization, not a performance or GPU compatibility
claim. Keep HIP as a separate candidate with its own dependency and GPU matrix.

The [pinned build guide](https://github.com/ggml-org/llama.cpp/blob/b10792/docs/build.md#hip)
documents HIP builds with `GGML_HIP` and GPU architecture targets. Its
[Vulkan section](https://github.com/ggml-org/llama.cpp/blob/b10792/docs/build.md#vulkan)
documents `GGML_VULKAN`. Generic backend availability does not establish that
Qwen3-TTS backbone and audio generation operations work on a particular AMD GPU.

The local validation host has an NVIDIA GeForce RTX 3090 and no detected AMD
adapter. No AMD synthesis, speed, memory, or cancellation result is claimed.

## Executable Vulkan investigation on the NVIDIA host

`packaging/qwen_vulkan_smoke.py` now provides an isolated Windows experiment.
It checks the archive, model, mmproj and preset hashes, extracts only the TTS
runtime into a temporary directory, and enumerates devices. Multiple Vulkan
devices require an explicit `--device` choice. It saves logs and a JSON report
to a new output directory; existing output directories are not overwritten.
No application configuration or installed bundle is changed.

```powershell
python packaging/qwen_vulkan_smoke.py --archives-dir C:\Downloads\llama-b10792 --model-dir D:\models\qwen --output-dir .codex-test-runs\vulkan-full
python packaging/qwen_vulkan_smoke.py --archives-dir C:\Downloads\llama-b10792 --model-dir D:\models\qwen --output-dir .codex-test-runs\vulkan-hybrid --mmproj-device cpu
```

The default requires both backbone and mmproj on Vulkan. The explicit CPU
mmproj option is a diagnostic hybrid. Success requires a valid WAV, positive
Vulkan backbone allocation, all backbone layers offloaded, and log evidence
that every reported CLIP context used the requested mmproj device. Verbosity 4
is necessary for this placement evidence in the pinned binary. Reports always
retain `production_ready=false` and `amd_hardware_validated=false`.

Observed results on RTX 3090:

| Mode | Result |
|---|---|
| Vulkan backbone + Vulkan mmproj | Failed reproducibly with exit code `3221226505` (`0xC0000409`). Logs show `GGML_OP_GET_ROWS` misalignment assertion at `ggml-vulkan.cpp:12105`. No usable WAV. |
| Vulkan backbone + CPU mmproj | Produced a validated 115,244-byte WAV. Logs show 29/29 backbone layers on Vulkan, an 811.93 MiB Vulkan model buffer, and CPU CLIP contexts. |

The assertion is in the [pinned Vulkan source](https://github.com/ggml-org/llama.cpp/blob/b10792/ggml/src/ggml-vulkan/ggml-vulkan.cpp#L12105).
The mode comparison narrows the failing path; it does not establish a complete
root cause or an AMD workaround. Full Vulkan remains blocked. No timing or
audio-quality acceptance matrix has been completed for the hybrid mode.

### Fixed diagnostic profiles

The smoke CLI accepts `--diagnostic-profile` with these fixed choices:

| Profile | Child-only changes | RTX 3090 result with Vulkan mmproj |
|---|---|---|
| `baseline` | None | GET_ROWS alignment assertion |
| `no-op-offload` | `--no-op-offload` during synthesis | Same assertion |
| `no-vulkan-optimizations` | `GGML_VK_DISABLE_FUSION=1`, `GGML_VK_DISABLE_GRAPH_OPTIMIZE=1` | Same assertion |

For example, append `--diagnostic-profile no-vulkan-optimizations` to the full
Vulkan command above and choose a new output directory. The profile is explicit
and recorded in `report.json`; inherited llama/ggml overrides are cleared before
the selected profile is applied. There is no arbitrary environment or argument
passthrough, and no profile is a verified workaround.

Failed reports now retain initialization placement evidence when available.
`failure.code=vulkan_get_rows_alignment` requires both a failed child and the
specific Vulkan assertion text. A timeout, cancellation, validation failure or
generic nonzero exit receives a different code. Successful initialization and
29/29 offloaded layers do not imply successful audio generation.

Source inspection found offset index views passed to `ggml_get_rows` in
[qwen3tts-gen.cpp](https://github.com/ggml-org/llama.cpp/blob/b10792/tools/mtmd/models/qwen3tts-gen.cpp#L251).
The pinned Vulkan `supports_op` GET_ROWS branch accepts supported element types
without that alignment check, while execution asserts zero misalignment. This
is a concrete investigation lead, not a proven identification of the exact
runtime tensor that triggers the assertion. A potential upstream fix must
handle misaligned index views correctly or decline that operation so it can be
scheduled elsewhere; simply deleting the assertion is not a valid fix.
No upstream binary, model or hash has been replaced in this investigation.

### Upstream fix status checked on 2026-09-07

The reproduced failure matches upstream issue
[#26853](https://github.com/ggml-org/llama.cpp/issues/26853).
PR [#28253](https://github.com/ggml-org/llama.cpp/pull/28253) is the active
type-aligned GET_ROWS fix; it was open and unmerged at review, with head
`46b2e9396f1fa337ba692b8e9527fde192963e47`. It adjusts descriptor offsets,
handles quantized shader offsets, and adds backend tests. Earlier proposals
[#26854](https://github.com/ggml-org/llama.cpp/pull/26854) and
[#28240](https://github.com/ggml-org/llama.cpp/pull/28240) were closed without
merging. Their closed state must not be interpreted as a released fix.

No local C++ compiler/CMake/Vulkan SDK toolchain was found for independently
building this PR. Keep b10792 unchanged. Recheck the PR and release containing
it before reviewing a new candidate's hashes, dependencies, GET_ROWS tests and
full Qwen synthesis. Upstream reviewer results are not a substitute for this
application's AMD hardware acceptance.

Timeout and asyncio cancellation kill and reap the child. Temporary runtime
cleanup retries short-lived Windows DLL locks for up to five seconds. Reports
preserve the original failure and separately record any remaining cleanup error.

### DLL and license review

PE import and delay-import inspection with `pefile 2024.8.26` covered 22 selected
EXE/DLL files. Together with the OpenMP license, this experiment extracts
87,996,701 bytes. Server, RPC and unrelated command-line tools are omitted.
Imports outside the selected set consist of Windows system DLLs and CRT API
sets, `MSVCP140.dll`, `VCRUNTIME140.dll`, `VCRUNTIME140_1.dll`, and `vulkan-1.dll`.
The child PATH includes only the temporary runtime and Windows system paths;
inherited llama/ggml environment overrides are removed.

This establishes the declared PE imports, not every library dynamically loaded
by a driver. A clean AMD machine still needs compatible Vulkan drivers and the
Visual C++ runtime. Those prerequisites are not bundled by this experiment.
The upstream llama.cpp license and the archive's `LICENSE-LLVM-OpenMP` must be
preserved in any future distributable. The latter's SHA-256 is
`fdad1758a9e1f9d5a81e18879b3406772115edc92c24bfa36b70c654f325e8e4`.
Redistribution notices and the final dependency closure remain release gates.

## Required before enabling a backend

- Audit imported DLLs, licenses and notices; build a separate minimal runtime
  manifest and installer transaction. Do not mix candidate DLLs into the CUDA bundle.
- On an actual AMD Windows host, record GPU model, driver, OS, selected backend
  device from `--list-devices`, and archive/model/voice hashes. Avoid hardcoded
  device indices on machines with multiple GPUs.
- Run the pinned 1.7B Base Q4_K_M backbone and mmproj with the existing presets.
  Record actual backbone and mmproj device placement, valid WAV output, and
  representative Chinese, English, Japanese and Korean synthesis results.
- Verify multiple subtitle segments, audio quality, elapsed time and memory use.
  CPU execution must not count as an AMD GPU pass.
- Verify cancellation and timeout cleanup, initialization/synthesis failure,
  CPU fallback on/off, repeated tasks, and install/upgrade rollback.
- Verify the application device policy, settings/API, release manifest,
  synthesis and rollback path on the accepted RX 5700 XT before publishing the
  expanded support matrix.

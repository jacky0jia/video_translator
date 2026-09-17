# Qwen3-TTS private Windows runtime notices

This directory describes the reviewed Windows x64 runtime; binary artifacts are not stored in Git.

- `llama-tts.exe`, llama/ggml/mtmd DLLs: llama.cpp build b10792, commit `c5a5535e6`, MIT. Include the upstream `LICENSE` as `LICENSE-llama.cpp`.
- `libomp.dll`: LLVM OpenMP runtime. Include the release artifact's `LICENSE-LLVM-OpenMP` unchanged.
- `cudart64_12.dll`, `cublas64_12.dll`, `cublasLt64_12.dll`: NVIDIA CUDA 12.4 redistribution files supplied by the official llama.cpp release. Distribution is subject to the NVIDIA CUDA Toolkit EULA and its redistribution terms.
- `ggml-vulkan.dll`: Vulkan backend supplied by the official llama.cpp release. The application installs it in a separate AMD runtime and does not mix it with CUDA libraries.
- Qwen3-TTS 1.7B Base model files are separate Apache-2.0 artifacts and are not part of this runtime directory.

Reviewed source archives:

- `llama-b10792-bin-win-cuda-12.4-x64.zip`, SHA-256 `9b0f2b64511d36688427daeada6f001ba544b5bcb5f5405fe86a0be4b230461d`.
- `cudart-llama-bin-win-cuda-12.4-x64.zip`, SHA-256 `8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6`.
- `llama-b10792-bin-win-vulkan-x64.zip`, SHA-256 `c55e5ce547153d21ff2ab4f19fada11e808f7372db8c57f69ca721d5976b2613`.

The assembled CUDA runtime must contain the 25 files in `runtime-manifest.b10792.json`.
The assembled Vulkan runtime must contain the 22 files in
`runtime-manifest-vulkan.b10792.json`. Each runtime also includes this notice,
`LICENSE-llama.cpp`, and `LICENSE-LLVM-OpenMP`. Do not include llama-server,
RPC, download, benchmark, conversion, quantization, or unrelated CLI executables.

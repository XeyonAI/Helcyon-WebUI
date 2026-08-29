# Native Qwen3-TTS backend

The existing `qwen-fast` HWUI engine key now launches qwentts.cpp with the 0.6B Base Q8 talker/tokenizer on port 8767. The UI key, voice selection, streaming playback, and shared WAV/TXT voice library remain unchanged. The former `Qwen3-TTS-Fast` Python source stays in the build as a fallback but is not launched while native Qwen is selected. The separate experimental `qwentts-cpp` 1.7B option remains available on port 8768.

## Windows CUDA setup

Prerequisites are Git, CMake, Visual Studio 2022 C++ Build Tools, the NVIDIA CUDA toolkit, and Hugging Face CLI (`huggingface-cli`). Set `HWUI_BUILD` to the build being configured, then run these commands in a Developer PowerShell for Visual Studio 2022:

```powershell
$HWUI_BUILD = "D:\path\to\your\HWUI-build"
Set-Location $HWUI_BUILD
git clone --recurse-submodules https://github.com/ServeurpersoCom/qwentts.cpp.git qwentts.cpp
Set-Location .\qwentts.cpp
if (Test-Path .\build) { Remove-Item -LiteralPath .\build -Recurse -Force }
cmake -S . -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120a-real "-DCUDAToolkit_ROOT=$env:CUDA_PATH_V13_0"
cmake --build build --config Release -j $env:NUMBER_OF_PROCESSORS
New-Item -ItemType Directory -Force .\models
huggingface-cli download Serveurperso/Qwen3-TTS-GGUF qwen-talker-0.6b-base-Q8_0.gguf qwen-tokenizer-12hz-Q8_0.gguf --local-dir .\models
```

The CUDA setting above is the RTX 5060 Ti / Blackwell target used by this build. It produces `compute_120a` / `sm_120a` code and avoids CUDA 13's unsupported legacy architecture list.

The HWUI wrapper checks the two Windows CMake output locations used by qwentts.cpp: `qwentts.cpp\build\Release\tts-server.exe` and `qwentts.cpp\build\tts-server.exe`. If the executable or models live elsewhere, add these keys to ignored `tts_paths.local.json`:

```json
{
  "HWUI_QWENTTS_SERVER_EXE": "D:\\qwentts.cpp\\build\\Release\\tts-server.exe",
  "HWUI_QWENTTS_MODELS_DIR": "D:\\qwentts.cpp\\models",
  "HWUI_QWENTTS_VOICES_DIR": "I:\\F5-TTS\\F5-TTS"
}
```

Place `qwen-talker-0.6b-base-Q8_0.gguf` and `qwen-tokenizer-12hz-Q8_0.gguf` in `qwentts.cpp\models`, select `Qwen3-TTS Native 0.6B Q8 (Streaming)` in Settings, save, then restart the HWUI Launcher. Startup, model-load errors, health readiness, CUDA backend selection, and shutdown are recorded in `HWUI-Launcher\electron-flask.log`; backend stdout/stderr is captured there too. `/api/tts/status` reports the active backend health and Q8 quantization.

For another HWUI build, copy the packaged native executables/DLLs but keep multi-GB models and voices outside the archive. Point that build's `tts_paths` user-config section (or environment) at its model and voice locations with `HWUI_QWENTTS_MODELS_DIR` and `HWUI_QWENTTS_VOICES_DIR`. No path falls back to the Dev checkout, so a copied build cannot silently depend on Dev remaining installed.

The bundled quantizer supports creating the Q3 talker from the existing Q8 talker on Windows:

```powershell
.\qwentts.cpp\build\Release\quantize.exe `
  .\qwentts.cpp\models\qwen-talker-1.7b-base-Q8_0.gguf `
  .\qwentts.cpp\models\qwen-talker-1.7b-base-Q3_K_M.gguf `
  Q3_K_M
```

The tool dequantizes Q8_0 tensors to F32 before applying the Q3_K_M mixed-precision policy. Codec-sensitive tensors that were retained as F32 in the Q8 file remain unquantized.

## Reusing voices and pre-encoding Sol

HWUI scans the same configured WAV voice directory used by Qwen Fast unless `HWUI_QWENTTS_VOICES_DIR` overrides it. A matching `.txt` transcript enables qwentts.cpp ICL cloning. With no transcript, qwentts.cpp uses x-vector-only cloning.

The first request for a voice registers it from the WAV/TXT pair in the native server's in-memory registry; every later request in that server lifetime reuses the extracted voice. Fresh `.spk` and `.rvq` sidecars are preferred when present. If the WAV or transcript is newer than either sidecar, HWUI invalidates the sidecars and registers from the updated WAV/TXT instead. To skip extraction after each server restart, create sidecars next to the WAV:

```powershell
$HWUI_BUILD = "D:\path\to\your\HWUI-build"
$VOICE_WAV = "D:\path\to\voices\Sol_American_Female.wav"
Set-Location (Join-Path $HWUI_BUILD "qwentts.cpp")
.\build\Release\qwen-codec.exe --model .\models\qwen-tokenizer-12hz-Q8_0.gguf --talker .\models\qwen-talker-0.6b-base-Q8_0.gguf -i $VOICE_WAV
```

Use the matching tokenizer/talker quantization when pre-encoding. The command writes `Sol_American_Female.spk` and `Sol_American_Female.rvq` beside the WAV. HWUI detects and uploads those compact latents automatically.

## Controlled A/B procedure

Use the same Sol WAV/TXT, test text, unloaded LLM state, and GPU power state for every run. Reboot or wait for the GPU to return to its idle memory level between backend/quantization changes.

1. Select Qwen3-TTS Fast, restart the launcher, and time from the launcher spawn log line until its status endpoint becomes online. Record `nvidia-smi --query-gpu=timestamp,memory.used,memory.free --format=csv` before startup and after warmup.
2. Run one discarded warmup, then five measured generations through HWUI. The script saves every WAV and samples total NVIDIA VRAM every 200 ms while requests run:

   ```powershell
   python .\scripts\benchmark_tts_backend.py --label qwen-fast --voice Sol_American_Female --runs 5 --text "The same fixed Sol evaluation paragraph goes here."
   ```

3. Repeat after selecting qwentts.cpp Q8_0, Q4_K_M, then Q3_K_M. Record the same idle/resident/peak VRAM readings and launcher spawn-to-health startup interval for each.
4. Keep the generated WAVs for a blind listening test. Randomize filenames, level-match without changing tempo, and score identity similarity, timbre, accent, prosody, artifacts, and overall preference from 1 to 5. Include at least one neutral paragraph, one emotional paragraph, and one dialogue-heavy paragraph.
5. Compare median generation seconds, median RTF, first VRAM sample, and peak VRAM from the script. RTF below 1.0 is faster than real time. Compare both absolute resident VRAM and incremental TTS VRAM over the same idle baseline. `nvidia-smi` readings are whole-GPU values, so no unrelated GPU process may change between runs.

For the final Ministral coexistence check, repeat only the winning TTS configuration with the same Ministral model/settings loaded, and compare available VRAM plus both LLM and TTS latency against the Fast baseline. Do not change LLM offload, context, KV cache, or sampling settings between runs.

Upstream contracts used by this integration:

- https://github.com/ServeurpersoCom/qwentts.cpp
- https://github.com/ServeurpersoCom/qwentts.cpp/blob/master/docs/ARCHITECTURE.md
- https://huggingface.co/Serveurperso/Qwen3-TTS-GGUF/tree/main

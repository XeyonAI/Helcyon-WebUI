from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import shutil
import struct
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

import numpy as np
import psutil
import soundfile as sf
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from faster_qwen3_tts import FasterQwen3TTS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"
MODEL_DIR = Path(os.getenv(
    "QWEN_FAST_MODEL_PATH",
    r"I:\HWUI-Pro-Dev-build\Qwen3-TTS\models\Qwen\Qwen3-TTS-12Hz-0.6B-Base",
))
VOICES = ROOT / "voices"
SHARED_VOICES = Path(os.getenv("QWEN_FAST_VOICES_DIR", r"I:\F5-TTS\F5-TTS"))
OUTPUTS = ROOT / "outputs"
BENCHMARKS = ROOT / "benchmarks"
OFFICIAL_OUTPUTS = OUTPUTS / "official"
FASTER_OUTPUTS = OUTPUTS / "faster"
STREAM_OUTPUTS = OUTPUTS / "streaming-captured"
PORT = 8767
HWUI_VOICE_TEMPERATURE = 0.8
for folder in (VOICES, OUTPUTS, BENCHMARKS, OFFICIAL_OUTPUTS, FASTER_OUTPUTS, STREAM_OUTPUTS):
    folder.mkdir(parents=True, exist_ok=True)


def gpu_stats() -> dict[str, float | None]:
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        util = pynvml.nvmlDeviceGetUtilizationRates(h)
        return {"vram_mb": round(mem.used / 1024**2, 1), "gpu_util_percent": float(util.gpu)}
    except Exception:
        return {"vram_mb": None, "gpu_util_percent": None}


def prompt_key(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", name.strip())[:60] or "voice"


class Sampler:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.peak_vram_mb = 0.0
        self.gpu_values: list[float] = []
        self.cpu_values: list[float] = []
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        psutil.cpu_percent(None)
        def sample() -> None:
            while not self.stop.wait(0.05):
                stats = gpu_stats()
                if stats["vram_mb"] is not None:
                    self.peak_vram_mb = max(self.peak_vram_mb, float(stats["vram_mb"]))
                if stats["gpu_util_percent"] is not None:
                    self.gpu_values.append(float(stats["gpu_util_percent"]))
                self.cpu_values.append(psutil.cpu_percent(None))
        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()

    def finish(self) -> dict[str, float | None]:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)
        return {
            "peak_vram_mb": round(self.peak_vram_mb, 1),
            "mean_gpu_util_percent": round(sum(self.gpu_values) / len(self.gpu_values), 1) if self.gpu_values else None,
            "mean_cpu_util_percent": round(sum(self.cpu_values) / len(self.cpu_values), 1) if self.cpu_values else None,
        }


class Runtime:
    def __init__(self) -> None:
        self.model: FasterQwen3TTS | None = None
        self.lock = threading.Lock()
        self.idle = gpu_stats()["vram_mb"]
        self.loaded_vram_mb: float | None = None
        self.load_seconds: float | None = None
        self.cuda_graphs_captured = False
        self.prompts: dict[str, dict[str, Any]] = {}
        self.prompt_meta: dict[str, dict[str, Any]] = {}
        self.results: dict[str, dict[str, Any]] = {}

    def load(self) -> FasterQwen3TTS:
        if self.model is not None:
            return self.model
        if not MODEL_DIR.is_dir():
            raise RuntimeError(f"Read-only official model path not found: {MODEL_DIR}")
        started = time.perf_counter()
        self.model = FasterQwen3TTS.from_pretrained(
            str(MODEL_DIR), device="cuda", dtype=torch.bfloat16,
            attn_implementation="sdpa", max_seq_len=512,
        )
        torch.cuda.synchronize()
        self.load_seconds = round(time.perf_counter() - started, 3)
        self.loaded_vram_mb = gpu_stats()["vram_mb"]
        return self.model

    def make_prompt(self, name: str, audio: Path, transcript: str) -> tuple[dict[str, Any], float]:
        model = self.load()
        key = prompt_key(name)
        started = time.perf_counter()
        reference_audio, sample_rate = model._load_ref_audio_with_silence(audio, silence_secs=0.5)
        reference_audio = reference_audio_with_headroom(reference_audio)
        items = model.model.create_voice_clone_prompt(
            ref_audio=(reference_audio, sample_rate),
            ref_text=transcript,
        )
        prompt = model.model._prompt_items_to_voice_clone_prompt(items)
        torch.cuda.synchronize()
        seconds = time.perf_counter() - started
        self.prompts[key] = prompt
        self.prompt_meta[key] = {"id": key, "reference_audio": str(audio), "reference_text": transcript,
                                 "creation_seconds": round(seconds, 4), "mode": "full_icl"}
        (VOICES / f"{key}.json").write_text(json.dumps(self.prompt_meta[key], indent=2), encoding="utf-8")
        return prompt, seconds

    def get_prompt(self, prompt_id: str) -> tuple[dict[str, Any], str, float]:
        if prompt_id in self.prompts:
            return self.prompts[prompt_id], self.prompt_meta[prompt_id]["reference_text"], 0.0
        meta_path = VOICES / f"{Path(prompt_id).name}.json"
        if not meta_path.is_file():
            raise ValueError(f"Unknown cached voice prompt: {prompt_id}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        prompt, elapsed = self.make_prompt(meta["id"], Path(meta["reference_audio"]), meta["reference_text"])
        return prompt, meta["reference_text"], elapsed


runtime = Runtime()
app = FastAPI(title="Faster Qwen3-TTS Native Windows Evaluation", version="1.0")


@app.on_event("startup")
def preload_runtime() -> None:
    if os.getenv("QWEN_FAST_PRELOAD", "1") != "1":
        return
    model = runtime.load()
    names = shared_voice_names()
    if not names:
        return
    voice = "Sol_American_Female" if "Sol_American_Female" in names else names[0]
    wav, transcript = shared_voice(voice)
    prompt, _ = runtime.make_prompt(voice, wav, transcript)
    torch.manual_seed(0)
    torch.cuda.manual_seed_all(0)
    for _chunk, _sr, _timing in model.generate_voice_clone_streaming(
        text="Ready.", language="English", ref_text=transcript,
        voice_clone_prompt=prompt, chunk_size=2, max_new_tokens=8,
        temperature=0.9, top_k=50, top_p=0.95, do_sample=True,
        repetition_penalty=1.05, xvec_only=False,
        non_streaming_mode=False, append_silence=True,
    ):
        pass
    torch.cuda.synchronize()
    runtime.cuda_graphs_captured = bool(model._warmed_up)


def wav_header(sample_rate: int = 24000) -> bytes:
    data_size = 0x7FFFFF00
    return struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", data_size + 36, b"WAVE", b"fmt ", 16,
                       1, 1, sample_rate, sample_rate * 2, 2, 16, b"data", data_size)


def pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(np.asarray(audio).reshape(-1), -1, 1) * 32767).astype("<i2").tobytes()


def completed_audio_with_headroom(audio: np.ndarray, target_peak: float = 0.95) -> np.ndarray:
    """Prevent an over-hot completed utterance from clipping during PCM encoding."""
    prepared = np.asarray(audio, dtype=np.float32).reshape(-1)
    if prepared.size == 0:
        return prepared
    peak = float(np.max(np.abs(prepared)))
    if np.isfinite(peak) and peak > target_peak:
        prepared = prepared * (target_peak / peak)
    return prepared


def reference_audio_with_headroom(audio: np.ndarray, target_peak: float = 0.5) -> np.ndarray:
    """Give loud clone references 6 dB of headroom without changing their source WAV."""
    prepared = np.asarray(audio, dtype=np.float32).reshape(-1)
    if prepared.size == 0:
        return prepared
    peak = float(np.max(np.abs(prepared)))
    if np.isfinite(peak) and peak > target_peak:
        prepared = prepared * (target_peak / peak)
    return prepared


def output_name(text: str, suffix: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:45] or "speech"
    return f"{datetime.now():%Y%m%d-%H%M%S-%f}_{slug}_{suffix}.wav"


def shared_voice_names() -> list[str]:
    if not SHARED_VOICES.is_dir():
        return []
    return sorted(
        wav.stem for wav in SHARED_VOICES.glob("*.wav")
        if (SHARED_VOICES / f"{wav.stem}.txt").is_file()
    )


def shared_voice(voice: str) -> tuple[Path, str]:
    safe = Path(str(voice).replace(".wav", "")).name
    wav = SHARED_VOICES / f"{safe}.wav"
    transcript = SHARED_VOICES / f"{safe}.txt"
    if not wav.is_file() or not transcript.is_file():
        raise ValueError(f"Voice '{safe}' needs matching .wav and .txt files in {SHARED_VOICES}")
    return wav, transcript.read_text(encoding="utf-8-sig").strip()


async def reference_file(upload: UploadFile | None, path: str | None) -> Path | None:
    if upload and upload.filename:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(upload.filename).name)
        target = VOICES / f"{datetime.now():%Y%m%d-%H%M%S}_{safe}"
        with target.open("wb") as stream:
            shutil.copyfileobj(upload.file, stream)
        return target
    if path:
        candidate = Path(path).resolve()
        if not candidate.is_file():
            raise HTTPException(400, f"Reference WAV not found: {candidate}")
        return candidate
    return None


def resolve_prompt(model: FasterQwen3TTS, ref: Path | None, transcript: str, prompt_id: str | None,
                   cache_name: str | None) -> tuple[dict[str, Any] | None, str, float, str | None]:
    if prompt_id:
        prompt, cached_text, elapsed = runtime.get_prompt(prompt_id)
        return prompt, cached_text, elapsed, prompt_id
    if ref is None:
        raise ValueError("Provide reference_audio, reference_audio_path, or voice_prompt_id")
    if cache_name:
        prompt, elapsed = runtime.make_prompt(cache_name, ref, transcript)
        key = re.sub(r"[^a-zA-Z0-9_.-]+", "_", cache_name.strip())[:60] or "voice"
        return prompt, transcript, elapsed, key
    return None, transcript, 0.0, None


def generation_kwargs(text: str, language: str, transcript: str, ref: Path | None,
                      prompt: dict[str, Any] | None, seed: int, chunk_size: int,
                      max_new_tokens: int = 512, temperature: float = 0.9) -> dict[str, Any]:
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    return {"text": text, "language": language, "ref_audio": str(ref) if ref and prompt is None else None,
            "ref_text": transcript, "voice_clone_prompt": prompt, "chunk_size": chunk_size,
            "max_new_tokens": max_new_tokens, "temperature": temperature, "top_k": 50, "top_p": 0.95,
            "do_sample": True, "repetition_penalty": 1.05, "xvec_only": False,
            "non_streaming_mode": False, "append_silence": True}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/health")
def health(load_model: bool = False) -> dict[str, Any]:
    error = None
    if load_model:
        try: runtime.load()
        except Exception as exc: error = str(exc)
    return {"status": "error" if error else "ok", "model_loaded": runtime.model is not None,
            "model_path": str(MODEL_DIR), "backend": "torch-cuda-graphs", "native_windows": True,
            "cuda_graphs_captured": runtime.cuda_graphs_captured, "torch": torch.__version__,
            "cuda": torch.version.cuda, "gpu": torch.cuda.get_device_name(0), "dtype": "bfloat16",
            "attention": "sdpa", "model_load_seconds": runtime.load_seconds,
            "idle_vram_mb": runtime.idle, "loaded_vram_mb": runtime.loaded_vram_mb, "error": error}


@app.get("/backend")
def backend() -> dict[str, Any]:
    return {"implementation": "andimarafioti/faster-qwen3-tts", "version": "0.3.0",
            "commit": "6cdb07a3deea6a8d097a5493963f9b2df3fd9af9", "backend": "PyTorch CUDA graphs",
            "triton": False, "flash_attention": False, "attention": "SDPA",
            "true_decoded_audio_streaming": True, "cached_voices": list(runtime.prompt_meta.values())}


# HWUI-compatible endpoints. These keep the existing HWUI voice contract
# (VoiceName.wav + VoiceName.txt) while the standalone evaluation API remains available.
@app.get("/voices")
def hwui_voices() -> dict[str, Any]:
    return {"voices": shared_voice_names(), "voices_dir": str(SHARED_VOICES)}


@app.get("/status")
def hwui_status() -> dict[str, Any]:
    return {"status": "online", "engine": "qwen-fast", "gpu": torch.cuda.get_device_name(0),
            "model_loaded": runtime.model is not None, "cuda_graphs_captured": runtime.cuda_graphs_captured,
            "voices_dir": str(SHARED_VOICES)}


@app.post("/warmup")
def hwui_warmup(payload: dict[str, Any]) -> dict[str, Any]:
    voice = str(payload.get("voice") or "Sol")
    with runtime.lock:
        wav, transcript = shared_voice(voice)
        _prompt, seconds = runtime.make_prompt(voice, wav, transcript)
    return {"status": "ok", "voice": voice, "voice_prompt_processing_seconds": round(seconds, 4)}


@app.post("/tts_to_audio")
def hwui_tts_to_audio(payload: dict[str, Any]) -> FileResponse:
    text = str(payload.get("text") or "").strip()
    voice = str(payload.get("voice") or "Sol")
    if not text:
        raise HTTPException(400, "No text provided")
    with runtime.lock:
        try:
            model = runtime.load()
            wav_path, transcript = shared_voice(voice)
            key = prompt_key(voice)
            prompt, _seconds = runtime.make_prompt(voice, wav_path, transcript) if key not in runtime.prompts else (runtime.prompts[key], 0.0)
            torch.manual_seed(int(payload.get("seed", 42)))
            torch.cuda.manual_seed_all(int(payload.get("seed", 42)))
            wavs, sr = model.generate_voice_clone(
                text=text, language=str(payload.get("language") or "English"),
                ref_text=transcript, voice_clone_prompt=prompt,
                max_new_tokens=int(payload.get("max_new_tokens", 512)),
                temperature=HWUI_VOICE_TEMPERATURE, top_k=50, top_p=0.95, do_sample=True,
                repetition_penalty=1.05, xvec_only=False,
                non_streaming_mode=False, append_silence=True,
            )
            name = output_name(text, f"hwui_{voice}")
            target = FASTER_OUTPUTS / name
            sf.write(target, completed_audio_with_headroom(wavs[0]), sr)
            return FileResponse(target, media_type="audio/wav", filename=name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.post("/tts_stream")
async def hwui_tts_stream(payload: dict[str, Any]) -> StreamingResponse:
    text = str(payload.get("text") or "").strip()
    voice = str(payload.get("voice") or "Sol")
    if not text:
        raise HTTPException(400, "No text provided")
    async def generate() -> AsyncIterator[bytes]:
        items: queue.Queue[bytes | Exception | object] = queue.Queue()
        done = object()

        def producer() -> None:
            chunks: list[np.ndarray] = []
            sr = 24000
            try:
                # Keep CUDA generation independent of the HTTP consumer. If a
                # browser aborts, this worker completes normally and releases
                # the single model lock instead of leaving later streams wedged.
                with runtime.lock:
                    model = runtime.load()
                    wav_path, transcript = shared_voice(voice)
                    key = prompt_key(voice)
                    prompt, _seconds = runtime.make_prompt(voice, wav_path, transcript) if key not in runtime.prompts else (runtime.prompts[key], 0.0)
                    stream = model.generate_voice_clone_streaming(**generation_kwargs(
                        text, str(payload.get("language") or "English"), transcript,
                        None, prompt, int(payload.get("seed", 42)),
                        max(1, int(payload.get("chunk_size", 2))), int(payload.get("max_new_tokens", 512)),
                        temperature=HWUI_VOICE_TEMPERATURE,
                    ))
                    for chunk, sr, _timing in stream:
                        audio = np.asarray(chunk).reshape(-1)
                        chunks.append(audio)
                        items.put(pcm16(audio))
                    if chunks:
                        name = output_name(text, f"hwui_stream_{voice}")
                        sf.write(STREAM_OUTPUTS / name, np.concatenate(chunks), sr)
            except Exception as exc:
                items.put(exc)
            finally:
                items.put(done)

        threading.Thread(target=producer, daemon=True).start()
        yield wav_header(24000)
        loop = asyncio.get_running_loop()
        while True:
            item = await loop.run_in_executor(None, items.get)
            if item is done:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    return StreamingResponse(generate(), media_type="audio/wav", headers={"X-Audio-Streaming": "decoded-pcm"})


@app.get("/result/{request_id}")
def result(request_id: str) -> dict[str, Any]:
    if request_id not in runtime.results: raise HTTPException(404, "Result not ready")
    return runtime.results[request_id]


@app.get("/audio/{group}/{filename}")
def audio(group: str, filename: str) -> FileResponse:
    folders = {"faster": FASTER_OUTPUTS, "streaming-captured": STREAM_OUTPUTS, "official": OFFICIAL_OUTPUTS}
    path = folders.get(group, OUTPUTS) / Path(filename).name
    if not path.is_file(): raise HTTPException(404, "Audio not found")
    return FileResponse(path, media_type="audio/wav")


@app.post("/clone")
async def clone(text: str = Form(...), language: str = Form("English"), reference_text: str = Form(""),
                reference_audio_path: str | None = Form(None), reference_audio: UploadFile | None = File(None),
                voice_prompt_id: str | None = Form(None), cache_voice_name: str | None = Form(None),
                seed: int = Form(42), max_new_tokens: int = Form(512)) -> dict[str, Any]:
    ref = await reference_file(reference_audio, reference_audio_path)
    with runtime.lock:
        try:
            model = runtime.load(); prompt, transcript, prompt_seconds, prompt_name = resolve_prompt(model, ref, reference_text, voice_prompt_id, cache_voice_name)
            torch.manual_seed(seed); torch.cuda.manual_seed_all(seed); sampler = Sampler(); sampler.start(); started = time.perf_counter()
            wavs, sr = model.generate_voice_clone(text=text, language=language, ref_audio=str(ref) if ref and prompt is None else None,
                ref_text=transcript, voice_clone_prompt=prompt, max_new_tokens=max_new_tokens, temperature=0.9,
                top_k=50, top_p=0.95, do_sample=True, repetition_penalty=1.05, xvec_only=False,
                non_streaming_mode=False, append_silence=True)
            torch.cuda.synchronize(); elapsed = time.perf_counter() - started; usage = sampler.finish(); runtime.cuda_graphs_captured = bool(model._warmed_up)
            wav = np.asarray(wavs[0]); duration = len(wav) / sr; name = output_name(text, f"seed{seed}"); target = FASTER_OUTPUTS / name; sf.write(target, wav, sr)
            return {"output_path": str(target), "audio_url": f"/audio/faster/{name}", "voice_prompt_id": prompt_name,
                    "voice_prompt_processing_seconds": round(prompt_seconds, 4), "total_seconds": round(elapsed, 4),
                    "audio_duration_seconds": round(duration, 4), "rtf": round(elapsed / duration, 4), **usage}
        except Exception as exc: raise HTTPException(500, str(exc)) from exc


@app.post("/clone-stream")
async def clone_stream(text: str = Form(...), language: str = Form("English"), reference_text: str = Form(""),
                       reference_audio_path: str | None = Form(None), reference_audio: UploadFile | None = File(None),
                       voice_prompt_id: str | None = Form(None), cache_voice_name: str | None = Form(None),
                       seed: int = Form(42), chunk_size: int = Form(2), max_new_tokens: int = Form(512)):
    ref = await reference_file(reference_audio, reference_audio_path); request_id = uuid.uuid4().hex
    def generate() -> Iterator[bytes]:
        with runtime.lock:
            sampler = Sampler(); chunks: list[np.ndarray] = []; arrivals: list[float] = []; started = time.perf_counter(); sampler.start()
            try:
                model = runtime.load(); prompt, transcript, prompt_seconds, prompt_name = resolve_prompt(model, ref, reference_text, voice_prompt_id, cache_voice_name)
                stream = model.generate_voice_clone_streaming(**generation_kwargs(text, language, transcript, ref, prompt, seed, max(1, chunk_size), max_new_tokens))
                first = None; sr = 24000
                yield wav_header(sr)
                for chunk, sr, timing in stream:
                    now = time.perf_counter(); first = first or now; arrivals.append(now); chunk = np.asarray(chunk).reshape(-1); chunks.append(chunk); yield pcm16(chunk)
                torch.cuda.synchronize(); finished = time.perf_counter(); usage = sampler.finish(); runtime.cuda_graphs_captured = bool(model._warmed_up)
                wav = np.concatenate(chunks) if chunks else np.array([], dtype=np.float32); name = output_name(text, f"stream_seed{seed}"); target = STREAM_OUTPUTS / name; sf.write(target, wav, sr)
                intervals = [b-a for a,b in zip(arrivals, arrivals[1:])]
                runtime.results[request_id] = {"request_id": request_id, "output_path": str(target), "audio_url": f"/audio/streaming-captured/{name}",
                    "voice_prompt_id": prompt_name, "voice_prompt_processing_seconds": round(prompt_seconds, 4),
                    "server_first_decoded_audio_seconds": round((first-started), 4) if first else None,
                    "total_seconds": round(finished-started, 4), "audio_duration_seconds": round(len(wav)/sr, 4),
                    "rtf": round((finished-started)/(len(wav)/sr), 4) if len(wav) else None,
                    "first_chunk_duration_seconds": round(len(chunks[0])/sr, 4) if chunks else None,
                    "chunk_count": len(chunks), "mean_chunk_interval_seconds": round(sum(intervals)/len(intervals), 4) if intervals else None,
                    "generation_faster_than_playback": (finished-started) < (len(wav)/sr) if len(wav) else False, **usage}
            except Exception as exc:
                runtime.results[request_id] = {"request_id": request_id, "error": str(exc)}; sampler.finish()
                raise
    return StreamingResponse(generate(), media_type="audio/wav", headers={"X-Request-ID": request_id, "X-Audio-Streaming": "decoded-pcm"})


@app.post("/benchmark")
async def benchmark(reference_text: str = Form(...), reference_audio_path: str | None = Form(None),
                    reference_audio: UploadFile | None = File(None), voice_name: str = Form("benchmark_voice"),
                    runs: int = Form(10), chunk_size: int = Form(2)) -> dict[str, Any]:
    ref = await reference_file(reference_audio, reference_audio_path)
    if ref is None: raise HTTPException(400, "Reference audio required")
    lines = ["Oh, come on. You cannot seriously believe that.", "Wait… what? No, that is actually brilliant.",
             "Yeah, I knew you were going to say that.", "I'm not angry. I'm just genuinely disappointed.",
             "Ha! Alright, you got me with that one.", "That is the strangest thing I've heard all week."]
    results = []
    with runtime.lock:
        try:
            model = runtime.load(); prompt, prompt_seconds = runtime.make_prompt(voice_name, ref, reference_text)
            for i in range(max(10, runs)):
                text = lines[i % len(lines)]; sampler = Sampler(); sampler.start(); started = time.perf_counter(); first = None; arrivals=[]; chunks=[]; sr=24000
                stream = model.generate_voice_clone_streaming(**generation_kwargs(text, "English", reference_text, None, prompt, 42+i, chunk_size))
                for chunk, sr, _ in stream:
                    now=time.perf_counter(); first=first or now; arrivals.append(now); chunks.append(np.asarray(chunk).reshape(-1))
                torch.cuda.synchronize(); finished=time.perf_counter(); usage=sampler.finish(); runtime.cuda_graphs_captured=bool(model._warmed_up)
                wav=np.concatenate(chunks); name=output_name(text,f"benchmark_run{i+1}_seed{42+i}"); sf.write(FASTER_OUTPUTS/name,wav,sr); intervals=[b-a for a,b in zip(arrivals,arrivals[1:])]
                results.append({"run":i+1,"text":text,"first_decoded_audio_seconds":round(first-started,4),"total_seconds":round(finished-started,4),
                    "audio_duration_seconds":round(len(wav)/sr,4),"rtf":round((finished-started)/(len(wav)/sr),4),"chunk_count":len(chunks),
                    "first_chunk_duration_seconds":round(len(chunks[0])/sr,4),"mean_chunk_interval_seconds":round(sum(intervals)/len(intervals),4) if intervals else None,
                    "generation_faster_than_playback":(finished-started)<(len(wav)/sr),"output_path":str(FASTER_OUTPUTS/name),**usage})
            summary={"model_load_seconds":runtime.load_seconds,"voice_prompt_creation_seconds":round(prompt_seconds,4),"idle_vram_mb":runtime.idle,
                "loaded_vram_mb":runtime.loaded_vram_mb,"cuda_graphs_captured":runtime.cuda_graphs_captured,"runs":len(results),
                "cold":results[0],"warm_mean_first_audio_seconds":round(sum(x["first_decoded_audio_seconds"] for x in results[1:])/len(results[1:]),4),
                "warm_mean_total_seconds":round(sum(x["total_seconds"] for x in results[1:])/len(results[1:]),4),
                "warm_mean_rtf":round(sum(x["rtf"] for x in results[1:])/len(results[1:]),4),"peak_vram_mb":max(x["peak_vram_mb"] for x in results)}
            report=BENCHMARKS/f"benchmark_{datetime.now():%Y%m%d-%H%M%S}.json"; report.write_text(json.dumps({"summary":summary,"results":results},indent=2),encoding="utf-8")
            return {"summary":summary,"results":results,"report_path":str(report)}
        except Exception as exc: raise HTTPException(500,str(exc)) from exc

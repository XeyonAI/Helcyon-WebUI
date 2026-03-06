import torch
print(f"CUDA available: {torch.cuda.is_available()}")
print(f"Device: {torch.cuda.get_device_name(0)}")

from TTS.api import TTS

tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cuda")

tts.tts_to_file(
    text="Hello, this is a test of XTTS version 2 running locally.",
    speaker_wav="test_voice.wav",
    language="en",
    file_path="test_output.wav"
)

print("✅ Done! Check test_output.wav")



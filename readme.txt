# Helcyon-WebUI + Benchmark (Free Edition)

A modern local AI workspace: chat, characters, memory, documents, web
search, and an integrated model-benchmarking workspace. See README.md
for the full feature list and the Free vs Pro breakdown.

## Requirements
- Python 3.11+
- A llama.cpp release (llama-server / llama-server.exe) for your platform
- 8GB+ VRAM recommended

## Installation

1. Extract/clone this repository to your desired location
2. Open a terminal in that folder
3. Run:
   Setup.bat
   (creates the virtual environment, installs dependencies, and sets up
   your models folder)
4. Download llama-server.exe from https://github.com/ggerganov/llama.cpp/releases
   and drop a .gguf model into C:\HWUI-Models
5. Start the app:
   START_UI.bat
6. Open your browser to: http://127.0.0.1:8081
7. In the Config page's Llama.cpp panel, point HWUI at your
   llama-server.exe and load your model.

## License

Helcyon-WebUI Free is licensed under the GNU General Public License
v3.0 — see LICENCE.txt. HWUI Pro is a separate paid product available
on Gumroad; it is not covered by this license.

## Support

For questions or issues, open an issue on GitHub.

## Version

This is the Free edition of Helcyon-WebUI + Benchmark. Pro adds the
Theme Editor, the Send Prompt / Capture Response workflow, automated
benchmarking, and benchmark session management.

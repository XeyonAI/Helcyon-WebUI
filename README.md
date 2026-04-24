# Helcyon-WebUI (HWUI) v1.0

**Professional AI chat interface for local LLMs**

A clean, powerful web interface designed specifically for [llama.cpp](https://github.com/ggerganov/llama.cpp) servers. Built to get out of the way and let you focus on conversations with your AI — no bloat, no unnecessary complexity.

Optimized for [Helcyon](https://huggingface.co/XeyonAI) models, but works beautifully with any ChatML-compatible local LLM.

---

## ✨ Features

### Free Version (This Repository)

- **Character Creator** — Build custom AI personas with full control over personality, tone, and behavior
- **Token Counter** — Real-time token tracking for messages and character cards
- **Character Switching** — Seamlessly switch between multiple characters mid-conversation
- **Custom User Persona** — Define your own user profile that carries across all chats
- **Random Opening Lines** — Characters greet you differently each time
- **Author's Note** — Add scene direction and tone adjustments on the fly
- **Chat Persistence** — All conversations auto-save to your local chats folder
- **Message Management** — Edit, delete, regenerate, or continue any message
- **Duplicate Chat** — Branch conversations to explore different paths
- **Streaming Responses** — Real-time token-by-token generation
- **Multi-line Support** — Shift+Enter for paragraph breaks
- **Time/Date Awareness** — Model knows the current time and date
- **Custom System Prompts** — Full control via `system_prompt.txt`
- **Markdown Rendering** — Full support for bold, italic, headers, bullet lists, separators and blockquotes
- 📁 **Project Folders** — Organise chats by topic and inject documents (PDF, DOCX, MD, TXT, ODT) directly into conversation context
- 🌐 **Web Search** — Real-time web search built in. Works just like Grok and ChatGPT — Helcyon searches and chats about the results. Compatible with Helcyon models (see [XeyonAI on HuggingFace](https://huggingface.co/XeyonAI))

### Pro Version (£25)

Everything in Free, plus:

- 🧠 **Memory System** — AI recalls and references past conversations across all chats. Your characters actually remember you.

👉 **[Get HWUI Pro on Gumroad](https://xeyonai.gumroad.com/l/bsmupk)**

---

## 🚀 Installation

### Requirements

- **Python 3.8+**
- **llama.cpp server** running locally (with a loaded model)
- **Recommended:** 8GB+ VRAM for decent performance

### Setup

1. **Clone this repository:**

```bash
git clone https://github.com/XeyonAI/Helcyon-WebUI.git
cd Helcyon-WebUI
```

2. **Install dependencies:**

```bash
pip install -r requirements.txt
```

Or run `Setup.bat`

3. **Start your llama.cpp server:**

Make sure you have a model loaded and llama.cpp server running.

Example llama.cpp command:

```bash
./llama-server -m /path/to/your/model.gguf -c 8192 --port 5000
```

4. **Configure HWUI:**

Edit `settings.json` to match your setup:
- `llama_server_url` - URL of your llama.cpp server (default: `http://localhost:5000`)
- `max_tokens` - Maximum response length
- `temperature`, `top_p`, `repeat_penalty` - Sampling parameters

5. **Run HWUI:**

**Windows:**
```bash
START_AI.bat
```

**Linux/Mac:**
```bash
python app.py
```

6. **Open your browser:**

Navigate to `http://localhost:8081`

---

## 🎯 Recommended Models

HWUI was built alongside the **Helcyon** model series — conversational local AI with presence, emotional intelligence, and zero corporate filter. Helcyon models are trained to use HWUI's web search natively, making the two genuinely better together.

Find all Helcyon models at [XeyonAI on HuggingFace](https://huggingface.co/XeyonAI).

HWUI also works great with any ChatML-compatible model:
- Mistral Nemo
- Qwen
- Llama 3
- Phi-4
- Any other instruct-tuned model that supports ChatML format

---

## 🛠️ Usage Tips

### Creating Characters

Use the **character creator** in Settings to build personas. Each character has:
- Main prompt (personality/style)
- Description & tagline
- Scenario context
- Example dialogue
- Author's notes for scene direction

### Opening Lines

Enable random greetings so your characters feel more dynamic. Each chat starts differently.

### Author's Note

Mid-conversation tone shifts? Use Author's Note to guide the next response:
- "Write in a more playful tone"
- "Keep responses under 3 paragraphs"
- "Focus on sensory details"

### Chat Branching

Duplicate any chat to explore alternate conversation paths without losing the original.

### Web Search

HWUI's web search works automatically when using a compatible Helcyon model. The model decides when to search — you just have a conversation. No commands, no toggles.

---

## 📂 File Structure

```
Helcyon-WebUI/
├── app.py                 # Main Flask application
├── chat_routes.py         # Chat management endpoints
├── extra_routes.py        # Character & user management
├── project_routes.py      # Projects & document handling
├── settings.json          # Configuration (edit this!)
├── system_prompt.txt      # Global system prompt
├── requirements.txt       # Python dependencies
├── START_AI.bat           # Windows launcher
├── characters/            # Character JSON files
├── character_cards/       # Exported character cards
├── users/                 # User persona data
├── chats/                 # Saved conversations
├── opening_lines/         # Random greeting text files
├── static/                # CSS, JS, images
└── templates/             # HTML templates
```

---

## 🔧 Troubleshooting

**"Connection refused" or server errors:**
- Make sure llama.cpp server is running
- Check `llama_server_url` in `settings.json` matches your llama.cpp server address
- Default is `http://localhost:5000` — adjust if your server uses a different port

**Characters not loading:**
- Ensure `/characters` folder has `.json` files
- Default character is "Cal" — check it exists

**Chats not saving:**
- Check `/chats` folder has write permissions

**Model responses are cut off:**
- Increase `max_tokens` in `settings.json`
- Adjust llama.cpp context size (`-c` parameter)

---

## 💡 Why HWUI?

Most local LLM interfaces are either:
- Overcomplicated with features you'll never use
- Designed for devs, not conversations
- Injecting weird templates that mess with model output

HWUI is different:
- **Clean output** — No weird prompts or formatting injections
- **Fast** — Lightweight Flask backend, vanilla JS frontend
- **Modular** — Easy to customise without breaking things
- **Respectful** — Your data stays local. No telemetry, no cloud, no BS.

---

## 📜 License

HWUI Free is licensed under the **GNU General Public License v3.0**.

This means you are free to:
- Use the software for any purpose
- Study and modify the source code
- Share the software with others
- Distribute modified versions

**However**, any modifications or derivative works must also be released under GPL v3.0.

HWUI Pro is available under a separate proprietary license. See [Gumroad](https://xeyonai.gumroad.com/l/bsmupk) for details.

---

© 2026 XeyonAI. All rights reserved.

---

## Support & Contributing

**Important:** This is a personal project released as-is.

### What I'll do:
- Fix critical bugs that affect core functionality
- Consider feature requests that align with my vision
- Review pull requests (no guarantee of merge)

### What I won't do:
- Provide installation tech support
- Implement features I don't personally need
- Answer general coding questions

**Want guaranteed support and advanced features?** → [HWUI Pro (£20)](https://xeyonai.gumroad.com/l/bsmupk) includes Memory and priority updates.

**Want to modify it yourself?** → Fork the repo! GPL v3 means you're free to build your own version.

---

## 🐛 Issues & Feedback

Found a bug? Have a feature request?

Open an issue on GitHub or reach out on [HuggingFace](https://huggingface.co/XeyonAI).

---

**Built by HardWire @ XeyonAI**  
Focus: Sovereign conversational AI with real emotional bandwidth.

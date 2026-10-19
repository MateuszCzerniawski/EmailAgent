# Ollama Setup Guide

Ollama lets you run large language models locally. Follow these steps to use it as an alternative to OpenAI in email Agent.

## 1. Install Ollama

**Option A — Installer (recommended)**
Download from https://ollama.com/download and run the installer.

**Option B — winget**
```
winget install Ollama.Ollama
```

After installation Ollama starts automatically as a Windows background service.

## 2. Verify Ollama is running

Open a terminal and run:
```
curl http://localhost:11434/api/tags
```
You should see a JSON response listing available models. If you get a connection error, start Ollama manually:
```
ollama serve
```

## 3. Pull a model

Recommended model — **qwen2.5:7b** (~4.7 GB, best structured JSON output):
```
ollama pull qwen2.5:7b
```

Alternative models:
| Model | Size | Notes |
|---|---|---|
| qwen2.5:7b | ~4.7 GB | Best JSON output, recommended |
| llama3.1:8b | ~5.0 GB | Strong reasoning, slightly less reliable JSON |
| qwen2.5:3b | ~2.0 GB | Faster, lower quality |

## 4. Hardware requirements

- **GPU (recommended):** 8 GB VRAM for 7B models — analysis runs in ~2-5 s
- **CPU fallback:** Works without a GPU but analysis takes 15-30 s per email
- **RAM:** 16 GB system RAM if running on CPU

## 5. Configure email Agent

In Settings -> LLM Provider:
1. Select **Ollama**
2. Set model name to `qwen2.5:7b` (or whichever model you pulled)
3. Leave the base URL as `http://localhost:11434` unless you changed it
4. Click **Save**

Then open any email and click **Re-analyse** to use the local model.

## Notes

- Switching providers takes effect immediately — no server restart needed
- Local models produce noticeably lower quality analysis than GPT-4o-mini
- The OpenAI API key is not required when using Ollama

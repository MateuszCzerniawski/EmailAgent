"""
Config router — exposes GET/PUT /config/llm for runtime provider switching
and GET /config/ollama-guide for serving the Ollama setup guide.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.config import CONFIG_PATH, AppConfig
from app.dependencies import get_config

router = APIRouter()

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_OLLAMA_GUIDE_PATH = _PROJECT_ROOT / "OLLAMA_SETUP.md"


class LlmConfigUpdate(BaseModel):
    """Partial update payload for PUT /config/llm. All fields are optional."""

    provider: str | None = None
    openai_model: str | None = None
    ollama_model: str | None = None
    ollama_base_url: str | None = None


def _persist_llm_config(config: AppConfig) -> None:
    """Write the current llm section of AppConfig back to config/config.yaml."""
    with CONFIG_PATH.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw.setdefault("llm", {})
    raw["llm"]["provider"] = config.llm.provider
    raw["llm"]["openai_model"] = config.llm.openai_model
    raw["llm"]["ollama_model"] = config.llm.ollama_model
    raw["llm"]["ollama_base_url"] = config.llm.ollama_base_url
    raw["llm"]["temperature"] = config.llm.temperature
    raw["llm"]["max_tokens"] = config.llm.max_tokens
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)


@router.get("/llm")
def get_llm_config(config: AppConfig = Depends(get_config)):
    """Return the current LLM provider settings (provider, models, base URL)."""
    return {
        "provider": config.llm.provider,
        "openai_model": config.llm.openai_model,
        "ollama_model": config.llm.ollama_model,
        "ollama_base_url": config.llm.ollama_base_url,
    }


@router.put("/llm")
def update_llm_config(body: LlmConfigUpdate, config: AppConfig = Depends(get_config)):
    """
    Update LLM provider settings at runtime without restarting the server.

    Mutates app.state.config in-place so the change is visible to the next
    LLMService instance (which is constructed per-request via Depends).
    Also writes the updated values back to config/config.yaml so they
    survive a restart.
    """
    if body.provider is not None:
        config.llm.provider = body.provider
    if body.openai_model is not None:
        config.llm.openai_model = body.openai_model
    if body.ollama_model is not None:
        config.llm.ollama_model = body.ollama_model
    if body.ollama_base_url is not None:
        config.llm.ollama_base_url = body.ollama_base_url
    _persist_llm_config(config)
    return {"ok": True}


@router.get("/ollama/models")
async def get_ollama_models(config: AppConfig = Depends(get_config)):
    """Probe Ollama and return installed model names. Never raises."""
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(f"{config.llm.ollama_base_url}/api/tags")
            r.raise_for_status()
            data = r.json()
            models = [m["name"] for m in (data.get("models") or [])]
            return {"running": True, "models": models}
    except Exception:
        return {"running": False, "models": []}


@router.get("/ollama-guide", response_class=PlainTextResponse)
def get_ollama_guide():
    """Serve the OLLAMA_SETUP.md installation guide as plain text."""
    if not _OLLAMA_GUIDE_PATH.exists():
        raise HTTPException(status_code=404, detail="OLLAMA_SETUP.md not found")
    return PlainTextResponse(_OLLAMA_GUIDE_PATH.read_text(encoding="utf-8"))

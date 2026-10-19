"""
Speech-to-text router — POST /stt/transcribe

Accepts a raw audio upload and transcribes it using OpenAI Whisper.
The API key is read from the OS keyring at request time so it is never
stored in the process environment. Mounted at /stt by main.py.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
import keyring
from openai import OpenAI

from app.dependencies import get_config

router = APIRouter()


@router.post("/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    lang: str = Form("pl"),
    config=Depends(get_config),
):
    """
    Transcribe an audio file using the OpenAI Whisper API.

    The upload is streamed into memory, forwarded to Whisper-1, and the
    resulting text is returned. Language defaults to Polish ("pl") but any
    ISO-639-1 code supported by Whisper is accepted via the ``lang`` form field.

    Raises:
        HTTPException 500: if the OpenAI API key is not configured in keyring,
            or if the Whisper API call itself fails.

    Returns:
        {"text": "<transcription string>"}
    """
    api_key = keyring.get_password(
        config.keyring.service_name,
        config.keyring.openai_key_name,
    )
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")

    audio_bytes = await audio.read()
    client = OpenAI(api_key=api_key)

    try:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=(audio.filename or "audio.webm", audio_bytes, audio.content_type or "audio/webm"),
            language=lang[:2],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"text": result.text}

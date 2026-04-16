import io
import base64
import openai
from config import settings

client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)


def synthesize_speech(text: str, voice: str = "alloy") -> str | None:
    """
    Convert text to speech using OpenAI TTS.
    Returns base64-encoded MP3 audio, or None on failure.

    Available voices: alloy, echo, fable, onyx, nova, shimmer
    """
    try:
        response = client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text,
            response_format="mp3",
        )
        audio_bytes = response.read()
        return base64.b64encode(audio_bytes).decode("utf-8")
    except Exception as e:
        print(f"[TTS] Error: {e}")
        return None

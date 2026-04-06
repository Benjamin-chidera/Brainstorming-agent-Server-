import openai
from config import settings

client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)


def transcribe_audio(audio_file_path: str) -> str:
    """
    Transcribe an audio file using OpenAI Whisper.
    Returns the transcribed text, or an empty string on failure.
    """
    try:
        with open(audio_file_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
            )
        return result.text.strip()
    except Exception as e:
        print(f"[Transcription] Error: {e}")
        return ""

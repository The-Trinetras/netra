"""Deepgram recognition adapter boundary (deepgram-sdk==7.8.1 per runtime baseline).

Not implemented in this build. Server-side recognition needs an approved
client-to-server microphone audio protocol (none is committed; the binary
audio framing contract is server-to-client only), the SDK installed, and an
authorized live check. Until then turns arrive only as final turn.submit
text, and interim transcripts cannot create turns or commands.

An implementation must yield netra_api.speech.recognition.TranscriptEvent
values and pass them through accept_final_transcript before any turn exists.
"""

from __future__ import annotations

from netra_api.platform.errors import ProviderUnavailableError
from netra_api.speech.recognition import RecognitionAdapter


def build_deepgram_recognizer(**_: object) -> RecognitionAdapter:
    raise ProviderUnavailableError("the Deepgram recognition adapter is not available in this build")

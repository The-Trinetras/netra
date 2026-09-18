"""ElevenLabs synthesis adapter boundary (elevenlabs==2.65.0 per runtime baseline).

Not implemented in this build: the SDK is not installed in the verified
environment, its 2.x streaming interface has not been checked against the
pinned version, and live provider calls are not authorized. Composition
therefore registers no synthesizer and delivers text only.

When the SDK is installed and a bounded live check is authorized, implement
``SpeechSynthesizer`` (netra_api.speech.synthesis) here: convert SDK chunks to
raw bytes, never return SDK objects, keep model/voice/output format explicit
in ``SynthesisConfig``, and raise ProviderUnavailableError on provider failure.
"""

from __future__ import annotations

from netra_api.platform.errors import ProviderUnavailableError
from netra_api.speech.synthesis import SpeechSynthesizer


def build_elevenlabs_synthesizer(**_: object) -> SpeechSynthesizer:
    raise ProviderUnavailableError("the ElevenLabs synthesis adapter is not available in this build")

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Voice input: the transcription route, its WAV checks, the Nemotron client and the optional cleanup."""

from __future__ import annotations

import asyncio
import io
import json
import threading
import wave
from types import SimpleNamespace
from typing import Any

import grpc
import httpx
import pytest

from demo_api.speech.cleanup import TranscriptCleanup
from demo_api.speech.cleanup import is_deletion_only
from demo_api.speech.nemotron import NemotronTranscriber
from demo_api.speech.service import SpeechProviderTimeoutError
from demo_api.speech.service import SpeechService
from demo_api.speech.service import SpeechSettings
from demo_api.speech.service import SpeechUnavailableError

ON = SpeechSettings(enabled=True, api_key="nvapi-test", max_seconds=2, max_concurrent=1)
WAV = {"content-type": "audio/wav"}


def wav(seconds: float = 0.5, *, channels: int = 1, rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(channels)
        recording.setsampwidth(2)
        recording.setframerate(rate)
        recording.writeframes(b"\x01\x00" * int(seconds * rate) * channels)
    return buffer.getvalue()


class FakeTranscriber:
    def __init__(self, text: str = "  which assets   led returns ", gate: threading.Event | None = None) -> None:
        self.text, self.gate, self.audio = text, gate, []

    def transcribe(self, pcm_audio: bytes) -> str:
        self.audio.append(pcm_audio)
        if self.gate:
            self.gate.wait(5)
        return self.text


async def test_voice_input_is_off_by_default(api):
    response = await api.post("/v1/speech/transcriptions", content=wav(), headers=WAV)

    assert response.status_code == 503


async def test_a_recording_is_transcribed(app, api):
    transcriber = FakeTranscriber()
    app.state.services.speech = SpeechService(ON, transcriber)

    response = await api.post("/v1/speech/transcriptions", content=wav(), headers=WAV)

    assert response.json() == {"text": "which assets led returns"}
    assert len(transcriber.audio[0]) == 8_000 * 2  # the PCM frames, without the WAV header


@pytest.mark.parametrize(
    ("body", "headers", "status"),
    [
        (wav(), {"content-type": "audio/mpeg"}, 415),
        (b"not a wav file", WAV, 422),
        (wav(channels=2), WAV, 422),
        (wav(rate=44_100), WAV, 422),
        (wav(seconds=3), WAV, 413),
        (b"", WAV, 422),
    ],
)
async def test_only_short_16khz_mono_wav_is_accepted(app, api, body, headers, status):
    app.state.services.speech = SpeechService(ON, FakeTranscriber())

    response = await api.post("/v1/speech/transcriptions", content=body, headers=headers)

    assert response.status_code == status


async def test_silence_is_not_recognized_and_a_second_recording_waits_its_turn(app, api):
    app.state.services.speech = SpeechService(ON, FakeTranscriber(text="   "))
    assert (await api.post("/v1/speech/transcriptions", content=wav(), headers=WAV)).status_code == 422

    gate = threading.Event()
    app.state.services.speech = SpeechService(ON, FakeTranscriber(gate=gate))
    first = asyncio.create_task(api.post("/v1/speech/transcriptions", content=wav(), headers=WAV))
    await asyncio.sleep(0.2)
    busy = await api.post("/v1/speech/transcriptions", content=wav(), headers=WAV)
    gate.set()

    assert busy.status_code == 429
    assert (await first).status_code == 200


def test_nemotron_streams_100ms_chunks_and_keeps_final_results() -> None:
    sent: dict[str, Any] = {}

    class Stub:
        def StreamingRecognize(self, requests, metadata, timeout):  # noqa: N802 - the gRPC method name
            sent["chunks"] = list(requests)
            sent["metadata"], sent["timeout"] = metadata, timeout
            final = SimpleNamespace(is_final=True, alternatives=[SimpleNamespace(transcript="Which assets led ")])
            interim = SimpleNamespace(is_final=False, alternatives=[SimpleNamespace(transcript="Which")])
            return [SimpleNamespace(results=[interim, final]), SimpleNamespace(results=[])]

    client = SimpleNamespace(
        Auth=lambda **kwargs: SimpleNamespace(get_auth_metadata=lambda: kwargs["metadata_args"], **kwargs),
        StreamingRecognitionConfig=lambda **kwargs: kwargs,
        RecognitionConfig=lambda **kwargs: kwargs,
        AudioEncoding=SimpleNamespace(LINEAR_PCM="LINEAR_PCM"),
        ASRService=lambda auth: SimpleNamespace(stub=Stub()),
    )
    transcriber = NemotronTranscriber(ON, client=client, request_factory=lambda chunks, config: chunks)

    assert transcriber.transcribe(b"\x00" * 7_000) == "Which assets led"
    assert [len(chunk) for chunk in sent["chunks"]] == [3_200, 3_200, 600]
    assert ["function-id", ON.function_id] in sent["metadata"]
    assert ["authorization", "Bearer nvapi-test"] in sent["metadata"]


@pytest.mark.parametrize(
    ("code", "error"),
    [
        (grpc.StatusCode.UNAUTHENTICATED, SpeechUnavailableError),
        (grpc.StatusCode.DEADLINE_EXCEEDED, SpeechProviderTimeoutError),
    ],
)
def test_grpc_failures_become_display_safe_errors(code, error) -> None:
    class Failure(grpc.RpcError):
        def code(self):
            return code

        def details(self):
            return "provider detail that must not reach the browser"

    def refuse(**kwargs):
        raise Failure()

    client = SimpleNamespace(Auth=refuse)
    with pytest.raises(error) as raised:
        NemotronTranscriber(ON, client=client).transcribe(b"\x00" * 100)
    assert "provider detail" not in str(raised.value)


def test_the_cleanup_may_only_delete_words() -> None:
    assert is_deletion_only("um which which assets uh led returns", "Which assets led returns?")
    assert not is_deletion_only("which assets led returns", "which stocks led returns")
    assert not is_deletion_only("which assets led returns", "returns which assets led")
    assert not is_deletion_only("which assets led returns", "")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ('{"cleaned_text": "Which assets led returns?"}', "Which assets led returns?"),
        ('<think></think>{"cleaned_text": "Which assets led returns?"}', "Which assets led returns?"),
        ('{"cleaned_text": "Which stocks led returns?"}', "um which assets uh led returns"),
        ("I cannot help with that.", "um which assets uh led returns"),
    ],
)
def test_the_cleanup_keeps_the_raw_transcript_unless_the_answer_only_deletes(reply, expected) -> None:
    requests: list[httpx.Request] = []

    def complete(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    cleanup = TranscriptCleanup(
        "nvapi-test", "nvidia/nemotron-3-super-120b-a12b", transport=httpx.MockTransport(complete)
    )

    assert cleanup.clean("um which assets uh led returns") == expected
    body = json.loads(requests[0].content)
    assert str(requests[0].url) == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert (body["model"], body["temperature"]) == ("nvidia/nemotron-3-super-120b-a12b", 0)


def test_a_failed_cleanup_keeps_the_raw_transcript() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    cleanup = TranscriptCleanup("nvapi-test", "some/model", transport=httpx.MockTransport(fail))

    assert cleanup.clean("which assets led") == "which assets led"

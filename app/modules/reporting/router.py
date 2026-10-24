"""Authenticated draft transcription, independent of incident creation."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.requests import ClientDisconnect

from app.api.dependencies import get_current_user, get_transcription_service
from app.core.security import AuthenticatedUser
from app.modules.reporting.schemas import TranscriptionResponse
from app.modules.reporting.transcription.service import (
    InvalidAudio,
    TranscriptionError,
    TranscriptionService,
    UnsupportedAudio,
)
from app.schemas.report import LanguageHint

router = APIRouter(prefix="/reporting", tags=["reporting"])


@router.post(
    "/transcriptions",
    response_model=TranscriptionResponse,
    responses={
        401: {"description": "Missing or invalid bearer access token."},
        403: {"description": "Verified token lacks the authenticated-user role."},
        413: {"description": "Audio exceeds permitted size or duration."},
        415: {"description": "Unsupported audio media type or WAV encoding."},
        422: {"description": "Invalid audio, language hint, or no usable speech."},
        503: {"description": "Authentication, transcription, or capacity is unavailable."},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def transcribe_audio(
    request: Request,
    _user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    service: Annotated[TranscriptionService, Depends(get_transcription_service)],
    language_hint: Annotated[LanguageHint, Query()] = LanguageHint.UNKNOWN,
) -> TranscriptionResponse:
    """Resolve authentication before manually consuming the raw request stream."""
    try:
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "audio/wav":
            raise UnsupportedAudio
        length_header = request.headers.get("content-length")
        declared_length = None
        if length_header is not None:
            if not length_header.isascii() or not length_header.isdecimal():
                raise InvalidAudio
            try:
                declared_length = int(length_header)
            except ValueError:
                raise InvalidAudio from None
        return await service.transcribe(request.stream(), language_hint, declared_length)
    except ClientDisconnect:
        raise HTTPException(status_code=422, detail=InvalidAudio.detail) from None
    except TranscriptionError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None

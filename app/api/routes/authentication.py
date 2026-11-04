"""Email registration, password login, and protected transient NIC classification."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.dependencies import get_current_user
from app.core.config import Settings, get_settings
from app.core.security import AuthenticatedUser
from app.schemas.authentication import SignInRequest, SignInResponse, SignUpRequest
from app.schemas.nic import NICClassificationRequest, NICClassificationResponse
from app.services.authentication import AuthenticationService
from app.services.nic import NICValidationError, classify_nic

router = APIRouter(prefix="/authentication", tags=["authentication"])


@router.post("/sign-up", status_code=204, response_class=Response)
async def sign_up(
    request: SignUpRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Create an email account; NIC is checked in memory and never forwarded/stored."""
    await AuthenticationService(settings).post(
        "signup",
        {
            "email": request.email,
            "password": request.password.get_secret_value(),
            "data": {"name": request.name, "phone_number": request.phone_number},
        },
        signup=True,
    )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/sign-in", response_model=SignInResponse)
async def sign_in(
    request: SignInRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Return only the session fields the browser needs to establish its session."""
    data = await AuthenticationService(settings).post(
        "token?grant_type=password",
        {"email": request.email, "password": request.password.get_secret_value()},
    )
    try:
        session = SignInResponse.model_validate(data)
    except ValidationError:
        raise HTTPException(503, "Authentication is unavailable.") from None
    return JSONResponse(
        {
            "access_token": session.access_token.get_secret_value(),
            "refresh_token": session.refresh_token.get_secret_value(),
            "token_type": session.token_type,
            "expires_in": session.expires_in,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post(
    "/nic-classification",
    response_model=NICClassificationResponse,
    description="Parse the NIC gender indicator locally. Does not verify identity or eligibility.",
    responses={
        401: {"description": "Missing or invalid bearer access token."},
        403: {"description": "Verified token lacks the authenticated-user role."},
        503: {"description": "Authentication is unavailable."},
    },
)
def classify_nic_number(
    request: NICClassificationRequest,
    _user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> NICClassificationResponse:
    """Calculate on demand; never store the NIC or associate the result with an account."""
    try:
        return classify_nic(request.nic.get_secret_value())
    except NICValidationError as exc:
        # Use the shared redacted validation response, without retaining the NIC
        # in an error input, context, or exception message.
        raise RequestValidationError(
            [{"loc": ("body", "nic"), "msg": str(exc), "type": "value_error"}]
        ) from None

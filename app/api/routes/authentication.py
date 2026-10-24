"""Local NIC classification protected by the existing Supabase JWT dependency."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.exceptions import RequestValidationError

from app.api.dependencies import get_current_user
from app.core.security import AuthenticatedUser
from app.schemas.nic import NICClassificationRequest, NICClassificationResponse
from app.services.nic import NICValidationError, classify_nic

router = APIRouter(prefix="/authentication", tags=["authentication"])


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

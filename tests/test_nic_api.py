"""NIC route tests with synthetic JWTs and in-memory trusted public keys."""

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.api.routes.authentication import router
from app.core.config import Settings, get_settings
from app.core.errors import validation_error_handler
from app.core.security import AuthenticationUnavailable, SupabaseTokenVerifier

PATH = "/api/v1/authentication/nic-classification"
BASE_URL = "https://example.invalid"
JWKS_URL = f"{BASE_URL}/auth/v1/.well-known/jwks.json"
USER_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def signing_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def make_token(signing_key: ec.EllipticCurvePrivateKey) -> Callable[[dict | None], str]:
    def token(changes: dict | None = None) -> str:
        now = int(datetime.now(UTC).timestamp())
        return jwt.encode(
            {
                "iss": f"{BASE_URL}/auth/v1",
                "aud": "authenticated",
                "sub": USER_ID,
                "role": "authenticated",
                "exp": now + 3600,
                **(changes or {}),
            },
            signing_key,
            algorithm="ES256",
            headers={"kid": "synthetic-key"},
        )

    return token


@pytest.fixture
def application(
    monkeypatch: pytest.MonkeyPatch, signing_key: ec.EllipticCurvePrivateKey
) -> FastAPI:
    settings = Settings(_env_file=None, supabase_url=BASE_URL, supabase_jwks_url=JWKS_URL)
    verifier = SupabaseTokenVerifier(BASE_URL, JWKS_URL)
    public_key = jwt.PyJWK.from_dict(
        {
            **json.loads(jwt.algorithms.ECAlgorithm.to_jwk(signing_key.public_key())),
            "kid": "synthetic-key",
            "alg": "ES256",
            "use": "sig",
        }
    )
    # Exercise the real signature/claim verifier without retrieving live JWKS.
    monkeypatch.setattr(verifier, "_fetch_keys", lambda: {"synthetic-key": public_key})
    monkeypatch.setattr("app.api.dependencies.get_token_verifier", lambda *_: verifier)
    # Isolate the actual router from unrelated optional analytics dependencies.
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.dependency_overrides[get_settings] = lambda: settings
    return app


@pytest.fixture
def client(application: FastAPI) -> Iterator[TestClient]:
    with TestClient(application) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "nic,gender,nic_format",
    [
        ("910010000V", "male", "old"),
        ("915010000x", "female", "old"),
        ("199100100000", "male", "new"),
        (" \t199150100000\n", "female", "new"),
    ],
)
def test_authenticated_classification(
    client: TestClient, make_token: Callable, nic: str, gender: str, nic_format: str
) -> None:
    response = client.post(
        PATH, json={"nic": nic}, headers={"Authorization": f"Bearer {make_token()}"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "gender_indicator": gender,
        "nic_format": nic_format,
        "classification_method": "nic_format",
        "identity_verified": False,
    }
    assert nic.strip() not in response.text
    assert USER_ID not in response.text


@pytest.mark.parametrize(
    "nic", ["", "  ", "bad-nic", "199136700000", "199150000000", "199106000000"]
)
def test_invalid_nic_is_redacted_422(client: TestClient, make_token: Callable, nic: str) -> None:
    response = client.post(
        PATH, json={"nic": nic}, headers={"Authorization": f"Bearer {make_token()}"}
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "nic"]
    assert all(set(error) == {"loc", "msg", "type"} for error in response.json()["detail"])
    if nic.strip():
        assert nic not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"nic": None},
        {"nic": 199100100000},
        {"nic": []},
        {"nic": "910010000V", "verified": True},
    ],
)
def test_request_validation_is_redacted(
    client: TestClient, make_token: Callable, payload: dict
) -> None:
    response = client.post(PATH, json=payload, headers={"Authorization": f"Bearer {make_token()}"})

    assert response.status_code == 422
    assert all(set(error) == {"loc", "msg", "type"} for error in response.json()["detail"])
    assert "199100100000" not in response.text
    assert "910010000V" not in response.text


@pytest.mark.parametrize("header", [None, "Basic synthetic", "Bearer not-a-token"])
def test_authentication_is_required(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, header: str | None
) -> None:
    def unexpected_parse(*args):
        pytest.fail("NIC parsing must not run before authentication succeeds")

    monkeypatch.setattr("app.api.routes.authentication.classify_nic", unexpected_parse)
    headers = {} if header is None else {"Authorization": header}
    response = client.post(PATH, json={"nic": "910010000V"}, headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert "910010000V" not in response.text


@pytest.mark.parametrize(
    "changes,status_code",
    [
        ({"exp": 0}, 401),
        ({"iss": "https://other.invalid/auth/v1"}, 401),
        ({"aud": "other"}, 401),
        ({"sub": "not-a-uuid"}, 401),
        ({"role": "service_role"}, 403),
    ],
)
def test_verified_claims_are_required(
    client: TestClient, make_token: Callable, changes: dict, status_code: int
) -> None:
    response = client.post(
        PATH, json={"nic": "910010000V"}, headers={"Authorization": f"Bearer {make_token(changes)}"}
    )

    assert response.status_code == status_code
    assert "910010000V" not in response.text


def test_invalid_signature_is_rejected(client: TestClient, make_token: Callable) -> None:
    other_key = ec.generate_private_key(ec.SECP256R1())
    # Re-sign synthetic claims with an untrusted key and the same key ID.
    claims = jwt.decode(make_token(), options={"verify_signature": False})
    token = jwt.encode(claims, other_key, algorithm="ES256", headers={"kid": "synthetic-key"})

    response = client.post(
        PATH, json={"nic": "910010000V"}, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401


def test_authentication_unavailable_fails_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*args):
        raise AuthenticationUnavailable

    monkeypatch.setattr("app.api.dependencies.get_token_verifier", unavailable)
    response = client.post(
        PATH, json={"nic": "910010000V"}, headers={"Authorization": "Bearer synthetic"}
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication is unavailable."}


@pytest.mark.parametrize("nic", ["910010000V", "199199900000"])
def test_nic_and_token_are_not_logged(
    client: TestClient, make_token: Callable, caplog: pytest.LogCaptureFixture, nic: str
) -> None:
    token = make_token()
    with caplog.at_level("DEBUG"):
        response = client.post(
            PATH, json={"nic": nic}, headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code in (200, 422)
    assert nic not in response.text
    assert nic not in caplog.text
    assert token not in caplog.text


def test_openapi_marks_request_sensitive_and_route_authenticated(application: FastAPI) -> None:
    schema = application.openapi()

    assert schema["paths"][PATH]["post"]["security"] == [{"HTTPBearer": []}]
    assert schema["components"]["schemas"]["NICClassificationRequest"]["properties"]["nic"][
        "writeOnly"
    ]
    assert "nic" not in schema["components"]["schemas"]["NICClassificationResponse"]["properties"]


def test_route_is_registered_in_the_combined_application() -> None:
    pytest.importorskip("numpy", reason="Existing analytics dependency is not installed")
    pytest.importorskip("sklearn", reason="Existing analytics dependency is not installed")
    from app.main import create_app

    assert PATH in create_app().openapi()["paths"]

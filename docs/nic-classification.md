# Local NIC gender-indicator classification

`POST /api/v1/authentication/nic-classification` parses a Sri Lankan NIC locally.
It requires the existing verified Supabase bearer access token. It does not
register an account, verify identity, change account gender, or grant eligibility.
Report submission does not depend on this endpoint.

## Contract

The request has one required string field, `nic`. Surrounding whitespace is
accepted; internal separators are not. Unknown fields are rejected. The input
uses Pydantic `SecretStr` and is excluded from normal model representations.

Invented example with unchecked serial/check digits, not a real identity:

```http
POST /api/v1/authentication/nic-classification
Authorization: Bearer <authorized Supabase access token>
Content-Type: application/json

{"nic": "199150100000"}
```

HTTP 200:

```json
{
  "gender_indicator": "female",
  "nic_format": "new",
  "classification_method": "nic_format",
  "identity_verified": false
}
```

`gender_indicator` is `male` or `female`; `nic_format` is `old` or `new`.
The other two fields are fixed. No NIC, birth date, year, account identifier,
verification record, or eligibility decision is returned.

Invalid input returns the existing redacted HTTP 422 detail-list format:

```json
{
  "detail": [
    {
      "loc": ["body", "nic"],
      "msg": "NIC day code must be 001-366 or 501-866.",
      "type": "value_error"
    }
  ]
}
```

Missing/invalid authentication returns 401, a verified non-user role returns 403,
and unavailable authentication returns 503, using the shared dependency. No
privileged database credential is needed by this operation.

## Encoding and date validation

`app/services/nic.py:classify_nic` is reusable independently of HTTP. It returns
`NICClassificationResponse` or raises `NICValidationError` with a safe message.

- Old: nine ASCII digits and a case-insensitive V/X suffix; day field at positions 3-5.
- New: twelve ASCII digits; day field at positions 5-7 and a non-zero four-digit year.
- Codes 001-366 indicate male; codes 501-866 indicate female after removing 500.
  Zero, 367-500, and 867-999 are rejected.
- The ordinal calendar reserves February 29 every year: 061 is March 1 and 366
  remains valid in a common year. Do not replace this with a 365-day upper bound.
- Codes 060/560 require a possible leap year. Full years use Gregorian century
  rules, so 1900/2100 are not leap years and 2000 is. In the legacy format, YY
  divisible by four permits the leap-day slot. YY=00 is century-ambiguous and
  may mean 2000; the parser does not silently interpret it as 1900 and reject it.

References checked on 2026-10-23:

- [SAP country-specific validation rules](https://help.sap.com/docs/successfactors-employee-central/countryregion-specifics/validation-rules-sri-lanka)
  documents the code ranges, February 29 slot, and invalid non-leap birth date.
- [SAP KBA 2334505](https://userapps.support.sap.com/sap/support/knowledge/en/2334505)
  describes the old/new field positions and leap-calendar encoding. The full
  article may require login; relevant details were available in indexed content.
- [ICTA LIFe discussion paper on SLIN](https://life.gov.lk/index.php?Itemid=127&id=13&lang=en&option=com_content&view=article)
  describes the older layout, female offset, and two-digit century ambiguity.
  Indexed content was available; direct retrieval timed out during research.

These are format/implementation references, not authorization to verify NICs or
confirmation from the issuing register. Serial/check digits, actual issuance,
ownership, birth date, age, citizenship, actual gender, and eligibility are not
verified. No birth date is constructed. No age threshold or future-year policy
is imposed. Legacy century ambiguity cannot be resolved from the number alone.

## Privacy and verification

The number is processed in request memory only. Neither the NIC nor the result
is persisted, added to Auth metadata/JWTs, sent to an identity/AI provider, or
written to logs or analytics. Deployment proxies and tracing must also exclude
request bodies and Authorization headers. Avoid submitting real NICs to demos.

Focused offline tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_nic.py tests/test_nic_api.py
```

The route tests mount the production router and shared error handler, exercise
the existing JWT verifier with synthetic keys, and need no Supabase connection.
A separate combined-application registration test skips if the already-declared
NumPy/scikit-learn dependencies are unavailable. Running the complete application
still requires the repository's existing runtime dependencies and normal JWT
configuration; this feature adds no dependencies or configuration settings.

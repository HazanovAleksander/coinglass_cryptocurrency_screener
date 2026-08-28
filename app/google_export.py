"""Google Sheets export via an interactive OAuth2 web flow.

No ``GOOGLE_REFRESH_TOKEN`` is needed in ``.env``: the user authorizes once
through the browser; the resulting credentials (refresh token included) live
in the signed session cookie and are reused/refreshed on later exports.

Requires ``GOOGLE_CLIENT_ID`` and ``GOOGLE_CLIENT_SECRET`` in the environment.
In the Google Cloud Console the OAuth client ("Web application") must list the
callback among authorized redirect URIs, e.g.
``http://localhost:8081/api/export/google/callback``.
"""
from __future__ import annotations

import os
import traceback
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from .exports import COLUMNS, col_help

router = APIRouter()

# The dashboard runs on plain HTTP locally; allow the OAuth2 redirect there.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# When the dashboard is reached through a reverse proxy, a remote host, a
# domain, or a Docker-published port (anything other than the bare
# request host), the redirect URI derived from the request does not match
# the URI registered in Google Cloud Console, yielding `redirect_uri_mismatch`.
# Set this to the exact "Authorized redirect URI" to decouple it from the
# request host.
GOOGLE_REDIRECT_URI_ENV = "CG_GOOGLE_REDIRECT_URI"


def _redirect_uri(request: Request) -> str:
    override = os.environ.get(GOOGLE_REDIRECT_URI_ENV)
    if override:
        return override
    return str(request.url_for("google_callback"))


def _client_config() -> dict:
    return {
        "web": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def is_configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def credentials_to_dict(credentials: Credentials) -> dict:
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
    }


def _credentials_from_dict(creds_dict: dict) -> Credentials:
    d = dict(creds_dict)
    expiry_str = d.pop("expiry", None)
    creds = Credentials(**d)
    if expiry_str:
        creds.expiry = datetime.fromisoformat(expiry_str)
    return creds


def _needs_auth(request: Request) -> JSONResponse:
    request.session.pop("credentials", None)
    return JSONResponse(status_code=401, content={"auth_url": str(request.url_for("google_auth"))})


def _make_flow(request: Request, state: str | None = None) -> Flow:
    """Build an OAuth2 flow with PKCE disabled.

    google-auth-oauthlib >= 1.0 auto-enables PKCE (code_challenge in the
    consent URL). Google "Web application" clients reject PKCE and answer the
    consent screen with "Confirmation not sent. There was an error", so we
    turn it off explicitly.
    """
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
        redirect_uri=_redirect_uri(request),
    )
    flow.autogenerate_code_verifier = False
    flow.code_verifier = None
    return flow


@router.get("/api/export/google/auth", name="google_auth")
def google_auth(request: Request):
    """Start the OAuth2 consent flow; redirects the browser to Google."""
    if not is_configured():
        return JSONResponse(
            status_code=503,
            content={"detail": "Google OAuth not configured: set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET"},
        )
    flow = _make_flow(request)
    auth_url, state = flow.authorization_url(prompt="consent", access_type="offline")
    request.session["state"] = state
    return RedirectResponse(auth_url)


@router.get("/api/export/google/callback", name="google_callback")
def google_callback(request: Request, state: str | None = None, code: str | None = None, error: str | None = None):
    """OAuth2 redirect target; exchanges the code for credentials in the session."""
    if error:
        return HTMLResponse(f"<script>alert('Google auth error: {error}'); window.close();</script>")
    flow = _make_flow(request, state=request.session.get("state"))
    authorization_response = str(request.url)
    if authorization_response.startswith("http://") and "localhost" not in authorization_response:
        authorization_response = authorization_response.replace("http://", "https://", 1)
    flow.fetch_token(authorization_response=authorization_response)
    request.session["credentials"] = credentials_to_dict(flow.credentials)
    return HTMLResponse("<script>window.close();</script>")


def create_sheet(request: Request, symbol: str, timeframe: str, rows: list[dict],
                 columns: list[str] | None = None) -> JSONResponse:
    """Create a Google Spreadsheet from cached rows using session credentials.

    ``columns`` (default :data:`exports.COLUMNS`) selects the exported columns
    and drives the header notes. Returns ``200 {"url": ...}`` on success,
    ``401 {"auth_url": ...}`` when the user must (re)authorize, or ``500`` on
    an unexpected error.
    """
    cols = columns or COLUMNS
    creds_dict = request.session.get("credentials")
    if not creds_dict:
        return _needs_auth(request)
    try:
        creds = _credentials_from_dict(creds_dict)
        if creds.refresh_token and (not creds.valid or not creds.expiry):
            try:
                creds.refresh(GoogleAuthRequest())
                request.session["credentials"] = credentials_to_dict(creds)
            except RefreshError:
                return _needs_auth(request)
        elif not creds.valid:
            return _needs_auth(request)

        service = build("sheets", "v4", credentials=creds)
        title = f"CoinGlass {symbol} dashboard {timeframe} {datetime.now(timezone.utc):%Y-%m-%d %H:%M}"
        spreadsheet = service.spreadsheets().create(
            body={"properties": {"title": title}},
            fields="spreadsheetId,spreadsheetUrl",
        ).execute()
        spreadsheet_id = spreadsheet.get("spreadsheetId")
        values = [cols] + [
            [r.get(c) if r.get(c) is not None else "" for c in cols] for r in rows
        ]
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range="A1",
            valueInputOption="RAW",
            body={"values": values},
        ).execute()
        # Header-cell notes (comments shown on hover in Google Sheets).
        # Notes are set via repeatCell with the "note" field (CellData.note).
        note_requests = []
        for j, c in enumerate(cols):
            help_text = col_help(c)
            if help_text:
                note_requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": 0,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": j,
                            "endColumnIndex": j + 1,
                        },
                        "cell": {"note": help_text},
                        "fields": "note",
                    }
                })
        if note_requests:
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": note_requests},
            ).execute()
        return JSONResponse(content={"url": spreadsheet.get("spreadsheetUrl")})
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(exc)})

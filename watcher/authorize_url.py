"""Print the OAuth consent URL instead of silently opening a browser.

drive_api.py's run_local_server() auto-opens a browser and buffers its prompt,
so when the browser lands on the wrong Google profile there is no visible link
to retry with. This prints the URL, then waits on the same localhost callback.

Usage:  python authorize_url.py
"""
from __future__ import annotations

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from drive_api import CLIENT_JSON, SCOPES, TOKEN_JSON


def main() -> None:
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_JSON), SCOPES)
    creds = flow.run_local_server(
        port=0,
        open_browser=False,
        # prompt=consent forces the account chooser even if a session exists,
        # so signing in as the Sierra account is always possible.
        prompt="consent",
        authorization_prompt_message="AUTH URL:\n{url}\n",
        success_message="Authorized. You can close this tab.",
    )
    TOKEN_JSON.write_text(creds.to_json(), encoding="utf-8")
    print("Authorized. token.json written.", flush=True)


if __name__ == "__main__":
    sys.exit(main())

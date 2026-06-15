"""
Robinhood MCP token manager.

Priority order for obtaining a valid access token:
  1. Claude Code credentials file (always fresh when running in-session)
  2. Refresh using MCP_REFRESH_TOKEN + MCP_CLIENT_ID env vars (GitHub Actions)
  3. Fall back to MCP_BEARER_TOKEN env var as-is

Call get_token() anywhere to get a guaranteed-fresh bearer token.
Call refresh_and_print() as a standalone script step in GitHub Actions.
"""
from __future__ import annotations

import datetime
import json
import os
import sys

import requests

CREDENTIALS_FILE = os.path.expanduser("~/.claude/.credentials.json")
TOKEN_URL        = "https://api.robinhood.com/oauth2/token/"
MCP_SERVER_URL   = "https://agent.robinhood.com/mcp/trading"

# Token is considered stale when fewer than this many seconds remain.
EXPIRY_BUFFER_SECS = 300   # 5 minutes


def _load_credentials_file() -> dict | None:
    """Read the Claude Code credentials file if present."""
    try:
        with open(CREDENTIALS_FILE) as f:
            d = json.load(f)
        servers = d.get("mcpOAuth", {})
        for key, cred in servers.items():
            if "robinhood-trading" in key:
                return cred
    except Exception:
        pass
    return None


def _token_valid(access_token: str) -> bool:
    """Quick liveness check against the MCP server."""
    import uuid
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0", "id": str(uuid.uuid4()),
        "method": "tools/list", "params": {},
    }
    try:
        r = requests.post(MCP_SERVER_URL, json=payload, headers=headers, timeout=10, stream=True)
        return r.status_code == 200
    except Exception:
        return False


def _refresh(refresh_token: str, client_id: str) -> dict | None:
    """Exchange a refresh token for a new access token."""
    try:
        r = requests.post(
            TOKEN_URL,
            data={
                "grant_type":    "refresh_token",
                "refresh_token": refresh_token,
                "client_id":     client_id,
                "scope":         "internal",
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[token_manager] refresh failed: {e}", file=sys.stderr)
        return None


def get_token() -> str:
    """
    Return a valid MCP bearer token, refreshing automatically if needed.

    Never raises — returns empty string if no token can be obtained.
    """

    # 1. Claude Code credentials file (in-session, always has the freshest token)
    cred = _load_credentials_file()
    if cred:
        expires_ms  = int(cred.get("expiresAt", 0))
        expires_at  = datetime.datetime.fromtimestamp(expires_ms / 1000, tz=datetime.timezone.utc)
        now         = datetime.datetime.now(datetime.timezone.utc)
        remaining   = (expires_at - now).total_seconds()
        access      = cred.get("accessToken", "")

        if access and remaining > EXPIRY_BUFFER_SECS:
            return access

        # Token in file is stale — refresh it
        refresh  = cred.get("refreshToken", "")
        clientid = cred.get("clientId", "")
        if refresh and clientid:
            new = _refresh(refresh, clientid)
            if new and new.get("access_token"):
                _save_credentials_file(cred, new)
                return new["access_token"]

    # 2. GitHub Actions / CI: use env-var refresh token
    refresh  = os.environ.get("MCP_REFRESH_TOKEN", "")
    clientid = os.environ.get("MCP_CLIENT_ID", "LtLiNmbs9owbYfWgBlC68Z2V-claude")
    if refresh:
        new = _refresh(refresh, clientid)
        if new and new.get("access_token"):
            # Print new tokens so the caller can persist them
            print(f"[token_manager] refreshed via MCP_REFRESH_TOKEN; "
                  f"expires_in={new.get('expires_in',0)}s", file=sys.stderr)
            # Write to env file if path provided
            env_out = os.environ.get("TOKEN_OUTPUT_FILE", "")
            if env_out:
                with open(env_out, "w") as fh:
                    fh.write(f"MCP_BEARER_TOKEN={new['access_token']}\n")
                    fh.write(f"MCP_REFRESH_TOKEN={new.get('refresh_token', refresh)}\n")
            return new["access_token"]

    # 3. Raw env var (may be expired — caller will see auth errors)
    return os.environ.get("MCP_BEARER_TOKEN", "")


def _save_credentials_file(old_cred: dict, new_tokens: dict) -> None:
    """Write refreshed tokens back to the Claude Code credentials file."""
    try:
        with open(CREDENTIALS_FILE) as f:
            d = json.load(f)
        servers = d.get("mcpOAuth", {})
        for key in servers:
            if "robinhood-trading" in key:
                servers[key]["accessToken"]  = new_tokens["access_token"]
                servers[key]["refreshToken"] = new_tokens.get("refresh_token",
                                                              old_cred.get("refreshToken", ""))
                expires_in = new_tokens.get("expires_in", 345600)
                import time
                servers[key]["expiresAt"] = str(int((time.time() + expires_in) * 1000))
                break
        with open(CREDENTIALS_FILE, "w") as f:
            json.dump(d, f, indent=2)
    except Exception as e:
        print(f"[token_manager] could not save credentials file: {e}", file=sys.stderr)


def refresh_and_print() -> None:
    """
    Standalone entry point for CI.
    Reads MCP_REFRESH_TOKEN + MCP_CLIENT_ID, refreshes, prints:
        NEW_ACCESS_TOKEN=<value>
        NEW_REFRESH_TOKEN=<value>
    so the caller can capture and store them as secrets.
    """
    refresh  = os.environ.get("MCP_REFRESH_TOKEN", "")
    clientid = os.environ.get("MCP_CLIENT_ID", "LtLiNmbs9owbYfWgBlC68Z2V-claude")
    if not refresh:
        print("ERROR: MCP_REFRESH_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    new = _refresh(refresh, clientid)
    if not new or not new.get("access_token"):
        print("ERROR: refresh failed", file=sys.stderr)
        sys.exit(1)
    print(f"NEW_ACCESS_TOKEN={new['access_token']}")
    print(f"NEW_REFRESH_TOKEN={new.get('refresh_token', refresh)}")
    hours = new.get("expires_in", 0) // 3600
    print(f"# expires in {hours}h", file=sys.stderr)


if __name__ == "__main__":
    refresh_and_print()

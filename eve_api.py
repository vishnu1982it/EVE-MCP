from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx


@dataclass
class EveClient:
    """
    Minimal EVE-NG Community Edition API client.

    Notes for EVE-NG CE (as seen in your environment):
    - Login works when we send the body as a RAW JSON STRING using `data=...`
      (same as: curl -d '{"username":"..","password":".."}' ...).
    - For login, avoid forcing Content-Type: application/json (your EVE returned 500).
    - For DELETE lab, EVE expects header: Content-type: application/json (precondition),
      otherwise it may return 412 Precondition Failed.
    """
    base_url: str
    username: str
    password: str
    author: str = "MCP"
    description: str = "Created by MCP"

    _client: Optional[httpx.Client] = None
    _default_folder: Optional[str] = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        # Keep a persistent client so cookies from login are reused for all API calls
        self._client = httpx.Client(base_url=self.base_url, timeout=30.0)

    def login(self) -> None:
        """
        Login to EVE-NG (cookie-based).

        Working pattern (matches your successful test):
            client.post('/api/auth/login', data='{"username":"...","password":"..."}')
        """
        assert self._client is not None

        payload = json.dumps({"username": self.username, "password": self.password})

        # IMPORTANT: Use data=payload (raw JSON string) and DO NOT set Content-Type
        resp = self._client.post("/api/auth/login", data=payload)

        if resp.status_code != 200:
            raise RuntimeError(f"Login failed HTTP {resp.status_code}: {resp.text}")

        js = resp.json()
        if js.get("status") != "success":
            raise RuntimeError(f"Login failed: {js}")

        # Discover user's default folder (useful default for lab create/delete)
        info = self.get_auth()
        folder = info.get("data", {}).get("folder")
        if folder:
            self._default_folder = folder

    def get_auth(self) -> Dict[str, Any]:
        """
        Returns logged-in user details (requires session cookie).
        """
        assert self._client is not None
        resp = self._client.get("/api/auth")
        resp.raise_for_status()
        return resp.json()

    @property
    def default_folder(self) -> str:
        return self._default_folder or "/"

    def create_lab(
        self,
        name: str,
        folder_path: Optional[str] = None,
        version: str = "1",
    ) -> Dict[str, Any]:
        """
        Create a lab:
          POST /api/labs
        Body is JSON.

        name: lab name without .unl
        folder_path: folder like "/" or "/<yourfolder>"
        """
        assert self._client is not None
        folder_path = folder_path or self.default_folder

        payload = {
            "path": folder_path,
            "name": name,
            "version": version,
            "author": self.author,
            "description": self.description,
            "body": "",
        }

        resp = self._client.post("/api/labs", json=payload)
        resp.raise_for_status()
        return resp.json()

    def delete_lab(self, name: str, folder_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Delete a lab:
          DELETE /api/labs/<folder>/<name>.unl

        IMPORTANT:
        - EVE may require header: Content-type: application/json for DELETE,
          otherwise it can return 412 Precondition Failed.
        - `name` can be passed with or without ".unl" (we normalize it).
        - Folder and filename are URL-encoded.
        """
        assert self._client is not None

        # Allow both "MyLab" and "MyLab.unl"
        lab_file = name if name.endswith(".unl") else f"{name}.unl"

        folder_path = (folder_path or self.default_folder).strip("/")

        encoded_folder = "/".join(
            quote(part, safe="") for part in folder_path.split("/") if part
        )
        encoded_file = quote(lab_file, safe="")

        if encoded_folder:
            url = f"/api/labs/{encoded_folder}/{encoded_file}"
        else:
            url = f"/api/labs/{encoded_file}"

        headers = {"Content-type": "application/json"}

        resp = self._client.delete(url, headers=headers)

        if resp.status_code >= 400:
            # Include response text so you instantly see the real reason from EVE
            raise RuntimeError(f"Delete failed HTTP {resp.status_code}: {resp.text}")

        return resp.json()

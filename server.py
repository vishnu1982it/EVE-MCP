from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from eve_api import EveClient

load_dotenv()

EVE_BASE_URL = os.getenv("EVE_BASE_URL", "").strip()
EVE_USERNAME = os.getenv("EVE_USERNAME", "").strip()
EVE_PASSWORD = os.getenv("EVE_PASSWORD", "").strip()
EVE_DEFAULT_AUTHOR = os.getenv("EVE_DEFAULT_AUTHOR", "MCP")
EVE_DEFAULT_DESCRIPTION = os.getenv("EVE_DEFAULT_DESCRIPTION", "Created by MCP")

if not (EVE_BASE_URL and EVE_USERNAME and EVE_PASSWORD):
    raise RuntimeError("Missing EVE_BASE_URL / EVE_USERNAME / EVE_PASSWORD in .env")

mcp = FastMCP("eve-ng-mcp")

eve = EveClient(
    base_url=EVE_BASE_URL,
    username=EVE_USERNAME,
    password=EVE_PASSWORD,
    author=EVE_DEFAULT_AUTHOR,
    description=EVE_DEFAULT_DESCRIPTION,
)
eve.login()


@mcp.tool()
def eve_create_lab(name: str, folder_path: Optional[str] = None) -> dict:
    """
    Create an EVE-NG lab in the given folder.
    - name: Lab name (without .unl)
    - folder_path: EVE folder path like "/User1" or "/" (optional; defaults to your user folder)
    """
    return eve.create_lab(name=name, folder_path=folder_path)


@mcp.tool()
def eve_delete_lab(name: str, folder_path: Optional[str] = None) -> dict:
    """
    Delete an EVE-NG lab from the given folder.
    - name: Lab name (without .unl)
    - folder_path: EVE folder path like "/User1" or "/" (optional; defaults to your user folder)
    """
    return eve.delete_lab(name=name, folder_path=folder_path)


if __name__ == "__main__":
    try:
        mcp.run()
    except KeyboardInterrupt:
        pass

"""SharePoint integration abstraction.

Currently only the Mock implementation is wired (filesystem-based simulation).
When real credentials are provided, swap the `get_client()` factory to return a
`GraphSharepointClient` that calls Microsoft Graph via msal + httpx.
"""
from __future__ import annotations
import os
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict


MOCK_ROOT = Path(os.environ.get("STORAGE_DIR", "/app/backend/storage")) / "sharepoint_mock"
MOCK_INPUT = MOCK_ROOT / "input"
MOCK_OUTPUT = MOCK_ROOT / "output"
MOCK_INPUT.mkdir(parents=True, exist_ok=True)
MOCK_OUTPUT.mkdir(parents=True, exist_ok=True)


class SharepointClient:
    """Abstract interface. Subclasses implement against real or mocked storage."""

    def list_input_files(self) -> List[Dict]:
        raise NotImplementedError

    def read_input_file(self, file_id: str) -> tuple[str, bytes]:
        """Return (filename, bytes)."""
        raise NotImplementedError

    def upload_output(self, municipio_name: str, filename: str, content: bytes) -> Dict:
        """Upload a PDF under /{municipio_name}/{YYYY-MM}/{filename}.
        Returns a dict with 'path' and 'web_url' (mocked)."""
        raise NotImplementedError


class MockSharepointClient(SharepointClient):
    def __init__(self, settings: Dict):
        self.settings = settings

    def list_input_files(self) -> List[Dict]:
        items: List[Dict] = []
        for p in sorted(MOCK_INPUT.glob("*.csv")):
            st = p.stat()
            items.append({
                "id": p.name,
                "name": p.name,
                "size": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            })
        return items

    def read_input_file(self, file_id: str) -> tuple[str, bytes]:
        # Prevent path traversal
        safe = Path(file_id).name
        path = MOCK_INPUT / safe
        if not path.exists():
            raise FileNotFoundError(file_id)
        return safe, path.read_bytes()

    def upload_output(self, municipio_name: str, filename: str, content: bytes) -> Dict:
        now = datetime.now(timezone.utc)
        ym = now.strftime("%Y-%m")
        safe_muni = _safe_folder(municipio_name)
        folder = MOCK_OUTPUT / safe_muni / ym
        folder.mkdir(parents=True, exist_ok=True)
        out_path = folder / filename
        out_path.write_bytes(content)
        rel_path = str(out_path.relative_to(MOCK_OUTPUT)).replace(os.sep, "/")
        return {
            "path": f"/Ayuntamientos/{rel_path}",
            "web_url": f"mock://sharepoint/{rel_path}",
        }


def _safe_folder(s: str) -> str:
    keep = "-_()"
    out = "".join(c if c.isalnum() or c in keep or c == " " else "_" for c in s).strip()
    return out or "sin_nombre"


def filename_for_output(municipio_name: str, when: Optional[datetime] = None) -> str:
    when = when or datetime.now(timezone.utc)
    date_str = when.strftime("%Y-%m-%d")
    safe = _safe_folder(municipio_name).replace(" ", "_")
    return f"{safe}_{date_str}.pdf"


def get_client(settings: Dict) -> SharepointClient:
    """Factory. Returns mock client when mock_mode=True or no credentials."""
    mock = settings.get("mock_mode", True)
    has_creds = all(settings.get(k) for k in ("tenant_id", "client_id", "client_secret", "site_url"))
    if mock or not has_creds:
        return MockSharepointClient(settings)
    # Real client to be implemented when credentials are wired.
    return MockSharepointClient(settings)

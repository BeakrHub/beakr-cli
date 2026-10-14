"""Binary downloads come back as bytes, never decoded as JSON.

Root cause guarded: ``files.preview_pdf`` was a JSON call, so a PDF raised a JSON
decode error; generated clients decoded zips the same way because the public
contract advertised JSON for binary routes.
"""

from __future__ import annotations

API = "https://api.test"
PDF = b"%PDF-1.7\x00\xff\xfe binary"

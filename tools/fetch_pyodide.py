"""Télécharge Pyodide (Python pour navigateur) pour la version web, avec vérification.

La version web fait tourner le moteur Python de l'outil dans le navigateur du planner :
ses fichiers sont publiés avec la page (dossier `preview/pyodide/`, jamais versionné).
Chaque fichier est vérifié par son empreinte (npm pour Pyodide, pyodide-lock.json pour
les bibliothèques).

    python tools/fetch_pyodide.py
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

VERSION = "314.0.7"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "preview" / "pyodide"
CORE_FILES = ("pyodide.js", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip",
              "pyodide-lock.json")  # fmt: skip
PACKAGES = ("pydantic", "pydantic-core", "typing-extensions", "annotated-types",
            "typing-inspection")  # fmt: skip


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 - URL fixe en https
        return resp.read()


def fetch() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = json.loads(_get(f"https://registry.npmjs.org/pyodide/{VERSION}"))
    tarball = _get(meta["dist"]["tarball"])
    algo, digest = meta["dist"]["integrity"].split("-", 1)
    if base64.b64encode(hashlib.new(algo, tarball).digest()).decode() != digest:
        raise SystemExit("Empreinte du paquet Pyodide incorrecte : téléchargement refusé.")
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        for name in CORE_FILES:
            member = tar.extractfile(f"package/{name}")
            if member is None:
                raise SystemExit(f"{name} absent du paquet Pyodide {VERSION}")
            (OUT / name).write_bytes(member.read())
    lock = json.loads((OUT / "pyodide-lock.json").read_text(encoding="utf-8"))
    for pkg in PACKAGES:
        info = lock["packages"][pkg]
        target = OUT / info["file_name"]
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != info["sha256"]:
            data = _get(f"https://cdn.jsdelivr.net/pyodide/v{VERSION}/full/{info['file_name']}")
            if hashlib.sha256(data).hexdigest() != info["sha256"]:
                raise SystemExit(f"Empreinte incorrecte pour {info['file_name']}")
            target.write_bytes(data)
    return OUT


def published_files() -> list[str]:
    """Fichiers à publier avec la page (chemins relatifs à preview/)."""
    lock = json.loads((OUT / "pyodide-lock.json").read_text(encoding="utf-8"))
    wheels = [lock["packages"][p]["file_name"] for p in PACKAGES]
    return [f"pyodide/{n}" for n in (*CORE_FILES, *wheels)]


if __name__ == "__main__":
    out = fetch()
    total = sum((ROOT / "preview" / f).stat().st_size for f in published_files())
    print(f"Pyodide {VERSION} prêt dans {out} ({total / 1e6:.1f} Mo à publier)")
    sys.exit(0)

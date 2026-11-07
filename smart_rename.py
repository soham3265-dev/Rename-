#!/usr/bin/env python3
"""
Smart rename by file content - DRY RUN by default.
For text/PDF: derives name from first-page / first-5-lines content.
For images: derives name from EXIF DateTimeOriginal if present.
For unknown: keeps stem + file mtime date.
"""
from pathlib import Path
import re
import sys
from datetime import datetime

DRY_RUN = False
TARGET_FOLDER = Path("/storage/emulated/0/Download")

TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".py"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}

MAX_NAME_LEN = 60  # stem length, without extension
SCRIPT_PATH = Path(__file__).resolve()


def slugify(text: str, fallback: str = "file") -> str:
    text = text.strip().splitlines()[0] if text.strip() else ""
    text = re.sub(r"[^A-Za-z0-9\s\-_]", " ", text)
    words = [w for w in re.split(r"\s+", text) if w]
    if not words:
        return fallback
    name = "_".join(words)
    return name[:MAX_NAME_LEN].strip("_") or fallback


def file_date(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return datetime.now().strftime("%Y-%m-%d")


def read_text_content(p: Path) -> str:
    try:
        lines = []
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for _ in range(5):
                line = f.readline()
                if not line:
                    break
                lines.append(line.strip())
        return " ".join(lines).strip()
    except Exception as e:
        print(f"  [warn] cannot read text {p.name}: {e}", file=sys.stderr)
        return ""


def read_pdf_content(p: Path) -> str:
    # Try PyPDF2 first, then pdfplumber
    try:
        import PyPDF2  # pip install PyPDF2

        with p.open("rb") as f:
            reader = PyPDF2.PdfReader(f)
            if reader.pages:
                return (reader.pages[0].extract_text() or "").strip()
    except ImportError:
        pass
    except Exception as e:
        print(f"  [warn] PyPDF2 failed for {p.name}: {e}", file=sys.stderr)

    try:
        import pdfplumber  # pip install pdfplumber

        with pdfplumber.open(p) as pdf:
            if pdf.pages:
                return (pdf.pages[0].extract_text() or "").strip()
    except ImportError:
        print("  [warn] Neither PyPDF2 nor pdfplumber installed", file=sys.stderr)
    except Exception as e:
        print(f"  [warn] pdfplumber failed for {p.name}: {e}", file=sys.stderr)
    return ""


def read_exif_date(p: Path) -> str:
    try:
        from PIL import Image  # pip install Pillow
        from PIL.ExifTags import TAGS

        with Image.open(p) as img:
            exif = img.getexif()
            if not exif:
                return ""
            for tag_id in (36867, 36868, 306):  # DateTimeOriginal, CreateDate, DateTime
                raw = exif.get(tag_id)
                if raw:
                    # Format "2023:05:01 12:30:00" -> "2023-05-01"
                    m = re.match(r"(\d{4}):(\d{2}):(\d{2})", str(raw))
                    if m:
                        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            # Fallback: search all tags by name
            for tag_id, value in exif.items():
                if TAGS.get(tag_id, "").lower() in ("datetimeoriginal", "datetime"):
                    m = re.match(r"(\d{4}):(\d{2}):(\d{2})", str(value))
                    if m:
                        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    except ImportError:
        print("  [warn] Pillow not installed", file=sys.stderr)
    except Exception as e:
        print(f"  [warn] EXIF read failed for {p.name}: {e}", file=sys.stderr)
    return ""


def propose_new_name(p: Path) -> tuple[str, str]:
    """Return (content_read_snippet, new_filename)"""
    ext = p.suffix.lower()
    content = ""

    if ext in TEXT_EXTS:
        content = read_text_content(p)
        stem = slugify(content, fallback=p.stem) if content else p.stem
        new_name = f"{stem}{p.suffix}"

    elif ext in PDF_EXTS:
        content = read_pdf_content(p)
        # First line of extracted text is usually title
        first_line = content.splitlines()[0] if content else ""
        stem = slugify(first_line, fallback=p.stem) if first_line else p.stem
        new_name = f"{stem}{p.suffix}"

    elif ext in IMAGE_EXTS:
        exif_date = read_exif_date(p)
        content = f"EXIF Date: {exif_date}" if exif_date else "no EXIF date"
        if exif_date:
            new_name = f"{exif_date}_{slugify(p.stem, fallback='image')}{p.suffix}"
        else:
            new_name = f"{slugify(p.stem, fallback='image')}_{file_date(p)}{p.suffix}"

    else:
        content = "unknown type"
        new_name = f"{p.stem}_{file_date(p)}{p.suffix}"

    snippet = (content[:80] + "...") if len(content) > 80 else content
    return snippet, new_name


def main() -> None:
    if not TARGET_FOLDER.is_dir():
        print(f"Target folder not found: {TARGET_FOLDER}")
        sys.exit(1)

    files = [
        p for p in sorted(TARGET_FOLDER.rglob("*"))
        if p.is_file() and p.resolve() != SCRIPT_PATH
    ]
    if not files:
        print(f"No files found in {TARGET_FOLDER}")
        return

    print(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE RUN'}")
    print(f"Target: {TARGET_FOLDER}\n")

    planned = set()

    for p in files:
        snippet, new_name = propose_new_name(p)
        # Handle collisions in dry-run planning
        candidate = p.parent / new_name
        if candidate in planned or (candidate.exists() and candidate != p):
            stem, suf = Path(new_name).stem, Path(new_name).suffix
            i = 1
            while (p.parent / f"{stem}_{i}{suf}") in planned or (p.parent / f"{stem}_{i}{suf}").exists():
                i += 1
            new_name = f"{stem}_{i}{suf}"
            candidate = p.parent / new_name
        planned.add(candidate)

        print(f"File: {p.name} -> Content read: '{snippet}' -> New Name: {new_name}")

        if not DRY_RUN and new_name != p.name:
            try:
                p.rename(candidate)
            except Exception as e:
                print(f"  [error] rename failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

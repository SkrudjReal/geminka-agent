#!/usr/bin/env python3
"""Search Pinterest and build a numbered contact sheet for a channel post."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps
from pinterest_dl import PinterestDL

_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_CANDIDATE_COUNT = 40
_SEARCH_COUNT = 200
_CELL_SIZE = (240, 300)
_COLUMNS = 5


def _used_urls(registry_path: Path) -> set[str]:
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    if not isinstance(data, dict) or not isinstance(data.get("used"), list):
        raise ValueError(f"Invalid post image registry: {registry_path}")
    return {
        item[key]
        for item in data["used"]
        if isinstance(item, dict)
        for key in ("url", "pin_url")
        if isinstance(item.get(key), str)
    }


def _make_contact_sheet(candidates: list[dict[str, Any]], output: Path) -> None:
    rows = (len(candidates) + _COLUMNS - 1) // _COLUMNS
    sheet = Image.new("RGB", (_COLUMNS * _CELL_SIZE[0], rows * _CELL_SIZE[1]), "white")
    draw = ImageDraw.Draw(sheet)

    for candidate in candidates:
        index = candidate["index"]
        image_path = Path(candidate.get("preview_path", candidate["path"]))
        with Image.open(image_path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((_CELL_SIZE[0] - 16, _CELL_SIZE[1] - 52))
        column, row = (index - 1) % _COLUMNS, (index - 1) // _COLUMNS
        left, top = column * _CELL_SIZE[0], row * _CELL_SIZE[1]
        x = left + (_CELL_SIZE[0] - image.width) // 2
        y = top + 36 + (_CELL_SIZE[1] - 52 - image.height) // 2
        sheet.paste(image, (x, y))
        draw.rounded_rectangle((left + 8, top + 7, left + 46, top + 32), radius=5, fill="#202124")
        draw.text((left + 20, top + 11), str(index), fill="white")
    sheet.save(output, format="JPEG", quality=88, optimize=True)


def _make_telegram_photo(source_path: Path, target_path: Path) -> None:
    with Image.open(source_path) as source:
        if getattr(source, "is_animated", False):
            source.seek(source.n_frames // 2)
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((2560, 2560))
        image.save(target_path, format="JPEG", quality=86, optimize=True)
        image.close()


def _video_preview(source: Path, target: Path) -> None:
    raw = subprocess.check_output(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                   '-show_entries', 'format=duration', '-of',
                                   'default=noprint_wrappers=1:nokey=1', str(source)], timeout=15)
    seconds = float(raw)
    if not 0 < seconds <= 600:
        raise ValueError('Video duration out of bounds')
    frames = []
    try:
        for index, fraction in enumerate((0.2, 0.5, 0.8)):
            frame = target.with_name(f'{target.stem}-{index}.jpg')
            frames.append(frame)
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', str(seconds * fraction),
                            '-protocol_whitelist', 'file,pipe', '-i', str(source), '-frames:v', '1',
                            '-vf', 'scale=320:320:force_original_aspect_ratio=decrease',
                            '-threads', '1', str(frame)], check=True, timeout=20,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sheet = Image.new('RGB', (960, 320), 'white')
        for index, frame in enumerate(frames):
            with Image.open(frame) as image:
                sheet.paste(image, (index * 320, 0))
        sheet.save(target)
    finally:
        for frame in frames:
            frame.unlink(missing_ok=True)


def select_images(query: str, job_id: str, output_dir: Path, registry_path: Path,
                  include_videos: bool = False) -> dict[str, Any]:
    if not _JOB_ID.fullmatch(job_id):
        raise ValueError("job_id must be a 32-character lowercase hex UUID")
    query = query.strip()
    if not query:
        raise ValueError("Pinterest search query cannot be empty")

    output_dir.mkdir(parents=True, exist_ok=True)
    used = _used_urls(registry_path)
    scraper = PinterestDL.with_api(timeout=20, verbose=False, max_retries=2)
    cookies = os.getenv("PINTEREST_COOKIES_FILE", "").strip()
    if cookies:
        scraper = scraper.with_cookies_path(Path(cookies).expanduser())
    # Paginate metadata first; do not download already-used pins.
    from itertools import islice

    pool = list(islice(scraper.iter_search(query, min_resolution=(512, 512), delay=0.4),
                       min(1000, _SEARCH_COUNT + len(used))))
    pool = [media for media in pool if getattr(media, 'src', None) not in used
            and getattr(media, 'origin', None) not in used
            and (include_videos or not getattr(media, 'video_stream', None))]
    random.shuffle(pool)
    # Reuse pinterest-dl's downloader, including its validation and retry behavior.
    medias = scraper._download_and_save(pool[:80], output_dir, include_videos, False, None, 'none') or []

    candidates = []
    seen: set[str] = set()
    for media in medias:
        url = getattr(media, "src", None)
        path = getattr(media, "local_path", None)
        if (
            not isinstance(url, str)
            or not url
            or url in used
            or getattr(media, "origin", None) in used
            or url in seen
            or not path
            or not Path(path).is_file()
        ):
            continue
        seen.add(url)
        is_video = bool(getattr(media, 'video_stream', None)) and Path(path).suffix.lower() == '.mp4'
        if Path(path).stat().st_size > 49 * 1024 * 1024:
            continue
        normalized_path = output_dir / f"preview_{len(candidates) + 1}.jpg"
        try:
            if is_video:
                _video_preview(Path(path), normalized_path)
            else:
                _make_telegram_photo(Path(path), normalized_path)
        except Exception:
            normalized_path.unlink(missing_ok=True)
            continue
        candidates.append(
            {
                "index": len(candidates) + 1,
                "url": url,
                "pin_url": getattr(media, "origin", None),
                "alt": getattr(media, "alt", None),
                "path": str(Path(path).resolve() if is_video else normalized_path.resolve()),
                "preview_path": str(normalized_path.resolve()),
                "kind": "video" if is_video else "photo",
                "resolution": list(getattr(media, "resolution", ())),
            }
        )
        if len(candidates) == _CANDIDATE_COUNT:
            break

    if not candidates:
        for media in medias:
            path = getattr(media, "local_path", None)
            if path and Path(path).is_file():
                Path(path).unlink()
        for path in output_dir.glob("preview_*.jpg"):
            path.unlink()
        raise RuntimeError("Pinterest returned no unused, downloadable images")

    selected_paths = {Path(candidate["path"]) for candidate in candidates}
    for media in medias:
        path = getattr(media, "local_path", None)
        if path and Path(path).is_file() and Path(path).resolve() not in selected_paths:
            Path(path).unlink()

    contact_sheet = output_dir / "contact-sheet.jpg"
    _make_contact_sheet(candidates, contact_sheet)
    return {
        "job_id": job_id,
        "query": query,
        "contact_sheet": str(contact_sheet.resolve()),
        "candidates": candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--include-videos", action='store_true')
    args = parser.parse_args()
    try:
        result = select_images(args.query, args.job_id, args.output_dir, args.registry, args.include_videos)
    except Exception as exc:
        print(f"Pinterest preview failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

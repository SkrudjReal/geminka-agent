#!/usr/bin/env python3
"""Rewrite emoji descriptions into pure poses, gestures, and actions without character names."""

import json
import re
from pathlib import Path
import httpx

INPUT_FILE = Path("data/honnkaistarrrraaailll_descriptions.txt")
OUTPUT_FILE = Path("data/honnkaistarrrraaailll_poses.txt")
OMP_URL = "http://127.0.0.1:4000/v1/chat/completions"


def process_chunk(items, start_idx, end_idx):
    chunk_text = "\n".join(items)
    prompt = f"""Ниже дан фрагмент из {len(items)} пронумерованных строк с описаниями эмодзи (#{start_idx}..#{end_idx}).
Твоя задача: переписать КАЖДУЮ из {len(items)} строк строго БЕЗ упоминания имён собственных и персонажей (никаких Кафка, Блэйд, Пом-Пом, Март 7, Ахерон, Авантюрин, Галлахер, Сампо, Аста, Сервал, Ху Тао, Пеппи, Часик, Дань Хэн, Цзин Юань, Топаз, Цзинлю, Гуйнайфэнь, Хохо, Аргенти, Ханья, Жуань Мэй, Рацио, Сюэи, Скрюллум, Черный Лебедь, Искорка, Миша, Клара, Гепард, Наташа, Лука, Байлу, Рысь, Сушан, Цинцюэ, Лоча, Вельт, Пела, Герта, Хук, Химеко, Вуббабу и др.).
Описывай ТОЛЬКО позу, мимику, жесты, предметы и действия.
Заменяй имена на общие описания: 'Девушка...', 'Парень...', 'Маленькое ушастое существо в фуражке...', 'Призрачный дух...', 'Мужчина в очках...' и т.д.

СТРОГИЕ ТРЕБОВАНИЯ:
1. Выдай РОВНО {len(items)} строк, по одной строке на каждый номер от {start_idx} до {end_idx}.
2. Формат каждой строки: "{start_idx}. Описание..."
3. НЕ объединяй строки, НЕ пропускай ни одного номера, НЕ пиши вступительный текст.

Исходные строки:
{chunk_text}
"""
    payload = {
        "model": "google-antigravity/gemini-3.8-flash",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    resp = httpx.post(OMP_URL, json=payload, timeout=60.0)
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text).strip()
    lines = [l.strip() for l in text.splitlines() if l.strip() and re.match(r"^\d+\.", l.strip())]
    if len(lines) != len(items):
        print(f"Warning: expected {len(items)} lines, got {len(lines)} for range {start_idx}..{end_idx}")
    return lines


def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    print(f"Loaded {len(lines)} lines from {INPUT_FILE}")
    chunk_size = 20
    all_rewritten = []

    for i in range(0, len(lines), chunk_size):
        chunk = lines[i : i + chunk_size]
        start_idx = i + 1
        end_idx = i + len(chunk)
        print(f"Processing chunk {start_idx}..{end_idx}...")
        rewritten_chunk = process_chunk(chunk, start_idx, end_idx)
        all_rewritten.extend(rewritten_chunk)

    print(f"Total rewritten lines: {len(all_rewritten)}")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(all_rewritten) + "\n")

    print(f"Saved {len(all_rewritten)} lines to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

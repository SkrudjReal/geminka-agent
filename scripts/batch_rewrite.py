#!/usr/bin/env python3
"""Concurrently rewrites all 200 descriptions to pure poses and actions without character names via JSON."""

import asyncio
import json
import re
from pathlib import Path
import httpx

INPUT_FILE = Path("data/honnkaistarrrraaailll_descriptions.txt")
OUTPUT_FILE = Path("data/honnkaistarrrraaailll_descriptions.txt")
OMP_URL = "http://127.0.0.1:4000/v1/chat/completions"


async def rewrite_batch(client: httpx.AsyncClient, items: list[str], start_idx: int, end_idx: int) -> list[dict]:
    items_text = "\n".join(items)
    prompt = f"""Ниже дан список из {len(items)} пронумерованных строк с описаниями эмодзи (индексы #{start_idx}..#{end_idx}).
Твоя задача: переписать КАЖДУЮ строчку строго БЕЗ упоминания имён собственных и имён персонажей (никаких Кафка, Блэйд, Пом-Пом, Март 7, Ахерон, Авантюрин, Галлахер, Сампо, Аста, Сервал, Ху Тао, Пеппи, Часик, Дань Хэн, Цзин Юань, Топаз, Цзинлю, Гуйнайфэнь, Хохо, Аргенти, Ханья, Жуань Мэй, Рацио, Сюэи, Скрюллум, Черный Лебедь, Искорка, Миша, Клара, Гепард, Наташа, Лука, Байлу, Рысь, Сушан, Цинцюэ, Лоча, Вельт, Пела, Герта, Хук, Химеко, Вуббабу и др.).

Описывай ТОЛЬКО позы, мимику, жесты, предметы и действия.
Заменяй имена на общие описания: 'Девушка...', 'Парень...', 'Маленькое существо в фуражке...', 'Призрачный дух...', 'Мужчина в очках...' и т.д.

Ответь ИСКЛЮЧИТЕЛЬНО валидным JSON следующего формата:
{{
  "items": [
    {{"index": {start_idx}, "text": "Описание позы и действия..."}}
  ]
}}

Список:
{items_text}
"""
    payload = {
        "model": "google-antigravity/gemini-3.8-flash",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    resp = await client.post(OMP_URL, json=payload, timeout=90.0)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()

    data = json.loads(raw)
    return data.get("items", [])


async def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        raw_lines = [l.strip() for l in f if l.strip()]

    # Make sure we have original base lines if already partially edited
    print(f"Loaded {len(raw_lines)} lines")

    batch_size = 25
    batches = []
    for i in range(0, len(raw_lines), batch_size):
        chunk = raw_lines[i : i + batch_size]
        batches.append((chunk, i + 1, i + len(chunk)))

    print(f"Splitting into {len(batches)} parallel batches of {batch_size}...")
    async with httpx.AsyncClient(timeout=90.0) as client:
        tasks = [rewrite_batch(client, chunk, s, e) for chunk, s, e in batches]
        results = await asyncio.gather(*tasks)

    all_items = []
    for res in results:
        all_items.extend(res)

    all_items.sort(key=lambda x: int(x["index"]))
    print(f"Total processed items: {len(all_items)}")

    out_lines = [f"{item['index']}. {item['text'].strip()}" for item in all_items]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")

    print(f"Successfully saved {len(out_lines)} lines to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())

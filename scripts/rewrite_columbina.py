#!/usr/bin/env python3
"""Concurrently rewrites all 200 emoji descriptions to feature Columbina across all items."""

import asyncio
import json
import re
from pathlib import Path
import httpx

INPUT_FILE = Path("data/original_quoted_with_names.txt")
OUTPUT_FILE = Path("data/honnkaistarrrraaailll_descriptions.txt")
OMP_URL = "http://127.0.0.1:4000/v1/chat/completions"


async def rewrite_batch(client: httpx.AsyncClient, items: list[str], start_idx: int, end_idx: int) -> list[dict]:
    items_text = "\n".join(items)
    prompt = f"""Ниже дан список из {len(items)} пронумерованных строк с описаниями эмодзи (номера #{start_idx}..#{end_idx}).
Твоя задача: заменить имена ВСЕХ персонажей, существ и маскотов на имя "Коломбина" (или "Чиби-Коломбина", если в оригинале указано чиби).
Каждый пункт должен описывать Коломбину!
Если в оригинале маскот/предмет/существо (кот, птица-оригами, Часик, мусорный бак и т.п.), опиши Коломбину в этом образе или действии (например, "Коломбина в образе круглой птицы-оригами...", "Коломбина весело приплясывает в праздничной соломенной шляпе...").

ГЛАВНЫЕ ПРАВИЛА:
1. Имя действующего лица во ВСЕХ пунктах — строго "Коломбина" или "Чиби-Коломбина". Никаких других персонажей (никаких Кафка, Блэйд, Пом-Пом, Март 7, Ахерон, Авантюрин, Сампо, Галлахер, Рацио и т.д.).
2. Согласование грамматики: строго женский род глаголов, прилагательных и местоимений (скрестила, посмотрела, надела, её, довольная, сердитая и т.д.).
3. Сохраняй все позы, жесты, мимику, предметы, надписи в кавычках (например, «OK», «Bye~», «START») и детали действий без потерь.
4. Ответь ИСКЛЮЧИТЕЛЬНО валидным JSON следующего формата:
{{
  "items": [
    {{"index": {start_idx}, "text": "Коломбина / Чиби-Коломбина..."}}
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
    items_out = data.get("items", [])
    if len(items_out) != len(items):
        print(f"Warning: expected {len(items)} items for range {start_idx}..{end_idx}, got {len(items_out)}")
    return items_out


async def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        raw_lines = [l.strip() for l in f if l.strip()]

    print(f"Loaded {len(raw_lines)} lines from {INPUT_FILE}")
    batch_size = 25
    batches = []
    for i in range(0, len(raw_lines), batch_size):
        chunk = raw_lines[i : i + batch_size]
        batches.append((chunk, i + 1, i + len(chunk)))

    print(f"Splitting into {len(batches)} parallel batches of {batch_size}...")
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = [rewrite_batch(client, chunk, s, e) for chunk, s, e in batches]
        results = await asyncio.gather(*tasks)

    all_items = []
    for res in results:
        all_items.extend(res)

    all_items.sort(key=lambda x: int(x["index"]))
    print(f"Total processed items: {len(all_items)}")

    # Check for missing indices
    indices = [int(x["index"]) for x in all_items]
    missing = [i for i in range(1, 201) if i not in indices]
    if missing:
        print(f"ERROR: Missing indices: {missing}")
        return

    out_lines = [f"{item['index']}. {item['text'].strip()}" for item in all_items]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")

    print(f"Successfully saved {len(out_lines)} lines to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())

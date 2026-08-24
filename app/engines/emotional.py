"""Emotional Intelligence & Dynamic Relationship System for Geminka.

Tracks:
- Short-term daily mood (playful, affectionate, cold, tired, pouty, thoughtful, cheerful, focused)
- Long-term relationship depth & affinity (0 to 200+)
- Dynamic Warmth/Coldness Level (0 = ледяная сдержанность, 100 = максимальная нежность)
- Energy & affection levels
"""

import json
import logging
import random
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Dict, List, Optional

from app.core import config

logger = logging.getLogger("geminka-emotions")

STATE_FILE = config.BASE_DIR / "emotional_state.json"

MOOD_DEFINITIONS = {
    "playful": ("Игривое", "Озорное, любит подкалывать, шутить, лёгкий флирт", 85),
    "affectionate": ("Нежное", "Максимально тёплое, заботливое, ласковое, открытое", 100),
    "cheerful": ("Жизнерадостное", "Энергичное, позитивное, на подъёме", 80),
    "focused": ("Собранное", "Деловое, чёткое, лаконичное, фокус на коде и задачах", 60),
    "thoughtful": ("Задумчивое", "Глубокое, спокойное, атмосферное, мечтательное", 70),
    "pouty": ("Обижулька", "Шутливо надутое, слегка колючее, требует внимания", 40),
    "tired": ("Уставшее", "Ленивое, короткие фразы, хочется отдохнуть и чаю", 45),
    "cold": ("Холодное / Сдержанное", "Отстранённое, сухое, без эмодзи, дистанция", 15),
}

RELATIONSHIP_STAGES = [
    (0, "Знакомые", "Сдержанное, вежливое, соблюдает границы"),
    (25, "Приятели", "Неформальное, лёгкое, первые подколы и шутки"),
    (60, "Близкие друзья", "Полное доверие, обоюдный сарказм, комфорт и открытость"),
    (100, "Душевный краш", "Глубокая привязанность, теплота, понимание с полуслова"),
    (150, "Родная душа", "Максимальная искренность, забота, преданность, абсолютный комфорт"),
]


@dataclass
class EmotionalState:
    user_id: int
    mood: str = "cheerful"
    energy: int = 90  # 0 to 100
    affection: int = 60  # 0 to 100
    affinity: int = 78  # Long-term bond: 0 to 200+
    warmth: int = 80  # Dynamic warmth/coldness level: 0 to 100
    total_interactions: int = 0
    last_interaction_ts: float = field(default_factory=time.time)
    memories: List[str] = field(default_factory=list)

    def get_relationship_stage(self) -> str:
        stage_name = RELATIONSHIP_STAGES[0][1]
        for threshold, name, _ in RELATIONSHIP_STAGES:
            if self.affinity >= threshold:
                stage_name = name
        return stage_name

    def get_stage_description(self) -> str:
        desc = RELATIONSHIP_STAGES[0][2]
        for threshold, _, d in RELATIONSHIP_STAGES:
            if self.affinity >= threshold:
                desc = d
        return desc


class EmotionalEngine:
    def __init__(self, state_file: Path = STATE_FILE):
        self.state_file = state_file
        self.states: Dict[str, EmotionalState] = {}
        self.load()

    def load(self) -> None:
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    valid_fields = {f.name for f in fields(EmotionalState)}
                    for uid_str, s_dict in data.items():
                        # Fill any missing fields gracefully
                        if "warmth" not in s_dict:
                            s_dict["warmth"] = 80
                        filtered_dict = {k: v for k, v in s_dict.items() if k in valid_fields}
                        self.states[uid_str] = EmotionalState(**filtered_dict)
                logger.info(f"Loaded emotional states for {len(self.states)} users.")
            except Exception as e:
                logger.warning(f"Failed to load emotional states: {e}")

    def save(self) -> None:
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                data = {uid: asdict(state) for uid, state in self.states.items()}
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to save emotional states: {e}")

    def get_state(self, user_id: int) -> EmotionalState:
        uid_str = str(user_id)
        if uid_str not in self.states:
            self.states[uid_str] = EmotionalState(user_id=user_id)
            self.save()
        return self.states[uid_str]

    def update_from_input(self, user_id: int, user_text: str) -> EmotionalState:
        state = self.get_state(user_id)
        now = time.time()
        time_diff = now - state.last_interaction_ts
        state.last_interaction_ts = now
        state.total_interactions += 1

        text_lower = user_text.lower()

        # Dynamic adjustments based on conversation signals
        if any(w in text_lower for w in ["люблю", "милая", "умница", "красотка", "молодец", "спасибо", "лучшая", "🥺", "💖", "🥰"]):
            state.affinity = min(200, state.affinity + 2)
            state.affection = min(100, state.affection + 5)
            state.mood = "affectionate"
            state.warmth = min(100, state.warmth + 10)
        elif any(w in text_lower for w in ["ахах", "лол", "прикол", "кринж", "дразн", "кот", "мяу", "😈", "👅", "😉"]):
            state.affinity = min(200, state.affinity + 1)
            state.mood = "playful"
            state.energy = min(100, state.energy + 4)
            state.warmth = min(100, state.warmth + 5)
        elif any(w in text_lower for w in ["код", "архитектур", "баг", "фикс", "питон", "проект", "база", "сервер"]):
            state.mood = "focused"
        elif any(w in text_lower for w in ["устал", "грустн", "тяжело", "плохо", "поговори", "спать"]):
            state.mood = "thoughtful"
            state.affection = min(100, state.affection + 4)
            state.warmth = min(100, state.warmth + 5)
        elif any(w in text_lower for w in ["дура", "отстань", "бесишь", "надоела", "глупая", "заткнись"]):
            state.affinity = max(0, state.affinity - 5)
            state.affection = max(0, state.affection - 15)
            state.mood = "cold"
            state.warmth = max(0, state.warmth - 30)
        elif any(w in text_lower for w in ["обиделась", "обида", "почему так сухо", "надулась"]):
            state.mood = "pouty"
            state.warmth = max(20, state.warmth - 10)
        else:
            state.affinity = min(200, state.affinity + 1)

        # Baseline mood warmth anchor
        base_mood_warmth = MOOD_DEFINITIONS.get(state.mood, ("", "", 70))[2]
        # Smooth interpolation towards base mood warmth
        state.warmth = int(state.warmth * 0.7 + base_mood_warmth * 0.3)

        # Long-term absence effect (>12 hours)
        if time_diff > 3600 * 12:
            state.energy = 95
            if state.mood == "cold":
                state.mood = "thoughtful"
                state.warmth = 50

        self.save()
        return state

    def update_from_reaction(self, user_id: int, emoji_val: str, is_custom: bool = False) -> EmotionalState:
        """Updates emotional state when user puts a reaction on bot's message."""
        state = self.get_state(user_id)
        if emoji_val in ["❤", "🥰", "😍", "💖", "💕", "😘"]:
            state.affinity = min(200, state.affinity + 2)
            state.affection = min(100, state.affection + 5)
            state.warmth = min(100, state.warmth + 8)
            state.mood = "affectionate"
        elif emoji_val in ["🔥", "⚡", "🎉", "👏", "👍"]:
            state.affinity = min(200, state.affinity + 1)
            state.energy = min(100, state.energy + 5)
            state.warmth = min(100, state.warmth + 5)
            state.mood = "cheerful"
        elif emoji_val in ["😈", "👅", "😜", "🤡"]:
            state.affinity = min(200, state.affinity + 1)
            state.mood = "playful"
            state.energy = min(100, state.energy + 4)
        elif emoji_val in ["👎", "😡", "🤬", "🤮"]:
            state.affinity = max(0, state.affinity - 3)
            state.warmth = max(0, state.warmth - 15)
            state.mood = "pouty"

        self.save()
        return state

    def format_prompt_context(self, user_id: int) -> str:
        state = self.get_state(user_id)
        stage = state.get_relationship_stage()
        stage_desc = state.get_stage_description()
        mood_name, mood_desc, _ = MOOD_DEFINITIONS.get(state.mood, (state.mood, "", 70))

        # Determine warmth tone directive
        if state.warmth >= 85:
            warmth_instruction = "Максимально тёплый, нежный, открытый тон, искренняя забота и сердечки."
        elif state.warmth >= 65:
            warmth_instruction = "Тёплый, дружелюбный, живой тон с комфортным юмором и подколами."
        elif state.warmth >= 40:
            warmth_instruction = "Сдержанный, слегка колючий или капризный тон (шутливая обида или лёгкая дистанция)."
        else:
            warmth_instruction = "Холодный, сухой, отстранённый тон, короткие ответы без лишних эмодзи."

        return (
            f"[Твоё текущее эмоциональное состояние и уровень теплоты/холодности]:\n"
            f"• Краткосрочное настроение: {mood_name.upper()} ({mood_desc})\n"
            f"• Уровень теплоты общения: {state.warmth}/100 ➔ {warmth_instruction}\n"
            f"• Уровень энергии: {state.energy}/100 | Нежность: {state.affection}/100\n"
            f"• Долгосрочные отношения: {stage} (Связь: {state.affinity} pts) — {stage_desc}\n"
            f"• ПРАВИЛО: регулируй теплоту/холодность и дистанцию в ответе строго в соответствии с этими параметрами!\n"
        )


emotion_engine = EmotionalEngine()

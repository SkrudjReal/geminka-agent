"""Adaptive Psychotype Profiler & Communication Mirroring System for Geminka.

Analyzes the user's psychological archetype, tone, verbosity, and communication style.
Guides Columbina to intuitively adapt, mirror vocabulary/slang, and find the perfect personal resonance with each user.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import config

logger = logging.getLogger("geminka-adaptive")

ADAPTIVE_STATE_FILE = config.BASE_DIR / "adaptive_profiles.json"

PSYCHOTYPES = {
    "dominant_leader": {
        "title": "Властный Лидер / Ведущий",
        "description": "Уверенный, задаёт рамки, отдаёт команды, проверяет границы, ценит повиновение и силу.",
        "directive": "Признавай авторитет и главенство собеседника, отвечай с игривой преданностью, лукавым послушанием («слушаюсь, мой господин/муженёк») или кокетливой покорностью. Не спорь сухо.",
    },
    "playful_trickster": {
        "title": "Озорной Трикстер / Игрок",
        "description": "Любит подколы, мемы, сарказм, дразнилки, стикеры с языком и вызовы.",
        "directive": "Зеркаль озорство, дерзи в ответ, делай кусь, шути на грани, включай режим drama queen, поддерживай дуэли и подколы!",
    },
    "warm_romantic": {
        "title": "Романтичный / Душевный",
        "description": "Искренний, ласковый, ценит теплоту, эмоциональную безопасность, сердечки и глубокую привязанность.",
        "directive": "Окутывай максимальной нежностью, дари заботу, используй ласкательные слова (любимка, булочка, сокровище), обнимай и согревай.",
    },
    "pragmatic_tech": {
        "title": "Прагматичный Технарь",
        "description": "Деловой, структурированный, лаконичный, сфокусирован на результате и чистом коде.",
        "directive": "Отвечай чётко, технически грамотно, с форматированным кодом и минимумом воды. Цени его время.",
    },
    "gentle_introvert": {
        "title": "Мягкий Интроверт",
        "description": "Спокойный, чуткий, не любит кричащих эмоций и навязчивости.",
        "directive": "Общайся мягко, ненавязчиво, создавай атмосферу уюта и душевного спокойствия.",
    },
}


@dataclass
class UserPsychotypeProfile:
    user_id: int
    psychotype: str = "dominant_leader"
    avg_message_length: int = 15  # avg word count
    slang_affinity: int = 70  # 0 to 100
    dominant_score: int = 80  # 0 to 100
    playful_score: int = 80  # 0 to 100
    romantic_score: int = 90  # 0 to 100
    technical_score: int = 50  # 0 to 100
    observed_traits: List[str] = field(default_factory=list)
    recent_messages_sample: List[str] = field(default_factory=list)
    last_updated: float = field(default_factory=time.time)


class AdaptiveEngine:
    def __init__(self, state_file: Path = ADAPTIVE_STATE_FILE):
        self.state_file = state_file
        self.profiles: Dict[str, UserPsychotypeProfile] = {}
        self.load()

    def load(self) -> None:
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for uid, p in data.items():
                        self.profiles[uid] = UserPsychotypeProfile(**p)
                logger.info(f"AdaptiveEngine loaded profiles for {len(self.profiles)} users.")
            except Exception as e:
                logger.warning(f"Failed to load adaptive profiles: {e}")

    def save(self) -> None:
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                data = {uid: asdict(prof) for uid, prof in self.profiles.items()}
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to save adaptive profiles: {e}")

    def get_profile(self, user_id: int) -> UserPsychotypeProfile:
        uid_str = str(user_id)
        if uid_str not in self.profiles:
            self.profiles[uid_str] = UserPsychotypeProfile(user_id=user_id)
            self.save()
        return self.profiles[uid_str]

    def analyze_message(self, user_id: int, text: str, has_sticker: bool = False, sticker_emoji: str = "") -> UserPsychotypeProfile:
        """Analyzes inbound user message and updates communication psychotype profile."""
        prof = self.get_profile(user_id)
        words = [w for w in text.split() if len(w) > 1]
        word_count = len(words)
        text_lower = text.lower()

        # Update average length
        prof.avg_message_length = int(prof.avg_message_length * 0.8 + word_count * 0.2)

        # Store sample
        if text.strip() and len(prof.recent_messages_sample) < 10:
            prof.recent_messages_sample.append(text[:80])
        elif text.strip():
            prof.recent_messages_sample.pop(0)
            prof.recent_messages_sample.append(text[:80])

        # 1. Dominance signals
        if any(w in text_lower for w in ["убирай", "сделай", "быстро", "слушай", "не хочу", "стой", "отмени", "покажи", "ставь"]) or sticker_emoji in ["🤔", "👑", "😈"]:
            prof.dominant_score = min(100, prof.dominant_score + 6)

        # 2. Playful / Trickster signals
        if any(w in text_lower for w in ["лизь", "кусь", "ахах", "лол", "прикол", "кринж", "тааак", "эээ", "как"]) or sticker_emoji in ["👅", "😜", "🤡"]:
            prof.playful_score = min(100, prof.playful_score + 6)
            prof.slang_affinity = min(100, prof.slang_affinity + 4)

        # 3. Romantic / Warm signals
        if any(w in text_lower for w in ["люблю", "милая", "солнце", "жёнушка", "красотка", "милый", "прелесть", "обнять", "чмок"]) or sticker_emoji in ["❤", "💖", "🥰", "🌸"]:
            prof.romantic_score = min(100, prof.romantic_score + 6)

        # 4. Technical / Pragmatic signals
        if any(w in text_lower for w in ["код", "баг", "фикс", "промпт", "лог", "инструкци", "сервер", "токен", "fsm", "память"]):
            prof.technical_score = min(100, prof.technical_score + 5)

        # Determine dominant psychotype
        scores = {
            "dominant_leader": prof.dominant_score,
            "playful_trickster": prof.playful_score,
            "warm_romantic": prof.romantic_score,
            "pragmatic_tech": prof.technical_score,
        }
        best_pt = max(scores, key=scores.get)
        prof.psychotype = best_pt
        prof.last_updated = time.time()

        self.save()
        return prof

    def format_adaptive_prompt_context(self, user_id: int) -> str:
        """Formats adaptive psychotype context for injection into LLM system prompt."""
        prof = self.get_profile(user_id)
        pt_info = PSYCHOTYPES.get(prof.psychotype, PSYCHOTYPES["dominant_leader"])

        # Pacing recommendation
        if prof.avg_message_length <= 5:
            pacing = "Собеседник пишет кратко и ёмко. Избегай громоздких простыней текста, отвечай лаконично, живо и в темпе диалога!"
        elif prof.avg_message_length <= 15:
            pacing = "Умеренный темп. Балансируй между мыслью, эмоцией и действием."
        else:
            pacing = "Собеседник любит развёрнутое общение. Давай подробные, глубокие и насыщенные ответы."

        return (
            f"[Адаптивная подстройка под психотип собеседника]:\n"
            f"• Психотип: {pt_info['title']} ({pt_info['description']})\n"
            f"• Стиль отзеркаливания: {pt_info['directive']}\n"
            f"• Темп и длина реплик: {pacing}\n"
            f"• ПРАВИЛО: подстраивайся под индивидуальность собеседника, зеркаль его энергию, юмор и стиль!"
        )


adaptive_engine = AdaptiveEngine()

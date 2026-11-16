"""Explicit content language takes priority over detected language."""
import re

JAPANESE_SPEECH_PROMPT = (
    "観客に聞こえる台詞（text、quote、acknowledgement、sentences）は、"
    "テーマや視聴者コメントの言語にかかわらず、自然な日本語の話し言葉で書いてください。"
    "固有名詞は元の表記を使って構いません。"
)

# Kana in a spoken sentence distinguishes Japanese from Chinese, including Kanji-heavy output.
_JAPANESE_KANA = re.compile(r"[ぁ-ゖァ-ヺｦ-ﾝ]")


def has_expected_speech_language(text: str, language: str | None) -> bool:
    return language != "ja" or _JAPANESE_KANA.search(text) is not None


def add_speech_language_prompt(prompt: str, language: str | None) -> str:
    if language == "ja":
        return f"{JAPANESE_SPEECH_PROMPT}\n{prompt}"
    return prompt


def is_prepared_story_language(language: str) -> bool:
    return language.startswith("zh") or language == "ja"

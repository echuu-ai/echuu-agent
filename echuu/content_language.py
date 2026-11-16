"""Explicit content language takes priority over detected language."""
import re

TTS_LANGUAGES = {"zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean"}
PREPARED_CHARACTERS_PER_SECOND = {"en": 11.0, "ko": 5.2}
RUNDOWN_LINE_LIMITS = {"en": (25, 140), "ko": (10, 55)}
SPOKEN_ACKNOWLEDGEMENTS = {
    "zh": "看到你的消息了，我接着聊。",
    "en": "I saw your message. Let's keep going.",
    "ja": "コメントを読んだよ、続きを話すね。",
    "ko": "댓글 읽었어요, 이야기를 이어 갈게요.",
}
SPOKEN_GIFT_ACKNOWLEDGEMENTS = {
    "en": "Thanks for the gift. Let's keep going.",
    "ja": "ギフトありがとう、続きを話すね。",
    "ko": "선물 고마워요, 이야기를 이어 갈게요.",
}
SPOKEN_GIFT_THANKS = {
    "en": "Thanks, everyone, for sending me gifts!",
    "ja": "みんな、ギフトを送ってくれてありがとう！",
    "ko": "여러분, 선물 보내 줘서 고마워요!",
}
_SPEECH_PROMPTS = {
    "zh": "台词、引语、感谢和续写都用自然的简体中文口语，不随主题、观众弹幕改变；专有名词可以保留原文。",
    "en": (
        "Write all audience-facing speech (text, quote, acknowledgement and "
        "sentences) in natural spoken English, regardless of the topic or chat "
        "language. Keep proper names as written."
    ),
    "ja": (
        "観客に聞こえる台詞（text、quote、acknowledgement、sentences）は、"
        "テーマや視聴者コメントの言語にかかわらず、自然な日本語の話し言葉で書いてください。"
        "固有名詞は元の表記を使って構いません。"
    ),
    "ko": (
        "주제나 시청자 댓글의 언어와 관계없이 시청자가 듣는 모든 대사(text, quote, "
        "acknowledgement, sentences)는 자연스러운 한국어 구어체로 작성하세요. "
        "고유명사의 원래 표기는 유지해도 됩니다."
    ),
}

_JAPANESE_KANA = re.compile(r"[ぁ-ゖァ-ヺｦ-ﾝ]")
_KOREAN_HANGUL = re.compile(r"[가-힣]")
_ENGLISH_WORD = re.compile(r"[a-z]+(?:['’][a-z]+)?", re.IGNORECASE)
_ENGLISH_FUNCTION_WORDS = frozenset(
    "a an and are as at be but by can do for from had has have he her his i if in is it "
    "my no of on or our she so that the their them then there they this to us was we "
    "were what when where which who will with would you your".split()
)


def has_expected_speech_language(text: str, language: str | None) -> bool:
    if language == "ja":
        return _JAPANESE_KANA.search(text) is not None
    if language == "ko":
        return _KOREAN_HANGUL.search(text) is not None
    if language == "en":
        words = _ENGLISH_WORD.findall(text.lower())
        if not words or sum(map(len, words)) <= sum(char.isalpha() for char in text) / 2:
            return False
        return len(words) <= 3 or any(word in _ENGLISH_FUNCTION_WORDS for word in words)
    # Keep legacy Chinese artifacts, including lines composed of proper names.
    return True


def add_speech_language_prompt(prompt: str, language: str | None) -> str:
    instruction = _SPEECH_PROMPTS.get(language)
    return f"{instruction}\n{prompt}" if instruction else prompt


def is_prepared_story_language(language: str) -> bool:
    return language in TTS_LANGUAGES or language.startswith("zh")


def prepared_body_limits(language: str) -> tuple[int, int]:
    rate = PREPARED_CHARACTERS_PER_SECOND.get(language, 4.4)
    return round(60 * rate), round(115 * rate)

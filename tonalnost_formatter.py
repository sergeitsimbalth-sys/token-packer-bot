# tonalnost_formatter.py — нормализация по правилам «тональности»:
# 1) Существительные -> именительный падеж, ед. число (если возможно; иначе pluralia tantum во мн. числе).
# 2) Прилагательные/причастия -> муж. род, ед. число, им. падеж.
# 3) Удаляем кавычки, ~число, дефисы/подчеркивания, пунктуацию; оставляем только слова.
# 4) Возвращаем: (результат_строкой, список_пояснений)

from __future__ import annotations
import re
from typing import List, Tuple
import pymorphy3

morph = pymorphy3.MorphAnalyzer()

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+", re.UNICODE)
CLEAN_QUOTE_TILDE_RE = re.compile(r'[\"“”«»„‟]+|~\d+')
PUNCT_TO_SPACE_RE = re.compile(r"[-–—_]|[^\w\sА-Яа-яЁё]")
MULTISPACE_RE = re.compile(r"\s+")

def _clean_fragment(text: str) -> str:
    # приводим к нижнему регистру
    s = text.lower().strip()
    # убираем кавычки и ~числа
    s = CLEAN_QUOTE_TILDE_RE.sub("", s)
    # переводим тире/подчеркивание в пробел, прочую пунктуацию — в пробел
    s = PUNCT_TO_SPACE_RE.sub(" ", s)
    # схлопываем пробелы
    s = MULTISPACE_RE.sub(" ", s).strip()
    return s

def _choose_parse(word: str):
    # Предпочитаем NOUN > ADJF/ADJS > PRTF/PRTS > остальное; затем по score
    parses = morph.parse(word)
    def score(p):
        pos = p.tag.POS
        base = 0
        if pos == "NOUN":
            base = 4
        elif pos in ("ADJF", "ADJS"):
            base = 3
        elif pos in ("PRTF", "PRTS"):
            base = 2
        elif pos in ("NPRO",):  # местоим. сущ.
            base = 1
        else:
            base = 0
        return (base, p.score)
    return max(parses, key=score) if parses else None

def _inflect_noun(p) -> Tuple[str, List[str]]:
    """Сущ.: nomn, sing; если нельзя — nomn, plur (pluralia tantum)."""
    notes = []
    form = p
    # нормальная форма как база
    base = p.normal_form
    # пытаемся в ед.ч.
    try:
        cand = p.inflect({"nomn", "sing"})
    except Exception:
        cand = None
    if cand:
        return cand.word, ["существительное → им. п., ед. ч."]
    # пробуем во мн.ч.
    try:
        cand = p.inflect({"nomn", "plur"})
    except Exception:
        cand = None
    if cand:
        notes.append("существительное не имеет формы ед. числа — оставлено во мн.ч.")
        return cand.word, notes
    # fallback: нормальная форма (как правило — им.п.)
    return base, ["существительное: оставлена нормальная форма"]

def _to_full_adj_parse(p):
    # ADJS -> ADJF (полная форма) через нормальную форму
    if p.tag.POS == "ADJS":
        nf = p.normal_form
        pp = _choose_parse(nf)
        return pp
    return p

def _inflect_adj(p) -> Tuple[str, List[str]]:
    """Прилагат./причастия: masc, sing, nomn."""
    notes = []
    base = p.normal_form
    p2 = _to_full_adj_parse(p)
    try:
        cand = p2.inflect({"nomn", "sing", "masc"})
    except Exception:
        cand = None
    if cand:
        return cand.word, ["прилагательное/причастие → м. род, ед. ч., им. п."]
    # fallback: нормальная форма
    return base, ["прилагательное/причастие: оставлена нормальная форма"]

def _normalize_word(word: str) -> Tuple[str, List[str], str]:
    """
    Возвращает (нормализованное_слово, пояснения[...], pos_hint).
    pos_hint: 'NOUN' если нашли существительное; иначе POS/None.
    """
    p = _choose_parse(word)
    if not p:
        return word, ["не распознано — оставлено как есть"], "UNK"
    pos = p.tag.POS
    if pos == "NOUN":
        w, notes = _inflect_noun(p)
        return w, notes, "NOUN"
    if pos in ("ADJF", "ADJS", "PRTF", "PRTS"):
        w, notes = _inflect_adj(p)
        return w, notes, pos
    # Прочее — лемматизируем
    w = p.normal_form
    return w, [f"{pos or 'другое'}: приведено к нормальной форме"], pos or "OTHER"

def normalize_message(text: str) -> Tuple[str, List[str]]:
    """
    Основная функция:
    - Делит вход по запятым на фрагменты;
    - Чистит каждый фрагмент;
    - Нормализует токены с сохранением порядка;
    - Возвращает:
        * строку «слова, слова, ...» (внутри фрагмента — слова через пробел),
        * пояснения списком.
    """
    # Разбиваем по запятым на фрагменты
    fragments = [frag.strip() for frag in text.split(",")]
    out_fragments: List[str] = []
    explanations: List[str] = []

    for raw_frag in fragments:
        if not raw_frag.strip():
            continue
        cleaned = _clean_fragment(raw_frag)
        # собираем только слова (м.б. аббревиатуры типа мчс)
        tokens = _WORD_RE.findall(cleaned)
        if not tokens:
            explanations.append(f"«{raw_frag}» — пусто после очистки, пропущено")
            continue

        norm_tokens: List[str] = []
        found_noun = False
        local_notes: List[str] = []

        for tok in tokens:
            new_w, notes, pos = _normalize_word(tok)
            norm_tokens.append(new_w)
            local_notes.extend([f"{tok} → {new_w}: {n}" for n in notes])
            if pos == "NOUN":
                found_noun = True

        # предупреждение, если нет существительного (но оставляем — как в примере с «мчс»)
        if not found_noun:
            local_notes.append("⚠ фрагмент без существительного — оставлен как есть (допустимо для аббревиатур)")

        explanations.extend(local_notes)
        out_fragments.append(" ".join(norm_tokens))

    # Итоговая строка: только слова через запятую
    result = ", ".join(out_fragments)
    return result, explanations

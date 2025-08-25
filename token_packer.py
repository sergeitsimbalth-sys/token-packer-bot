# token_packer.py — логика упаковки
from typing import List

def normalize_tokens(lines: List[str]) -> List[str]:
    tokens = []
    for line in lines:
        parts = line.replace(";", ",").replace("\n", ",").split(",")
        for p in parts:
            p = p.strip()
            if p:
                tokens.append(p)
    return tokens

def preprocess(tokens: List[str]) -> List[str]:
    return [t.strip() for t in tokens if t.strip()]

def len_sep_construct(llen: int, rlen: int, sep_len: int) -> int:
    return 2 + sep_len + llen + rlen  # "(" + LEFT + sep + RIGHT + ")"

def split_right_tokens(right: List[str], left_len: int,
                       min_len: int, max_len: int, sep_len: int,
                       inner_sep: str = ",") -> List[str]:
    results, buffer = [], []
    buffer_len = 0
    for tok in right:
        tok_len = len(tok)
        extra = len(inner_sep) if buffer else 0
        projected_rlen = buffer_len + extra + tok_len
        projected_total = len_sep_construct(left_len, projected_rlen, sep_len)
        if projected_total > max_len:
            if buffer:
                results.append(inner_sep.join(buffer))
            buffer, buffer_len = [tok], tok_len
        else:
            buffer.append(tok)
            buffer_len = projected_rlen
            if len_sep_construct(left_len, buffer_len, sep_len) >= min_len:
                results.append(inner_sep.join(buffer))
                buffer, buffer_len = [], 0
    if buffer:
        results.append(inner_sep.join(buffer))
    return results

def pack(left_tokens: List[str], right_tokens: List[str],
         min_len: int, max_len: int, separator: str) -> List[str]:
    if min_len > max_len:
        raise ValueError(f"min_len ({min_len}) > max_len ({max_len})")
    left_tokens = preprocess(left_tokens)
    right_tokens = preprocess(right_tokens)
    if not left_tokens:
        raise ValueError("Левая часть пуста")
    if not right_tokens:
        raise ValueError("Правая часть пуста")
    lstr = ",".join(left_tokens)
    llen = len(lstr)
    sep_len = len(separator)

    # Ранняя проверка на «невозможные» токены
    for tok in right_tokens:
        if len_sep_construct(llen, len(tok), sep_len) > max_len:
            raise ValueError(f"Токен '{tok}' слишком длинный для max_len={max_len}")

    right_groups = split_right_tokens(right_tokens, llen, min_len, max_len, sep_len, inner_sep=",")
    result = []
    for rstr in right_groups:
        total_len = len_sep_construct(llen, len(rstr), sep_len)
        if total_len > max_len:
            raise ValueError(f"Превышен лимит {max_len} символов (получилось {total_len})")
        result.append(f"({lstr}{separator}{rstr})")
    return result

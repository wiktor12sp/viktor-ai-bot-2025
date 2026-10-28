# test_smoke.py
"""Проверка чистой логики бота. Запуск: python test_smoke.py"""
import ast
import pathlib

from main import (
    MAX_HISTORY, SYSTEM_PROMPT, TTLDict,
    bot_best_move, check_winner, clean_response,
    get_trimmed_history, parse_ttt_index,
)


def test_syntax():
    ast.parse(pathlib.Path("main.py").read_text(encoding="utf-8"))


def test_winner():
    assert check_winner(list("XXXOO    ")) == "X"
    assert check_winner(list("XOXOXOOXO")) == "draw"
    assert check_winner([" "] * 9) is None


def test_clean():
    assert clean_response("<think>x</think>ответ") == "ответ"
    assert clean_response("  abc  ") == "abc"


def test_trim():
    # system первым, длина ограничена
    h = [{"role": "system", "content": SYSTEM_PROMPT}]
    h += [{"role": "user", "content": f"m{i}"} for i in range(30)]
    out = get_trimmed_history(h)
    assert out[0]["role"] == "system"
    assert len(out) == MAX_HISTORY + 1
    assert out[-1]["content"] == "m29"
    # идемпотентность
    assert get_trimmed_history(out) == out
    # пустая история
    assert get_trimmed_history(None)[0]["role"] == "system"
    # system отсутствует — восстанавливается
    out2 = get_trimmed_history([{"role": "user", "content": "x"}])
    assert out2[0]["role"] == "system"
    assert out2[-1]["content"] == "x"


def test_callback():
    assert parse_ttt_index("ttt_0") == 0
    assert parse_ttt_index("ttt_8") == 8
    assert parse_ttt_index("ttt_99") is None
    assert parse_ttt_index("ttt_abc") is None               
    assert parse_ttt_index("ttt_restart") is None
    assert parse_ttt_index("snake_1") is None


def test_bot_move():
    assert bot_best_move(list("OO XX    ")) == 2   # завершает линию
    assert bot_best_move(list("XX O     ")) == 2   # блокирует игрока


def test_ttl():
    d = TTLDict(ttl=1)
    d.set(1, "a")
    d.set(2, 0)  # проверка на falsy-значение
    assert d.get(1) == "a"
    assert d.get(2) == 0
    import time
    time.sleep(1.1)
    assert d.get(1) is None
    assert d.get(2) is None


if __name__ == "__main__":
    test_syntax()
    test_winner()
    test_clean()
    test_trim()
    test_callback()
    test_bot_move()
    test_ttl()
    print("✅ Все smoke-тесты пройдены")

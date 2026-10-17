"""Optional numerology + Chinese astrology modifier. Bounded and shown separately;
it never replaces the projection-based value, it only nudges it."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import requests
from lunardate import LunarDate

from app.config import CACHE_DIR

ANIMALS = ["Rat", "Ox", "Tiger", "Rabbit", "Dragon", "Snake", "Horse", "Goat", "Monkey", "Rooster", "Dog", "Pig"]
ELEMENTS = ["Wood", "Fire", "Earth", "Metal", "Water"]
TRINES = [{"Rat", "Dragon", "Monkey"}, {"Ox", "Snake", "Rooster"}, {"Tiger", "Horse", "Dog"}, {"Rabbit", "Goat", "Pig"}]
HARMONY = [{"Rat", "Ox"}, {"Tiger", "Pig"}, {"Rabbit", "Dog"}, {"Dragon", "Rooster"}, {"Snake", "Monkey"}, {"Horse", "Goat"}]
HARM = [{"Rat", "Goat"}, {"Ox", "Horse"}, {"Tiger", "Snake"}, {"Rabbit", "Dragon"}, {"Dog", "Rooster"}, {"Monkey", "Pig"}]
GENERATES = {"Wood": "Fire", "Fire": "Earth", "Earth": "Metal", "Metal": "Water", "Water": "Wood"}
CONTROLS = {"Wood": "Earth", "Earth": "Water", "Water": "Fire", "Fire": "Metal", "Metal": "Wood"}
LP_GROUPS = [{1, 5, 7}, {2, 4, 8}, {3, 6, 9}]


def life_path(d: date) -> int:
    n = sum(int(c) for c in d.strftime("%Y%m%d"))
    while n > 9 and n not in (11, 22, 33):
        n = sum(int(c) for c in str(n))
    return n


def zodiac(d: date) -> tuple[str, str]:
    y = LunarDate.from_solar_date(d.year, d.month, d.day).year
    return ANIMALS[(y - 4) % 12], ELEMENTS[((y - 4) % 10) // 2]


def profile(d: date) -> dict:
    animal, element = zodiac(d)
    return {"life_path": life_path(d), "animal": animal, "element": element}


def _animal_score(a: str, b: str) -> float:
    if a == b:
        return 0.5
    pair = {a, b}
    if any(pair <= t for t in TRINES) or pair in HARMONY:
        return 1.0
    if abs(ANIMALS.index(a) - ANIMALS.index(b)) == 6:
        return -1.0
    if pair in HARM:
        return -0.5
    return 0.0


def _element_score(a: str, b: str) -> float:
    if GENERATES[a] == b or GENERATES[b] == a:
        return 0.5
    if CONTROLS[a] == b or CONTROLS[b] == a:
        return -0.5
    return 0.0


def _lp_score(a: int, b: int) -> float:
    a, b = (a % 9 or 9), (b % 9 or 9)  # master numbers reduce for harmony
    return 0.5 if a == b or any({a, b} <= g for g in LP_GROUPS) else 0.0


def compatibility(player_dob: date, user_dob: date | None, today: date | None = None) -> dict:
    """Score in [-1, 1] plus the reasons behind it."""
    p = profile(player_dob)
    year_animal, _ = zodiac(today or date.today())
    raw = 0.5 * _animal_score(p["animal"], year_animal)
    reasons = [f'{p["element"]} {p["animal"]} in the Year of the {year_animal}']
    if user_dob:
        u = profile(user_dob)
        raw += _animal_score(p["animal"], u["animal"]) + _element_score(p["element"], u["element"]) + _lp_score(p["life_path"], u["life_path"])
        reasons.append(f'vs your {u["element"]} {u["animal"]}, LP {p["life_path"]}↔{u["life_path"]}')
    return {**p, "score": round(max(-1.0, min(1.0, raw / 2)), 2), "why": "; ".join(reasons)}


# ---------- birthdates (ESPN athlete ids == fantasy player ids) ----------
DOB_CACHE = CACHE_DIR / "birthdates.json"
_dob_lock = threading.Lock()


def _fetch_dob(pid: int) -> str | None:
    for _ in range(3):
        try:
            r = requests.get(f"https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/athletes/{pid}", timeout=10)
            if r.ok:
                return (r.json().get("dateOfBirth") or "")[:10] or None
        except requests.RequestException:
            pass
    return None


def birthdates(player_ids: list[int]) -> dict[int, str]:
    with _dob_lock:
        return _birthdates(player_ids)


def _birthdates(player_ids: list[int]) -> dict[int, str]:
    cache = {int(k): v for k, v in json.loads(DOB_CACHE.read_text()).items()} if DOB_CACHE.exists() else {}
    missing = [i for i in player_ids if not cache.get(i)]  # failed lookups are retried
    if missing:
        with ThreadPoolExecutor(8) as ex:
            for pid, dob in zip(missing, ex.map(_fetch_dob, missing)):
                if dob:
                    cache[pid] = dob
        DOB_CACHE.write_text(json.dumps(cache))
    return {i: cache[i] for i in player_ids if cache.get(i)}

#!/usr/bin/env python3
"""
COTD — Calories of the Day  |  Chat Server
Run:  python app.py
Open: http://localhost:8000
"""

# ── Imports ────────────────────────────────────────────────────────────────────
from __future__ import annotations
import json, re, os, sqlite3, uuid
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional, List
from pathlib import Path

import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx, requests
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

_executor = ThreadPoolExecutor(max_workers=4)

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
OLLAMA_URL      = "http://localhost:11434/api/chat"
USDA_API_KEY    = os.getenv("USDA_API_KEY", "DEMO_KEY")
USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
DB_PATH         = Path("cotd.db")
MAX_QUESTIONS   = 3

# ── Data structures ────────────────────────────────────────────────────────────
@dataclass
class NutritionData:
    calories: float
    protein_g: float
    fat_g: float
    carbs_g: float
    fat_saturated_g: Optional[float] = None
    fiber_g: Optional[float] = None
    sugar_g: Optional[float] = None
    sodium_mg: Optional[float] = None
    api_source: str = "unknown"
    confidence: float = 1.0

@dataclass
class Ingredient:
    name: str
    amount: float
    unit: str
    confidence: float = 1.0
    source: str = "user_input"
    cooking_method: Optional[str] = None
    notes: Optional[str] = None
    nutrition: Optional[NutritionData] = None

@dataclass
class Meal:
    meal_type: str
    ingredients: List[Ingredient] = field(default_factory=list)
    meal_timestamp: datetime = field(default_factory=datetime.now)
    meal_date: date = field(default_factory=date.today)
    total_calories: float = 0.0
    total_protein_g: float = 0.0
    total_fat_g: float = 0.0
    total_carbs_g: float = 0.0
    total_fiber_g: float = 0.0
    mood: Optional[str] = None
    notes: Optional[str] = None
    api_fallback_count: int = 0
    id: Optional[int] = None

# ── Ollama helper ──────────────────────────────────────────────────────────────
def ollama_chat(model: str, messages: list[dict], temperature: float = 0.0) -> str:
    payload = {"model": model, "messages": messages, "stream": False,
               "options": {"temperature": temperature}}
    resp = httpx.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()

def extract_json(text: str) -> dict:
    text = re.sub(r'```(?:json)?\s*', '', text)
    text = re.sub(r'```', '', text)
    # Walk brace depth to extract the first complete JSON object,
    # avoiding greedy regex that grabs text between multiple objects.
    start = text.find('{')
    if start == -1:
        raise ValueError(f"No JSON found in model output:\n{text}")
    depth = 0
    for i, c in enumerate(text[start:], start):
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError(f"Incomplete JSON in model output:\n{text}")

# ── Router agent ───────────────────────────────────────────────────────────────
ROUTER_MODEL  = "gemma3:1b"
ROUTER_SYSTEM = """You are a food input router. Classify user input into exactly one category:
- IMAGE_AGENT  → user provides an image file path, URL ending in .jpg/.png, or explicitly mentions a photo
- TEXT_AGENT   → user describes food in text

Respond with ONLY one of these two words: IMAGE_AGENT or TEXT_AGENT"""

def router_agent(user_message: str) -> str:
    reply = ollama_chat(ROUTER_MODEL,
                        [{"role": "system", "content": ROUTER_SYSTEM},
                         {"role": "user",   "content": user_message}])
    return "IMAGE_AGENT" if "IMAGE_AGENT" in reply.upper() else "TEXT_AGENT"

# ── Text agent ─────────────────────────────────────────────────────────────────
TEXT_AGENT_MODEL  = "qwen2.5:3b-instruct"
TEXT_AGENT_SYSTEM = """You are a food ingredient parser. Extract all food ingredients from the user's message.

Rules:
- "name" = the food item ONLY in ENGLISH (e.g. "banana", "greek yogurt"). NEVER include measurement words in the name.
- "amount" = numeric value (float). If unknown, use null.
- "unit" = "g", "ml", "piece", "cup", "tbsp", "tsp". If unknown, use null.
- "cooking_method" = "grilled", "raw", "steamed", etc. If unknown, use null.

EXAMPLE:
Input: "1 banana, 1 cup Greek yogurt, 1 tbsp honey"
Output:
{"ingredients": [
  {"name": "banana",       "amount": 1, "unit": "piece", "cooking_method": null},
  {"name": "greek yogurt", "amount": 1, "unit": "cup",   "cooking_method": null},
  {"name": "honey",        "amount": 1, "unit": "tbsp",  "cooking_method": null}
]}

EXAMPLE (Chinese → English):
Input: "一个香蕉，一杯希腊酸奶"
Output:
{"ingredients": [
  {"name": "banana",       "amount": 1, "unit": "piece", "cooking_method": null},
  {"name": "greek yogurt", "amount": 1, "unit": "cup",   "cooking_method": null}
]}

Respond ONLY with valid JSON. No markdown, no explanation."""

def text_agent(user_message: str) -> list[Ingredient]:
    reply = ollama_chat(TEXT_AGENT_MODEL,
                        [{"role": "system", "content": TEXT_AGENT_SYSTEM},
                         {"role": "user",   "content": user_message}])
    parsed = extract_json(reply)
    return [Ingredient(
        name=item["name"],
        amount=float(item["amount"]) if item.get("amount") is not None else 0.0,
        unit=item.get("unit") or "unknown",
        cooking_method=item.get("cooking_method"),
        confidence=0.9 if item.get("amount") is not None else 0.3,
        source="user_input",
    ) for item in parsed.get("ingredients", [])]

# ── Reasoning agent ────────────────────────────────────────────────────────────
REASONING_MODEL  = "deepseek-r1:1.5b"
REASONING_SYSTEM = """You are a meal completeness checker. Given a list of food ingredients, evaluate if we have enough data for accurate calorie calculation.

Scoring per ingredient:
- Amount missing OR unit vague → confidence 0.3
- Non-standard unit (handful, some) → confidence 0.6
- Missing cooking method for protein/oil → confidence 0.8
- Everything clear → confidence 1.0

overall_completeness = mean of all ingredient confidences

Respond ONLY with valid JSON:
{"ingredient_scores": [{"name": "...", "confidence": 0.0, "issue": "...or null"}],
 "completeness_score": 0.0,
 "next_question": "...one specific question, or null"}

CRITICAL RULE: if completeness_score < 0.75 you MUST write a question in next_question. It must NEVER be null when score < 0.75."""

def reasoning_agent(ingredients: list[Ingredient]) -> dict:
    ing_list = [{"name": i.name, "amount": i.amount if i.amount > 0 else None,
                 "unit": i.unit, "cooking_method": i.cooking_method} for i in ingredients]
    reply = ollama_chat(REASONING_MODEL,
                        [{"role": "system", "content": REASONING_SYSTEM},
                         {"role": "user",   "content": f"Ingredients: {json.dumps(ing_list)}"}])
    result = extract_json(reply)

    # Safety net: derive question from actual ingredient state
    if result.get("completeness_score", 1.0) < 0.75:
        low_scores = sorted(result.get("ingredient_scores", []), key=lambda s: s.get("confidence", 1))
        if low_scores:
            s     = low_scores[0]
            name  = s["name"]
            issue = (s.get("issue") or "").lower()
            match = next((i for i in ingredients if i.name.lower() == name.lower()), None)
            if match is None or match.amount <= 0:
                result["next_question"] = f"How much {name} did you have? (e.g. 1 cup, 200g, 1 plate)"
            elif "unit" in issue or "vague" in issue:
                result["next_question"] = f"Can you be more specific about how much {name}? (e.g. grams or cups)"
            elif not result.get("next_question"):
                result["next_question"] = f"How much {name} did you have? (e.g. 1 cup, 200g, 1 plate)"
    return result

# ── Nutrition lookup ───────────────────────────────────────────────────────────
NUTRIENT_IDS = {"calories": 1008, "protein_g": 1003, "fat_g": 1004,
                "carbs_g": 1005, "fiber_g": 1079, "sugar_g": 2000,
                "sodium_mg": 1093, "fat_saturated_g": 1258}
FALLBACK_GENERICS = {
    "oil":       NutritionData(calories=884, protein_g=0,   fat_g=100, carbs_g=0,  api_source="fallback_generic", confidence=0.3),
    "meat":      NutritionData(calories=150, protein_g=26,  fat_g=5,   carbs_g=0,  api_source="fallback_generic", confidence=0.3),
    "dairy":     NutritionData(calories=61,  protein_g=3.2, fat_g=3.3, carbs_g=4.8,api_source="fallback_generic", confidence=0.3),
    "grain":     NutritionData(calories=130, protein_g=4,   fat_g=1,   carbs_g=27, api_source="fallback_generic", confidence=0.3),
    "fruit":     NutritionData(calories=60,  protein_g=0.8, fat_g=0.2, carbs_g=15, api_source="fallback_generic", confidence=0.3),
    "vegetable": NutritionData(calories=30,  protein_g=2,   fat_g=0.3, carbs_g=5,  api_source="fallback_generic", confidence=0.3),
    "default":   NutritionData(calories=100, protein_g=3,   fat_g=3,   carbs_g=12, api_source="fallback_generic", confidence=0.2),
}
UNIT_TO_GRAMS = {"g": 1.0, "ml": 1.0, "kg": 1000.0, "l": 1000.0,
                 "oz": 28.35, "lb": 453.6, "cup": 240.0, "tbsp": 15.0,
                 "tsp": 5.0, "piece": 100.0, "medium": 100.0,
                 "large": 150.0, "small": 70.0, "unknown": 100.0}
_PROCESSED = {"breaded","fried","dehydrated","powder","dried","frozen",
              "canned","mix","flavored","instant","flour","unenriched"}

def unit_to_grams(amount: float, unit: str) -> float:
    return amount * UNIT_TO_GRAMS.get(unit.lower(), 100.0)

def _extract_nutrients(food: dict) -> NutritionData:
    nmap = {n["nutrientId"]: n.get("value", 0.0) for n in food.get("foodNutrients", [])}
    return NutritionData(
        calories=nmap.get(NUTRIENT_IDS["calories"], 0.0),
        protein_g=nmap.get(NUTRIENT_IDS["protein_g"], 0.0),
        fat_g=nmap.get(NUTRIENT_IDS["fat_g"], 0.0),
        carbs_g=nmap.get(NUTRIENT_IDS["carbs_g"], 0.0),
        fiber_g=nmap.get(NUTRIENT_IDS["fiber_g"]),
        sugar_g=nmap.get(NUTRIENT_IDS["sugar_g"]),
        sodium_mg=nmap.get(NUTRIENT_IDS["sodium_mg"]),
        fat_saturated_g=nmap.get(NUTRIENT_IDS["fat_saturated_g"]),
        api_source="USDA_FDC", confidence=0.95,
    )

def _scale(base: NutritionData, grams: float) -> NutritionData:
    r = grams / 100.0
    return NutritionData(
        calories=round(base.calories * r, 1),
        protein_g=round(base.protein_g * r, 2),
        fat_g=round(base.fat_g * r, 2),
        carbs_g=round(base.carbs_g * r, 2),
        fiber_g=round(base.fiber_g * r, 2) if base.fiber_g else None,
        sugar_g=round(base.sugar_g * r, 2) if base.sugar_g else None,
        sodium_mg=round(base.sodium_mg * r, 1) if base.sodium_mg else None,
        fat_saturated_g=round(base.fat_saturated_g * r, 2) if base.fat_saturated_g else None,
        api_source=base.api_source, confidence=base.confidence,
    )

def _usda_search(query: str) -> NutritionData | None:
    try:
        r = requests.get(USDA_SEARCH_URL,
                         params={"query": query, "pageSize": 20, "api_key": USDA_API_KEY,
                                 "dataType": ["Foundation", "SR Legacy"]}, timeout=10)
        if r.status_code == 429:
            return None
        r.raise_for_status()
        foods = r.json().get("foods", [])
        if not foods:
            return None
        q_words = query.lower().split()
        def _score(f):
            desc = f.get("description", "").lower()
            hits    = sum(1 for w in q_words if w in desc)
            first   = 2 if any(desc.startswith(w) for w in q_words) else 0
            penalty = -3 * len(_PROCESSED & set(desc.split(",")[0].split()))
            return (first + hits + penalty, -len(desc))
        return _extract_nutrients(max(foods, key=_score))
    except Exception:
        return None

def _get_fallback(name: str) -> NutritionData:
    n = name.lower()
    if any(w in n for w in ["oil","butter","lard"]):                           return FALLBACK_GENERICS["oil"]
    if any(w in n for w in ["chicken","beef","pork","fish","salmon","tuna"]):  return FALLBACK_GENERICS["meat"]
    if any(w in n for w in ["milk","cheese","yogurt","cream"]):                return FALLBACK_GENERICS["dairy"]
    if any(w in n for w in ["rice","pasta","bread","oat","grain","cereal"]):   return FALLBACK_GENERICS["grain"]
    if any(w in n for w in ["apple","banana","orange","berry","mango"]):       return FALLBACK_GENERICS["fruit"]
    if any(w in n for w in ["broccoli","spinach","carrot","tomato","onion"]): return FALLBACK_GENERICS["vegetable"]
    return FALLBACK_GENERICS["default"]

# ── Database ───────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_timestamp DATETIME, meal_date DATE, meal_type TEXT,
    total_calories REAL, total_protein_g REAL, total_fat_g REAL,
    total_carbs_g REAL, total_fiber_g REAL, api_fallback_count INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS meal_ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT, meal_id INTEGER,
    ingredient_name TEXT, amount_value REAL, unit TEXT, cooking_method TEXT,
    calories REAL, protein_g REAL, fat_g REAL, carbs_g REAL, fiber_g REAL,
    api_source TEXT, confidence REAL, source TEXT,
    FOREIGN KEY (meal_id) REFERENCES meals(id)
);
CREATE TABLE IF NOT EXISTS ingredient_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ingredient_name TEXT UNIQUE,
    amount_value INTEGER, unit TEXT, nutrition_data JSON, cached_at DATETIME
);"""

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn

def _cache_get(conn, name):
    row = conn.execute("SELECT nutrition_data FROM ingredient_cache WHERE ingredient_name=?",
                       (name.lower(),)).fetchone()
    return NutritionData(**json.loads(row[0])) if row else None

def _cache_set(conn, name, nutrition):
    conn.execute("INSERT OR REPLACE INTO ingredient_cache "
                 "(ingredient_name, amount_value, unit, nutrition_data, cached_at) "
                 "VALUES (?, 100, 'g', ?, CURRENT_TIMESTAMP)",
                 (name.lower(), json.dumps(nutrition.__dict__)))
    conn.commit()

def nutrition_lookup_agent(ingredients: list[Ingredient], conn) -> list[Ingredient]:
    for ing in ingredients:
        grams = unit_to_grams(ing.amount, ing.unit)
        if grams <= 0:
            grams = 100.0
        query = ing.name + (f" {ing.cooking_method}" if ing.cooking_method else "")
        cached = _cache_get(conn, ing.name)
        if cached:
            ing.nutrition = _scale(cached, grams)
        else:
            base = _usda_search(query)
            if base:
                _cache_set(conn, ing.name, base)
                ing.nutrition = _scale(base, grams)
            else:
                base = _get_fallback(ing.name)
                ing.nutrition = _scale(base, grams)
                ing.confidence = min(ing.confidence, 0.4)
                ing.source = "fallback_average"
    return ingredients

def save_meal(meal: Meal, conn) -> int:
    cur = conn.execute(
        "INSERT INTO meals (meal_timestamp,meal_date,meal_type,total_calories,"
        "total_protein_g,total_fat_g,total_carbs_g,total_fiber_g,api_fallback_count) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (meal.meal_timestamp.isoformat(), meal.meal_date.isoformat(), meal.meal_type,
         meal.total_calories, meal.total_protein_g, meal.total_fat_g,
         meal.total_carbs_g, meal.total_fiber_g, meal.api_fallback_count))
    meal_id = cur.lastrowid
    for ing in meal.ingredients:
        n = ing.nutrition
        conn.execute(
            "INSERT INTO meal_ingredients (meal_id,ingredient_name,amount_value,unit,"
            "cooking_method,calories,protein_g,fat_g,carbs_g,fiber_g,api_source,confidence,source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (meal_id, ing.name, ing.amount, ing.unit, ing.cooking_method,
             n.calories if n else 0, n.protein_g if n else 0, n.fat_g if n else 0,
             n.carbs_g if n else 0, n.fiber_g if n else 0,
             n.api_source if n else "none", ing.confidence, ing.source))
    conn.commit()
    meal.id = meal_id
    return meal_id

def get_daily_summary(target_date: date, conn) -> dict:
    row = conn.execute(
        "SELECT SUM(total_calories),SUM(total_protein_g),SUM(total_fat_g),"
        "SUM(total_carbs_g),COUNT(*) FROM meals WHERE meal_date=?",
        (target_date.isoformat(),)).fetchone()
    return {"date": target_date.isoformat(), "total_calories": row[0] or 0,
            "total_protein_g": row[1] or 0, "total_fat_g": row[2] or 0,
            "total_carbs_g": row[3] or 0, "meal_count": row[4] or 0}

# ── Helpers ────────────────────────────────────────────────────────────────────
def _infer_meal_type() -> str:
    h = datetime.now().hour
    if h < 10: return "breakfast"
    if h < 14: return "lunch"
    if h < 18: return "snack"
    return "dinner"

def _apply_clarification(ingredients: list[Ingredient], reply: str, question: str) -> list[Ingredient]:
    prompt = (f"A user was asked: '{question}'\nThey replied: '{reply}'\n\n"
              "Extract any food amounts or cooking methods mentioned. "
              'Respond ONLY with JSON: {"updates": [{"name":"...","amount":...,"unit":"...","cooking_method":"..."}]}')
    try:
        updates = extract_json(ollama_chat(TEXT_AGENT_MODEL,
                                           [{"role": "user", "content": prompt}])).get("updates", [])
        for upd in updates:
            for ing in ingredients:
                if upd["name"].lower() in ing.name.lower() or ing.name.lower() in upd["name"].lower():
                    if upd.get("amount"):         ing.amount = float(upd["amount"])
                    if upd.get("unit"):           ing.unit = upd["unit"]
                    if upd.get("cooking_method"): ing.cooking_method = upd["cooking_method"]
                    ing.source = "user_input"; ing.confidence = 0.9
    except Exception:
        pass
    return ingredients

# ── Conversation state (one per session) ──────────────────────────────────────
@dataclass
class ConversationState:
    # Full history — never cleared, survives reconnects
    history: list[dict]          = field(default_factory=list)
    # Current meal being built
    ingredients: list[Ingredient] = field(default_factory=list)
    pending_q: Optional[str]     = None
    phase: str                   = "idle"   # idle | clarifying
    turn: int                    = 0
    # Memory of last saved meal
    last_meal_id: Optional[int]       = None
    last_meal_summary: Optional[str]  = None

    def push(self, role: str, text: str):
        self.history.append({"role": role, "text": text, "ts": datetime.now().isoformat()})

SESSIONS: dict[str, ConversationState] = {}

# ── Core conversation logic ────────────────────────────────────────────────────
def _finalize(state: ConversationState) -> str:
    state.phase = "idle"
    state.turn  = 0
    conn = get_db()
    state.ingredients = nutrition_lookup_agent(state.ingredients, conn)
    meal_type = _infer_meal_type()
    meal = Meal(
        meal_type=meal_type,
        ingredients=state.ingredients,
        total_calories  = sum(i.nutrition.calories   for i in state.ingredients if i.nutrition),
        total_protein_g = sum(i.nutrition.protein_g  for i in state.ingredients if i.nutrition),
        total_fat_g     = sum(i.nutrition.fat_g      for i in state.ingredients if i.nutrition),
        total_carbs_g   = sum(i.nutrition.carbs_g    for i in state.ingredients if i.nutrition),
        total_fiber_g   = sum((i.nutrition.fiber_g or 0) for i in state.ingredients if i.nutrition),
        api_fallback_count = sum(1 for i in state.ingredients if i.source == "fallback_average"),
    )
    save_meal(meal, conn)
    conn.close()
    state.last_meal_id = meal.id
    state.last_meal_summary = (
        f"{meal_type}: {meal.total_calories:.0f} kcal | "
        f"{meal.total_protein_g:.1f}g protein | "
        f"{meal.total_fat_g:.1f}g fat | "
        f"{meal.total_carbs_g:.1f}g carbs"
    )
    names = ", ".join(i.name for i in state.ingredients)
    return (f"✅ {meal_type.capitalize()} logged!\n"
            f"Foods: {names}\n"
            f"🔥 {meal.total_calories:.0f} kcal  "
            f"💪 {meal.total_protein_g:.1f}g protein  "
            f"🥑 {meal.total_fat_g:.1f}g fat  "
            f"🌾 {meal.total_carbs_g:.1f}g carbs")

def _check_completeness(state: ConversationState) -> str:
    """Run reasoning agent. Return question string or trigger finalize."""
    check    = reasoning_agent(state.ingredients)
    score    = check.get("completeness_score", 1.0)
    question = check.get("next_question")
    if score >= 0.75 or not question or state.turn >= MAX_QUESTIONS:
        return _finalize(state)
    state.phase    = "clarifying"
    state.pending_q = question
    state.turn     += 1
    return question

def process(state: ConversationState, text: str) -> str:
    """Route a message and return the bot reply. Updates state in-place."""
    state.push("user", text)
    low = text.lower()

    # ── Built-in commands ──────────────────────────────────────────────────────
    if any(w in low for w in ["last meal", "my last meal", "what did i eat", "previous meal"]):
        reply = (f"Your last meal → {state.last_meal_summary}"
                 if state.last_meal_summary else "No previous meal in this session.")
        state.push("bot", reply)
        return reply

    if any(w in low for w in ["today", "daily total", "summary", "how much today"]):
        conn = get_db()
        s = get_daily_summary(date.today(), conn)
        conn.close()
        reply = (f"Today: {s['meal_count']} meal(s)  "
                 f"🔥 {s['total_calories']:.0f} kcal  "
                 f"💪 {s['total_protein_g']:.1f}g protein  "
                 f"🥑 {s['total_fat_g']:.1f}g fat")
        state.push("bot", reply)
        return reply

    if any(w in low for w in ["history", "what have i said", "chat log"]):
        if not state.history:
            reply = "No history yet."
        else:
            lines = [f"[{h['role'].upper()}] {h['text']}" for h in state.history[-10:]]
            reply = "Last 10 messages:\n" + "\n".join(lines)
        state.push("bot", reply)
        return reply

    # ── Clarification answer (same meal context retained) ──────────────────────
    if state.phase == "clarifying" and state.pending_q:
        # User explicitly skips
        if text.strip() in ("", "skip", "no", "n/a"):
            for ing in state.ingredients:
                if ing.amount <= 0:
                    ing.source = "fallback_average"
            state.pending_q = None
            reply = _finalize(state)
            state.push("bot", reply)
            return reply
        # User answered
        state.ingredients = _apply_clarification(state.ingredients, text, state.pending_q)
        state.pending_q = None
        reply = _check_completeness(state)
        state.push("bot", reply)
        return reply

    # ── New meal description ───────────────────────────────────────────────────
    state.ingredients = []
    state.pending_q   = None
    state.phase       = "idle"
    state.turn        = 0
    try:
        state.ingredients = text_agent(text)
        if not state.ingredients:
            reply = "I couldn't find any foods in that. Try describing what you ate."
            state.push("bot", reply)
            return reply
        parsed_summary = ", ".join(
            f"{i.name} ({i.amount} {i.unit})" if i.amount > 0 else i.name
            for i in state.ingredients
        )
        intro = f"Got it! I see: {parsed_summary}"
        followup = _check_completeness(state)
        reply = f"{intro}\n\n{followup}" if followup != intro else intro
    except Exception as e:
        reply = f"Parse error: {e}. Please rephrase."
    state.push("bot", reply)
    return reply

# ── HTML frontend ──────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>COTD — Calories of the Day</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f5f5f5; display: flex; justify-content: center;
         align-items: center; height: 100vh; }
  .chat-wrapper { width: 480px; height: 680px; background: #fff;
                  border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,.12);
                  display: flex; flex-direction: column; overflow: hidden; }
  .header { background: #1a1a2e; color: #fff; padding: 16px 20px;
            font-size: 17px; font-weight: 600; display: flex;
            align-items: center; gap: 10px; }
  .status-dot { width: 9px; height: 9px; border-radius: 50%;
                background: #4ade80; flex-shrink: 0; }
  .status-dot.offline { background: #f87171; }
  .messages { flex: 1; overflow-y: auto; padding: 16px; display: flex;
              flex-direction: column; gap: 10px; }
  .bubble { max-width: 78%; padding: 10px 14px; border-radius: 18px;
            font-size: 14px; line-height: 1.5; white-space: pre-wrap; word-wrap: break-word; }
  .bubble.bot  { background: #f0f0f0; color: #222;
                 border-radius: 18px 18px 18px 4px; align-self: flex-start; }
  .bubble.user { background: #0084ff; color: #fff;
                 border-radius: 18px 18px 4px 18px; align-self: flex-end; }
  .bubble.system { background: #fff3cd; color: #856404; font-size: 12px;
                   border-radius: 8px; align-self: center; padding: 6px 12px; }
  .typing { display: none; align-self: flex-start; }
  .typing span { display: inline-block; width: 8px; height: 8px; margin: 0 2px;
                 background: #bbb; border-radius: 50%;
                 animation: bounce 1.2s infinite; }
  .typing span:nth-child(2) { animation-delay: .2s; }
  .typing span:nth-child(3) { animation-delay: .4s; }
  @keyframes bounce { 0%,60%,100%{transform:translateY(0)} 30%{transform:translateY(-6px)} }
  .input-row { display: flex; gap: 8px; padding: 12px 16px;
               border-top: 1px solid #eee; background: #fff; }
  textarea { flex: 1; border: 1px solid #ddd; border-radius: 22px;
             padding: 10px 16px; font-size: 14px; resize: none;
             outline: none; font-family: inherit; max-height: 100px; }
  textarea:focus { border-color: #0084ff; }
  button { background: #0084ff; color: #fff; border: none;
           border-radius: 50%; width: 42px; height: 42px; cursor: pointer;
           font-size: 18px; flex-shrink: 0; align-self: flex-end; }
  button:hover { background: #006fd6; }
  button:disabled { background: #ccc; cursor: default; }
  .hints { padding: 4px 16px 8px; display: flex; gap: 6px; flex-wrap: wrap; }
  .hint { font-size: 11px; background: #f0f4ff; color: #0056b3;
          border-radius: 12px; padding: 3px 10px; cursor: pointer;
          border: 1px solid #c8d8ff; }
  .hint:hover { background: #dce8ff; }
</style>
</head>
<body>
<div class="chat-wrapper">
  <div class="header">
    <div class="status-dot" id="dot"></div>
    🥗 COTD — Calorie Tracker
  </div>
  <div class="messages" id="messages">
    <div class="bubble system">Connected. Tell me what you ate!</div>
  </div>
  <div class="typing" id="typing"><span></span><span></span><span></span></div>
  <div class="hints">
    <span class="hint" onclick="send('my last meal')">Last meal</span>
    <span class="hint" onclick="send('today summary')">Today summary</span>
    <span class="hint" onclick="send('skip')">Skip question</span>
  </div>
  <div class="input-row">
    <textarea id="inp" rows="1" placeholder="Describe your meal…"></textarea>
    <button id="btn" onclick="send()">▶</button>
  </div>
</div>

<script>
const sid = localStorage.getItem("cotd_sid") || crypto.randomUUID();
localStorage.setItem("cotd_sid", sid);

const msgs   = document.getElementById("messages");
const inp    = document.getElementById("inp");
const btn    = document.getElementById("btn");
const typing = document.getElementById("typing");
const dot    = document.getElementById("dot");

const ws = new WebSocket(`ws://${location.host}/ws/${sid}`);

ws.onopen = () => {
  dot.classList.remove("offline");
};
ws.onclose = () => {
  dot.classList.add("offline");
  addBubble("system", "Disconnected. Refresh to reconnect.");
};
ws.onmessage = (e) => {
  typing.style.display = "none";
  btn.disabled = false;
  const data = JSON.parse(e.data);
  addBubble("bot", data.text);
};

function addBubble(role, text) {
  const d = document.createElement("div");
  d.className = `bubble ${role}`;
  d.textContent = text;
  msgs.appendChild(d);
  msgs.scrollTop = msgs.scrollHeight;
}

function send(text) {
  const msg = (text || inp.value).trim();
  if (!msg || ws.readyState !== WebSocket.OPEN) return;
  inp.value = "";
  inp.style.height = "auto";
  addBubble("user", msg);
  typing.style.display = "flex";
  msgs.scrollTop = msgs.scrollHeight;
  btn.disabled = true;
  ws.send(msg);
}

inp.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
inp.addEventListener("input", () => {
  inp.style.height = "auto";
  inp.style.height = Math.min(inp.scrollHeight, 100) + "px";
});
</script>
</body>
</html>"""

# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI()

@app.get("/")
async def root():
    return HTMLResponse(HTML)

@app.websocket("/ws/{session_id}")
async def ws_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    state = SESSIONS.setdefault(session_id, ConversationState())
    loop = asyncio.get_event_loop()
    try:
        while True:
            text  = await websocket.receive_text()
            # Run blocking agents (Ollama + USDA HTTP) in a thread so the event loop stays free
            reply = await loop.run_in_executor(_executor, process, state, text)
            await websocket.send_json({"text": reply, "history_len": len(state.history)})
    except WebSocketDisconnect:
        pass   # session kept in SESSIONS — reconnect restores full history

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)

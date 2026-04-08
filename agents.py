"""
agents.py — single source of truth for all COTD agent logic.
Imported by both chainlit_app.py and the notebook.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional, List
import json, re, os, sqlite3
from pathlib import Path

import httpx, requests
from dotenv import load_dotenv

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
    vitamin_a_mcg: Optional[float] = None
    vitamin_c_mg: Optional[float] = None
    calcium_mg: Optional[float] = None
    iron_mg: Optional[float] = None
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
def ollama_chat(model: str, messages: list, temperature: float = 0.0) -> str:
    payload = {"model": model, "messages": messages, "stream": False,
               "options": {"temperature": temperature}}
    resp = httpx.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()

def extract_json(text: str) -> dict:
    text = re.sub(r'```(?:json)?\s*', '', text)
    text = re.sub(r'```', '', text)
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
- TEXT_AGENT   → user describes food in text (ingredients, meal description, etc.)

Respond with ONLY one of these two words: IMAGE_AGENT or TEXT_AGENT"""

def router_agent(user_message: str, has_image: bool = False) -> str:
    if has_image:
        return "IMAGE_AGENT"
    reply = ollama_chat(ROUTER_MODEL,
                        [{"role": "system", "content": ROUTER_SYSTEM},
                         {"role": "user",   "content": user_message}])
    return "IMAGE_AGENT" if "IMAGE_AGENT" in reply.upper() else "TEXT_AGENT"

# ── Text agent ─────────────────────────────────────────────────────────────────
TEXT_AGENT_MODEL  = "qwen2.5:3b-instruct"
TEXT_AGENT_SYSTEM = """You are a food ingredient parser. Extract all food ingredients from the user's message.

Rules:
- "name" = the food item ONLY (e.g. "banana", "greek yogurt", "honey"). NEVER include measurement words like "cup", "tbsp", "tsp", "g" in the name.
- "amount" = numeric value (float). If unknown, use null.
- "unit" = "g", "ml", "piece", "cup", "tbsp", "tsp". If unknown, use null.
- "cooking_method" = "grilled", "raw", "steamed", etc. If unknown, use null.
- if you don't know the unit or the food is 99% that the amount is 0, do not include in the output.

EXAMPLE:
Input: "1 banana, 1 cup Greek yogurt, 1 tbsp honey"
Output:
{
  "ingredients": [
    {"name": "banana", "amount": 1, "unit": "piece", "cooking_method": null},
    {"name": "greek yogurt", "amount": 1, "unit": "cup", "cooking_method": null},
    {"name": "honey", "amount": 1, "unit": "tbsp", "cooking_method": null}
  ]
}

EXAMPLE (Chinese input → English output):
Input: "一个香蕉，一杯希腊酸奶，没吃蜂蜜"
Output:
{
  "ingredients": [
    {"name": "banana", "amount": 1, "unit": "piece", "cooking_method": null},
    {"name": "greek yogurt", "amount": 1, "unit": "cup", "cooking_method": null}
  ]
}

Respond ONLY with valid JSON. No markdown, no explanation."""

def text_agent(user_message: str) -> List[Ingredient]:
    reply = ollama_chat(TEXT_AGENT_MODEL,
                        [{"role": "system", "content": TEXT_AGENT_SYSTEM},
                         {"role": "user",   "content": user_message}])
    reply = re.sub(r'```(?:json)?\s*', '', reply)
    reply = re.sub(r'```', '', reply)
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

For EACH ingredient assess:
- Is the name specific enough?
- Is there a numeric amount? (null = missing = low confidence)
- Is the unit standard? ("handful" or "some" = vague)
- For proteins and oils: is cooking method specified?

Scoring per ingredient:
- Amount missing OR unit vague → confidence 0.3
- Non-standard unit (handful, some, a bit) → confidence 0.6
- Missing cooking method for protein/oil → confidence 0.8
- Everything clear → confidence 1.0

overall_completeness = mean of all ingredient confidences

Respond ONLY with valid JSON:
{
  "ingredient_scores": [{"name": "...", "confidence": 0.0, "issue": "...or null"}],
  "completeness_score": 0.0,
  "next_question": "...one specific question, or null"
}

CRITICAL RULE: if completeness_score < 0.75 you MUST write a question in next_question. It must NEVER be null when score < 0.75."""

def reasoning_agent(ingredients: List[Ingredient]) -> dict:
    ing_list = [{"name": i.name, "amount": i.amount if i.amount > 0 else None,
                 "unit": i.unit, "cooking_method": i.cooking_method} for i in ingredients]
    reply = ollama_chat(REASONING_MODEL,
                        [{"role": "system", "content": REASONING_SYSTEM},
                         {"role": "user",   "content": f"Ingredients: {json.dumps(ing_list)}"}])
    result = extract_json(reply)

    # Override completeness with our own calculation based on actual ingredient state.
    # Small models (1.5b) often score complete ingredients as incomplete.
    _VALID_UNITS = set(UNIT_TO_GRAMS.keys()) if 'UNIT_TO_GRAMS' in dir() else {
        "g","ml","kg","l","oz","lb","cup","tbsp","tsp","piece","medium","large","small","unknown"
    }
    def _ing_confidence(ing: Ingredient) -> float:
        if ing.amount <= 0: return 0.3
        if ing.unit not in _VALID_UNITS: return 0.6
        return 1.0
    real_score = sum(_ing_confidence(i) for i in ingredients) / len(ingredients) if ingredients else 1.0
    # Use the higher of model score and real score so we don't ask unnecessary questions
    result["completeness_score"] = max(result.get("completeness_score", 0.0), real_score)

    if result["completeness_score"] < 0.75:
        # Find ingredient that actually needs info (amount missing or unit vague)
        needs_info = [i for i in ingredients if i.amount <= 0 or i.unit not in _VALID_UNITS]
        if needs_info:
            target = needs_info[0]
            if target.amount <= 0:
                result["next_question"] = f"How much {target.name} did you have? (e.g. 1 cup, 200g, 1 plate)"
            else:
                result["next_question"] = f"Can you be more specific about how much {target.name}? (e.g. grams or cups)"
        else:
            # Model thinks incomplete but all ingredients have amount+unit — trust real score
            result["completeness_score"] = 1.0
            result["next_question"] = None
    return result

# ── Nutrition lookup ───────────────────────────────────────────────────────────
NUTRIENT_IDS = {
    "calories": 1008, "protein_g": 1003, "fat_g": 1004, "carbs_g": 1005,
    "fiber_g": 1079,  "sugar_g": 2000,   "sodium_mg": 1093, "fat_saturated_g": 1258,
}
FALLBACK_GENERICS = {
    "vegetable": NutritionData(calories=30,  protein_g=2,   fat_g=0.3, carbs_g=5,   api_source="fallback_generic", confidence=0.3),
    "fruit":     NutritionData(calories=60,  protein_g=0.8, fat_g=0.2, carbs_g=15,  api_source="fallback_generic", confidence=0.3),
    "meat":      NutritionData(calories=150, protein_g=26,  fat_g=5,   carbs_g=0,   api_source="fallback_generic", confidence=0.3),
    "grain":     NutritionData(calories=130, protein_g=4,   fat_g=1,   carbs_g=27,  api_source="fallback_generic", confidence=0.3),
    "oil":       NutritionData(calories=884, protein_g=0,   fat_g=100, carbs_g=0,   api_source="fallback_generic", confidence=0.3),
    "dairy":     NutritionData(calories=61,  protein_g=3.2, fat_g=3.3, carbs_g=4.8, api_source="fallback_generic", confidence=0.3),
    "default":   NutritionData(calories=100, protein_g=3,   fat_g=3,   carbs_g=12,  api_source="fallback_generic", confidence=0.2),
}
UNIT_TO_GRAMS = {
    "g": 1.0, "ml": 1.0, "kg": 1000.0, "l": 1000.0,
    "oz": 28.35, "lb": 453.6, "cup": 240.0, "tbsp": 15.0, "tsp": 5.0,
    "piece": 100.0, "medium": 100.0, "large": 150.0, "small": 70.0, "unknown": 100.0,
}
_PROCESSED = {"breaded","fried","dehydrated","powder","dried","frozen",
              "canned","mix","flavored","instant","flour","unenriched"}

def unit_to_grams(amount: float, unit: str) -> float:
    return amount * UNIT_TO_GRAMS.get(unit.lower(), 100.0)

def _extract_nutrients(food_item: dict) -> NutritionData:
    nutrients = {n["nutrientId"]: n.get("value", 0.0) for n in food_item.get("foodNutrients", [])}
    return NutritionData(
        calories=nutrients.get(NUTRIENT_IDS["calories"], 0.0),
        protein_g=nutrients.get(NUTRIENT_IDS["protein_g"], 0.0),
        fat_g=nutrients.get(NUTRIENT_IDS["fat_g"], 0.0),
        carbs_g=nutrients.get(NUTRIENT_IDS["carbs_g"], 0.0),
        fiber_g=nutrients.get(NUTRIENT_IDS["fiber_g"]),
        sugar_g=nutrients.get(NUTRIENT_IDS["sugar_g"]),
        sodium_mg=nutrients.get(NUTRIENT_IDS["sodium_mg"]),
        fat_saturated_g=nutrients.get(NUTRIENT_IDS["fat_saturated_g"]),
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

def _usda_search(query: str) -> Optional[NutritionData]:
    try:
        r = requests.get(USDA_SEARCH_URL,
                         params={"query": query, "pageSize": 20, "api_key": USDA_API_KEY,
                                 "dataType": ["Foundation", "SR Legacy"]}, timeout=10)
        if r.status_code == 429:
            print("  [USDA] Rate limit — get a free key at fdc.nal.usda.gov/api-key-signup.html")
            return None
        r.raise_for_status()
        foods = r.json().get("foods", [])
        if not foods:
            return None
        q_words = query.lower().split()
        def _score(food):
            desc = food.get("description", "").lower()
            hits    = sum(1 for w in q_words if w in desc)
            first   = 2 if any(desc.startswith(w) for w in q_words) else 0
            penalty = -3 * len(_PROCESSED & set(desc.split(",")[0].split()))
            return (first + hits + penalty, -len(desc))
        return _extract_nutrients(max(foods, key=_score))
    except Exception as e:
        print(f"  [USDA warn] {e}")
        return None

def _get_fallback(name: str) -> NutritionData:
    n = name.lower()
    if any(w in n for w in ["oil","butter","lard"]):                                      return FALLBACK_GENERICS["oil"]
    if any(w in n for w in ["chicken","beef","pork","fish","salmon","tuna","turkey","lamb"]): return FALLBACK_GENERICS["meat"]
    if any(w in n for w in ["milk","cheese","yogurt","cream","dairy"]):                   return FALLBACK_GENERICS["dairy"]
    if any(w in n for w in ["rice","pasta","bread","oat","grain","flour","cereal"]):      return FALLBACK_GENERICS["grain"]
    if any(w in n for w in ["apple","banana","orange","berry","grape","mango","fruit"]):  return FALLBACK_GENERICS["fruit"]
    if any(w in n for w in ["broccoli","spinach","carrot","lettuce","tomato","pepper","onion","vegetable"]): return FALLBACK_GENERICS["vegetable"]
    return FALLBACK_GENERICS["default"]

# ── Database ───────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT DEFAULT 'local',
    meal_timestamp DATETIME, meal_date DATE, meal_type TEXT,
    mood TEXT, notes TEXT,
    total_calories REAL, total_protein_g REAL, total_fat_g REAL,
    total_carbs_g REAL, total_fiber_g REAL,
    api_fallback_count INTEGER DEFAULT 0,
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
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.executescript(SCHEMA)
    return conn

def _cache_get(conn: sqlite3.Connection, name: str) -> Optional[NutritionData]:
    row = conn.execute("SELECT nutrition_data FROM ingredient_cache WHERE ingredient_name=?",
                       (name.lower(),)).fetchone()
    if row:
        d = json.loads(row[0])
        # Filter to only known fields to handle cache entries from older versions
        known = {f for f in NutritionData.__dataclass_fields__}
        return NutritionData(**{k: v for k, v in d.items() if k in known})
    return None

def _cache_set(conn: sqlite3.Connection, name: str, nutrition: NutritionData) -> None:
    conn.execute("INSERT OR REPLACE INTO ingredient_cache "
                 "(ingredient_name, amount_value, unit, nutrition_data, cached_at) "
                 "VALUES (?, 100, 'g', ?, CURRENT_TIMESTAMP)",
                 (name.lower(), json.dumps(nutrition.__dict__)))
    conn.commit()

def nutrition_lookup_agent(ingredients: List[Ingredient], conn: sqlite3.Connection) -> List[Ingredient]:
    for ing in ingredients:
        grams = unit_to_grams(ing.amount, ing.unit)
        if grams <= 0:
            grams = 100.0
        query = ing.name + (f" {ing.cooking_method}" if ing.cooking_method else "")
        cached = _cache_get(conn, ing.name)
        if cached:
            ing.nutrition = _scale(cached, grams)
            print(f"  [cache]    {ing.name}: {ing.nutrition.calories} kcal")
            continue
        base = _usda_search(query)
        if base:
            _cache_set(conn, ing.name, base)
            ing.nutrition = _scale(base, grams)
            print(f"  [USDA]     {ing.name}: {ing.nutrition.calories} kcal")
        else:
            base = _get_fallback(ing.name)
            ing.nutrition = _scale(base, grams)
            ing.confidence = min(ing.confidence, 0.4)
            ing.source = "fallback_average"
            print(f"  [fallback] {ing.name}: {ing.nutrition.calories} kcal (generic)")
    return ingredients

def save_meal(meal: Meal, conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO meals (meal_timestamp,meal_date,meal_type,mood,notes,"
        "total_calories,total_protein_g,total_fat_g,total_carbs_g,total_fiber_g,api_fallback_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (meal.meal_timestamp.isoformat(), meal.meal_date.isoformat(), meal.meal_type,
         meal.mood, meal.notes,
         meal.total_calories, meal.total_protein_g, meal.total_fat_g,
         meal.total_carbs_g, meal.total_fiber_g, meal.api_fallback_count))
    meal_id = cur.lastrowid
    for ing in meal.ingredients:
        n = ing.nutrition
        conn.execute(
            "INSERT INTO meal_ingredients (meal_id,ingredient_name,amount_value,unit,cooking_method,"
            "calories,protein_g,fat_g,carbs_g,fiber_g,api_source,confidence,source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (meal_id, ing.name, ing.amount, ing.unit, ing.cooking_method,
             n.calories if n else 0, n.protein_g if n else 0, n.fat_g if n else 0,
             n.carbs_g if n else 0, n.fiber_g if n else 0,
             n.api_source if n else "none", ing.confidence, ing.source))
    conn.commit()
    meal.id = meal_id
    return meal_id

def get_daily_summary(target_date: date, conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT SUM(total_calories),SUM(total_protein_g),SUM(total_fat_g),SUM(total_carbs_g),COUNT(*) "
        "FROM meals WHERE meal_date=?", (target_date.isoformat(),)).fetchone()
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

def _apply_user_clarification(ingredients: List[Ingredient], user_reply: str, question: str) -> List[Ingredient]:
    """Parse clarification reply and update matching ingredients. Falls back to regex."""
    prompt = (f"A user was asked: '{question}'\nThey replied: '{user_reply}'\n\n"
              "Extract any food amounts or cooking methods mentioned. "
              'Respond ONLY with JSON like:\n{"updates": [{"name": "...", "amount": ..., "unit": "...", "cooking_method": "..."}]}')
    applied = False
    try:
        reply   = ollama_chat(TEXT_AGENT_MODEL, [{"role": "user", "content": prompt}])
        updates = extract_json(reply).get("updates", [])
        for upd in updates:
            for ing in ingredients:
                if upd["name"].lower() in ing.name.lower() or ing.name.lower() in upd["name"].lower():
                    if upd.get("amount"): ing.amount = float(upd["amount"]); applied = True
                    if upd.get("unit"):   ing.unit = upd["unit"]
                    if upd.get("cooking_method"): ing.cooking_method = upd["cooking_method"]
                    ing.source = "user_input"; ing.confidence = 0.9
    except Exception:
        pass
    # Regex fallback if LLM extracted nothing
    if not applied:
        m = re.search(r'(\d+(?:\.\d+)?)\s*(g|kg|ml|l|cup|tbsp|tsp|piece|oz|lb)?',
                      user_reply, re.IGNORECASE)
        if m:
            target = min(ingredients, key=lambda i: i.amount)
            target.amount = float(m.group(1))
            target.unit   = (m.group(2) or "g").lower()
            target.source = "user_input"; target.confidence = 0.85
    return ingredients

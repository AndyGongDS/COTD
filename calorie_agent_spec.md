# Calorie Tracking AI Agent - Requirements Specification

**Last Updated:** April 2026  
**Status:** Ready for Implementation  
**Target Environment:** Mac Mini M4 (16GB), Local LLMs

---

## 1. EXECUTIVE SUMMARY

A multi-modal calorie tracking agent that accepts **food photos or text descriptions** and uses local reasoning-capable LLMs to:
- Identify ingredients intelligently through image analysis or text parsing
- Gather missing nutritional information via agentic questioning
- Query a nutrition API for macro/micronutrient data
- Store results with metadata (timestamp, date, meal type)
- Run entirely on local hardware using 3B parameter models

The system **must reason about completeness** (do we have enough ingredient data?) and **fail gracefully** (use averages if user skips/refuses detailed input).

---

## 2. SYSTEM ARCHITECTURE

### 2.1 High-Level Flow

```
USER INPUT (photo OR text)
    ↓
[ROUTER AGENT] - Decide: Image or Text?
    ↓
    ├─→ IMAGE AGENT (if photo)
    │   ├─ Vision analysis
    │   ├─ Ingredient extraction
    │   └─ Agentic questioning loop
    │
    └─→ TEXT AGENT (if description)
        ├─ NLP ingredient parsing
        └─ Agentic questioning loop
    ↓
[REASONING AGENT] - Check completeness
    ├─ Do we have all ingredient amounts?
    ├─ Are units standardized?
    └─ Is reasoning sufficient to proceed?
    ↓
[NUTRITION LOOKUP AGENT] - Fetch data
    ├─ Query USDA FoodData Central API
    ├─ Aggregate macro/micronutrients
    └─ Handle missing entries (fallback to generics)
    ↓
[DATABASE AGENT] - Record & Persist
    ├─ Timestamp, meal type, date
    ├─ All ingredients + amounts
    ├─ Nutritional breakdown
    └─ User session metadata
    ↓
SUMMARY & VISUALIZATION
```

---

## 3. COMPONENT SPECIFICATIONS

### 3.1 ROUTER AGENT

**Input:** User message (text) + optional attachment (image file)  
**Decision Logic:**
- If message contains URL or file path to image → IMAGE_AGENT
- If message contains food description text → TEXT_AGENT
- If both → Ask user for preference (or process both sequentially)

**Output:** Route decision + preprocessed input for downstream agent

---

### 3.2 IMAGE AGENT

**Input:** Food photo (PNG/JPG)  
**Process:**

1. **Vision Analysis** (local 3B vision model, e.g., Qwen 2.5-VL, LLaVA-NeXT)
   - Identify all visible food components
   - Estimate visual quantities (small/medium/large portion)
   - Flag ambiguous items

   **Output Example:**
   ```
   Identified components:
   - Broccoli (medium florets, ~80g estimated)
   - Chicken breast (sliced, ~120-150g estimated)
   - Olive oil (light coating, unknown exact amount)
   ```

2. **Agentic Questioning Loop** (reasoning model with native agents, e.g., Qwen 2.5-Math/QwQ-1B if available, or Llama 3.2 3B with extended thinking)
   
   **Reasoning Process:**
   - Analyze extracted components against completeness checklist:
     * ✓ Ingredient identified
     * ✓ Quantity estimated or actual?
     * ✓ Unit clarity (grams, cups, pieces)?
     * ✓ Cooking method affects calories (raw vs grilled)?
   
   - **If missing info:**
     - Ask user specific questions one at a time
     - Example: "I see broccoli in the photo. How much broccoli did you eat? (Approximate in grams or by count, e.g., '8 florets')"
     - Capture response and re-evaluate
   
   - **Repeat until:**
     - All components have clear quantities, OR
     - User declines/skips (auto-use visual estimate or USDA average for portion)

   **Output:** Structured ingredient list with confidence scores
   ```json
   {
     "ingredients": [
       {"name": "broccoli", "amount": 80, "unit": "g", "confidence": 0.95},
       {"name": "chicken breast", "amount": 145, "unit": "g", "confidence": 0.92},
       {"name": "olive oil", "amount": 15, "unit": "ml", "confidence": 0.50, "note": "visual estimate"}
     ],
     "completeness_score": 0.85
   }
   ```

---

### 3.3 TEXT AGENT

**Input:** Text description of food (e.g., "100g of grilled chicken, side salad, 2 tbsp dressing")  
**Process:**

1. **NLP Ingredient Parsing**
   - Extract ingredient name, quantity, unit
   - Normalize units (tbsp → ml, cups → grams where possible)
   - Identify structured entries vs. free-form descriptions

   **Example Parsed:**
   ```
   "100g grilled chicken" → {"name": "chicken", "amount": 100, "unit": "g", "method": "grilled"}
   "2 tbsp salad dressing" → {"name": "salad dressing", "amount": 30, "unit": "ml"}
   ```

2. **Agentic Questioning Loop** (same as Image Agent)
   - Reasoning agent checks for completeness
   - Ask user for missing quantities/clarifications
   - Handle ambiguities (e.g., "salad" = mixed greens? Include quantification)

   **Output:** Same structured list as Image Agent

---

### 3.4 REASONING AGENT (Completeness Checker)

**Inputs:** Extracted ingredients + confidence metadata  
**Logic:**

```
FOR each ingredient:
  IF amount is missing OR unit is vague:
    confidence = 0.3
    action = "ASK_USER"
  ELIF unit is non-standard (e.g., "handful"):
    confidence = 0.6
    action = "REQUEST_CLARIFICATION"
  ELIF cooking method affects nutrition (e.g., grilled vs fried):
    confidence = 0.8
    action = "CONFIRM_METHOD"
  ELSE:
    confidence = 1.0
    action = "PROCEED"

overall_completeness = mean(confidence across all ingredients)

IF overall_completeness >= 0.75:
  PROCEED to nutrition lookup
ELSE:
  GENERATE questions and ASK_USER
  
  WHILE user provides responses:
    UPDATE ingredient data
    RECALCULATE completeness
  
  IF user skips/refuses:
    USE fallback logic:
      - If visual estimate exists: use median portion size
      - Else: use USDA average serving size for ingredient
```

**Example Fallback:**
```
User refuses to specify broccoli amount.
Agent: "OK, I'll use a standard medium serving: 90g"
(reasoning documented for transparency)
```

---

### 3.5 NUTRITION LOOKUP AGENT

**API:** USDA FoodData Central  
**Inputs:** Ingredient name, amount, unit

**Process:**

1. **Query USDA API**
   ```
   GET /fdc/v1/foods/search?query=broccoli&pageSize=5
   ```
   - Fetch top matches by relevance
   - Filter by food type (raw vs cooked, if specified)
   - Return nutrition data per 100g

2. **Aggregate Macros/Micros**
   - Calories (kcal)
   - Protein (g)
   - Fat (g) – total, saturated, trans
   - Carbs (g) – total, fiber, sugars
   - Sodium (mg)
   - Optional: Vitamin A, C, Iron, Calcium, etc.

3. **Scale to User Portion**
   ```
   user_portion = amount_value * unit_converter[unit]
   kcal_total = (kcal_per_100g / 100) * user_portion
   ```

4. **Handle API Failures**
   - If USDA returns no match: fuzzy search on similar names
   - If still no match: use fallback generic (e.g., "average vegetable" = 30 kcal/100g)
   - Log fallback decision with lower confidence flag

**Output:**
```json
{
  "ingredient": "broccoli",
  "amount": 80,
  "unit": "g",
  "api_source": "USDA FDC",
  "nutrition": {
    "calories": 24,
    "protein_g": 2.4,
    "fat_g": 0.4,
    "carbs_g": 4.3,
    "fiber_g": 0.9,
    "sodium_mg": 52
  },
  "confidence": 0.95
}
```

---

### 3.6 DATABASE AGENT

**Database:** SQLite (local, no external dependencies)  
**Schema:**

```sql
CREATE TABLE meals (
  id INTEGER PRIMARY KEY,
  user_id TEXT,
  meal_timestamp DATETIME,
  meal_date DATE,
  meal_type TEXT,  -- breakfast, lunch, dinner, snack
  mood TEXT,  -- optional: how they felt eating
  notes TEXT,
  total_calories INT,
  total_protein_g FLOAT,
  total_fat_g FLOAT,
  total_carbs_g FLOAT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE meal_ingredients (
  id INTEGER PRIMARY KEY,
  meal_id INTEGER,
  ingredient_name TEXT,
  amount_value FLOAT,
  unit TEXT,
  calories INT,
  protein_g FLOAT,
  fat_g FLOAT,
  carbs_g FLOAT,
  fiber_g FLOAT,
  api_source TEXT,
  confidence FLOAT,
  FOREIGN KEY (meal_id) REFERENCES meals(id)
);

CREATE TABLE ingredient_cache (
  id INTEGER PRIMARY KEY,
  ingredient_name TEXT UNIQUE,
  amount_value INT,  -- assumes 100g or 1 unit
  unit TEXT,
  nutrition_data JSON,  -- cached from USDA
  cached_at DATETIME
);
```

**Insertion Logic:**
1. Create `meals` record with timestamp, meal type, notes
2. For each ingredient, create `meal_ingredients` entry
3. Sum macros and insert into `meals.total_*` fields
4. Cache ingredient nutrition data for future queries

---

## 4. TECHNICAL STACK

### 4.1 Local LLM Models (Mac Mini M4, 16GB RAM)

**Critical:** Choose models with **native agent/reasoning capabilities** and **<3B parameters** (to run comfortably on 16GB with other processes).

#### Recommended Lineup:

| Role | Model | Params | Key Features | Notes |
|------|-------|--------|--------------|-------|
| **Router** | Llama 3.2 1B | 1B | Fast classification | Determines image vs text |
| **Text Parser** | Qwen 2.5 3B | 3B | Strong instruction-following, multilingual | Ingredient extraction |
| **Image Analysis** | LLaVA-NeXT 2B (or Qwen 2.5-VL) | 2B | Vision + reasoning | Identifies food components |
| **Reasoning/Agent** | Llama 3.2 3B Instruct | 3B | Extended thinking potential, agent-capable | Completeness checking, interactive questioning |
| **Nutrition Lookup** | Qwen 2.5 0.5B | 0.5B | Fast API response formatting | Lightweight for deterministic queries |

**Alternative Stack (if extended thinking available):**
- Qwen QwQ-1B (reasoning-focused, ~1B effective)
- Deepseek-R1-Distill-Qwen-1.5B (reasoning + speed)

**Installation & Runtime:**
- Use **Ollama** or **LM Studio** as the inference server
- Models loaded on-demand into VRAM
- Quantized to Q4_K_M (4-bit) for ~6-8GB footprint per model
- Rotate models in/out of memory as agents run sequentially

### 4.2 Framework & API Integration

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| **Orchestration** | Python 3.11+ with Langgraph / LangChain | Agent state management, prompt chaining |
| **Vision Processing** | Ollama API (local LLaVA-NeXT) | No cloud dependency |
| **NLP & Parsing** | Qwen 2.5 via Ollama (or spaCy for lightweight NER) | Fast, local, multilingual |
| **Reasoning Loop** | Llama 3.2 3B with multi-turn conversation | Track state, maintain context |
| **Nutrition API** | USDA FoodData Central (free, public) | Authoritative, no API key for basic search |
| **Database** | SQLite3 (Python built-in) | No server, no external dependency |
| **Caching** | Python `functools.lru_cache` + SQLite `ingredient_cache` | Minimize redundant API calls |

### 4.3 Local Setup Instructions (Template)

```bash
# Install Ollama
brew install ollama

# Start Ollama daemon
ollama serve &

# Pull models into local registry
ollama pull llama2:1b
ollama pull qwen:3b
ollama pull llava-neXT:2b

# Python environment
python -m venv venv
source venv/bin/activate
pip install langchain langgraph ollama pydantic sqlite3 requests

# Test connectivity
curl http://localhost:11434/api/generate -d '{"model":"llama2:1b","prompt":"test"}'
```

---

## 5. AGENT INTERACTION FLOW (Detailed)

### 5.1 Image Input Flow

```
User sends: [photo of grilled chicken + broccoli]

↓ ROUTER AGENT (Llama 3.2 1B)
  Decision: IMAGE_AGENT
  
↓ IMAGE AGENT (LLaVA-NeXT 2B)
  Vision Analysis:
  "I see a plate with grilled chicken breast (appears to be ~4-5 oz)
   and steamed broccoli florets (approximately 1 cup worth).
   Unclear: amount of any oil/seasoning used."
  
↓ REASONING AGENT (Llama 3.2 3B)
  Checks completeness:
  - Chicken: identified, portion estimate, high confidence
  - Broccoli: identified, portion estimate, high confidence
  - Oil/seasoning: not visible or unclear
  
  Overall completeness: 0.85
  Decision: PROCEED with 1 clarification question
  
  Generated question:
  "I see the chicken and broccoli clearly. Was any oil
   (olive, butter, etc.) used in cooking?"

↓ USER RESPONSE (interaction)
  "Yes, about 1 teaspoon of olive oil for the whole plate"
  
↓ REASONING AGENT (re-check)
  Updated completeness: 0.95
  Decision: PROCEED to nutrition lookup
  
↓ NUTRITION LOOKUP AGENT
  Query USDA for:
  - "chicken breast, grilled, 145g" → 245 kcal, 45g protein, 5g fat
  - "broccoli, steamed, 90g" → 26 kcal, 2.4g protein, 0.4g fat
  - "olive oil, 5ml" → 45 kcal, 0g protein, 5g fat
  
  Total: 316 kcal, 47.4g protein, 10.4g fat

↓ DATABASE AGENT
  Insert meal record:
  {
    meal_type: "lunch",
    meal_timestamp: "2026-04-07 12:30:00",
    total_calories: 316,
    ingredients: [
      {name: "chicken breast", amount: 145, unit: "g", calories: 245},
      {name: "broccoli", amount: 90, unit: "g", calories: 26},
      {name: "olive oil", amount: 5, unit: "ml", calories: 45}
    ]
  }

↓ OUTPUT TO USER
  "Meal recorded! 316 kcal, 47.4g protein, 10.4g fat, 8g carbs"
```

### 5.2 Text Input Flow

```
User sends: "Had a morning smoothie: 1 banana, 1 cup Greek yogurt, 1 tbsp honey"

↓ ROUTER AGENT
  Decision: TEXT_AGENT
  
↓ TEXT AGENT (Qwen 2.5 3B)
  NLP Parsing:
  - "1 banana" → {name: "banana", amount: 1, unit: "medium"}
  - "1 cup Greek yogurt" → {name: "greek yogurt", amount: 240, unit: "ml"}
  - "1 tbsp honey" → {name: "honey", amount: 15, unit: "ml"}
  
↓ REASONING AGENT
  Completeness check:
  - Banana: standard medium ~120g, confidence 0.95
  - Greek yogurt: 1 cup = 240ml, confidence 0.95
  - Honey: 1 tbsp = 15ml, confidence 0.95
  
  Overall: 0.95 → PROCEED
  (No clarification questions needed)

↓ NUTRITION LOOKUP
  - banana (120g) → 107 kcal, 1.3g protein, 27g carbs
  - greek yogurt (200g) → 128 kcal, 18g protein, 7g carbs
  - honey (15ml) → 46 kcal, 0g protein, 12.5g carbs
  
  Total: 281 kcal, 19.3g protein, 46.5g carbs

↓ DATABASE AGENT
  Insert meal record
  
↓ OUTPUT
  "Smoothie logged! 281 kcal, 19g protein, 46g carbs"
```

### 5.3 Fallback/Refusal Flow

```
User sends photo but refuses to specify olive oil amount
"I don't want to guess"

↓ IMAGE AGENT extracts: chicken + broccoli clear, oil unknown
↓ REASONING AGENT asks: "How much oil did you use?"
↓ USER RESPONSE: "I'd rather skip that"
↓ REASONING AGENT (fallback logic)
  - Oil detected visually (light coating)
  - User refuses quantification
  - Use USDA average light cooking oil: 5ml
  - Document in confidence: 0.50, note: "user-refused, using average"

↓ NUTRITION LOOKUP continues with fallback
↓ DATABASE stores with confidence flag
↓ OUTPUT: "Logged with estimate on oil (5ml assumed)"
```

---

## 6. DATA STRUCTURES

### 6.1 Ingredient Object

```python
@dataclass
class Ingredient:
    name: str  # e.g., "chicken breast"
    amount: float  # e.g., 145
    unit: str  # e.g., "g", "ml", "piece"
    confidence: float  # 0.0–1.0, uncertainty measure
    source: str  # "user_input", "visual_estimate", "fallback_average"
    cooking_method: Optional[str]  # e.g., "grilled", "raw"
    notes: Optional[str]  # e.g., "light coating"
    
    # Populated after nutrition lookup:
    nutrition: Optional[NutritionData]
```

### 6.2 Meal Object

```python
@dataclass
class Meal:
    id: Optional[str]  # auto-generated
    meal_timestamp: datetime
    meal_date: date
    meal_type: str  # "breakfast", "lunch", "dinner", "snack"
    ingredients: List[Ingredient]
    total_calories: int
    total_protein_g: float
    total_fat_g: float
    total_carbs_g: float
    total_fiber_g: float
    mood: Optional[str]  # "energetic", "satisfied", etc.
    notes: Optional[str]
    api_fallback_count: int  # track how many ingredients used fallback
```

### 6.3 NutritionData Object

```python
@dataclass
class NutritionData:
    calories: int
    protein_g: float
    fat_g: float
    fat_saturated_g: Optional[float]
    carbs_g: float
    fiber_g: Optional[float]
    sugar_g: Optional[float]
    sodium_mg: Optional[float]
    
    # Optional micronutrients
    vitamin_a_mcg: Optional[float]
    vitamin_c_mg: Optional[float]
    calcium_mg: Optional[float]
    iron_mg: Optional[float]
    
    api_source: str  # "USDA_FDC", "fallback_generic", etc.
    confidence: float  # 0.0–1.0
```

---

## 7. ISSUES & MITIGATIONS

### 7.1 Identified Issues

| Issue | Severity | Mitigation |
|-------|----------|-----------|
| **VRAM Pressure** | HIGH | Load models sequentially; unload after each agent. Use quantization (Q4_K_M). Consider model merging (all agents → one 3B model). |
| **Slow Inference** | MEDIUM | Average latency per agent: 3–8s (typical for 3B models on CPU-accelerated M4). Accept trade-off for privacy. Consider MPS (Metal Performance Shaders) optimization in Ollama. |
| **Vision Ambiguity** | MEDIUM | Photos of mixed foods (e.g., casserole) are hard to parse. Mitigate: ask user to clarify "What are the main ingredients?" if confidence <0.6. |
| **USDA API Gaps** | MEDIUM | Not all foods in USDA database. Mitigate: fuzzy matching + fallback to generic category (vegetable, meat, grain). Document confidence. |
| **User Input Errors** | MEDIUM | User may misreport amounts (e.g., "cup" interpreted as 240ml but user meant 200ml). Mitigate: ask for clarification if unit is vague; show assumption. |
| **Cooking Method Variability** | MEDIUM | "Grilled" vs "fried" same ingredient = 2–3× calorie difference. Mitigate: always ask cooking method for major proteins. |
| **Multi-turn State Loss** | MEDIUM | If session crashes, incomplete meal is not saved. Mitigate: save intermediate state to SQLite after each reasoning step. |
| **Multilingual Support** | LOW | Users may mix Chinese/English. Mitigate: use Qwen 2.5 (multilingual) for text parsing; ask clarification in user's language. |

### 7.2 Recommended Mitigations (Prioritized)

**MUST-HAVE (before launch):**
1. Quantized model loading strategy (load/unload dance)
2. Fallback logic for missing USDA data
3. Confidence tracking in database
4. Intermediate state persistence

**SHOULD-HAVE (v1.1):**
1. MPS optimization for M4 inference
2. Fuzzy ingredient name matching (levenshtein distance)
3. Cooking method database (oil reduction factors)
4. Daily/weekly summary queries

**NICE-TO-HAVE (v2.0):**
1. Barcode scanning integration
2. Photos → ingredient extraction via OCR (for packaged foods)
3. User preferences (dietary restrictions, allergies)
4. Macros visualization dashboard

---

## 8. IMPLEMENTATION ROADMAP

### Phase 1: Core Agents (Week 1–2)

- [ ] Ollama setup & model pulls
- [ ] Router agent (text vs image classification)
- [ ] Text agent (NLP ingredient extraction)
- [ ] Image agent (LLaVA vision + ingredient list)
- [ ] Basic reasoning agent (completeness check, question generation)

### Phase 2: Nutrition & Database (Week 3)

- [ ] USDA API integration with caching
- [ ] SQLite schema & insertion logic
- [ ] Nutrition lookup agent (scaling portions, aggregation)
- [ ] Fallback logic for missing data

### Phase 3: Integration & UX (Week 4)

- [ ] End-to-end flow testing
- [ ] User interaction loop (questioning, responses, fallbacks)
- [ ] Error handling & logging
- [ ] CLI or web interface (optional)

### Phase 4: Optimization & Expansion (Week 5+)

- [ ] Model performance tuning (quantization, batching)
- [ ] Multi-language support
- [ ] Cooking method database
- [ ] Summary/analytics queries

---

## 9. SUCCESS CRITERIA

✅ **Functional Requirements:**
1. Accept food photos & text descriptions
2. Extract ingredients with amounts
3. Ask clarifying questions for missing data
4. Fall back to averages if user refuses
5. Query USDA API for nutrition data
6. Store results in SQLite with metadata

✅ **Non-Functional Requirements:**
1. Run entirely on Mac Mini M4 (no cloud)
2. End-to-end latency <30 seconds per meal
3. Confidence scores <0.5 or fallback noted in UI
4. No external API keys (USDA is free)
5. Support English + Chinese input (via Qwen)

✅ **Quality Metrics:**
1. Ingredient extraction accuracy >85% (spot-check)
2. USDA match success >80% (for common foods)
3. Zero crashes on intermediate state save
4. Log all fallback decisions for transparency

---

## 10. EXAMPLE PROMPTS FOR IMPLEMENTATION

### Router Prompt
```
You are a food input router. Determine if the user is providing:
1. A food PHOTO (image file, URL, or reference to image)
2. A FOOD DESCRIPTION (text listing ingredients)

Respond with ONLY: "IMAGE_AGENT" or "TEXT_AGENT"

User input: [insert user message here]
```

### Image Agent Prompt
```
You are a food vision analyst. Examine this photo and extract all visible food components.

For each component, provide:
- Name (e.g., "broccoli", "chicken breast")
- Visual quantity estimate (small/medium/large or approximate grams)
- Confidence (0–100%)
- Notes (e.g., "grilled", "raw", "unclear")

[image provided]

Format output as JSON:
{
  "components": [
    {"name": "...", "visual_estimate": "...", "confidence": "...%", "notes": "..."}
  ]
}
```

### Reasoning Agent Prompt
```
You are a meal completeness checker. Evaluate if we have enough ingredient data to look up nutrition.

For each ingredient, check:
- Is the name clear?
- Is the amount in grams or a standard unit?
- Is the cooking method specified (if it matters)?

If anything is missing or ambiguous, generate ONE clarifying question.

Current ingredients:
[JSON list provided]

Respond with:
{
  "completeness_score": 0.0–1.0,
  "issues": ["..."],
  "next_question": "..." or null
}

If score >= 0.75: set "next_question" to null (proceed).
If score < 0.75: generate a specific, single question.
```

### Nutrition Lookup Prompt
```
You are a nutrition data formatter. Given ingredient name, amount, and unit,
construct a query for the USDA FoodData Central API and format the result.

Input:
- name: "broccoli"
- amount: 90
- unit: "g"

1. Query USDA (via Python function call)
2. Extract macros from top match
3. Scale to user amount
4. Return JSON with calories, protein, fat, carbs, confidence

Output:
{
  "ingredient": "...",
  "amount": ...,
  "unit": "...",
  "nutrition": {...},
  "confidence": 0.0–1.0,
  "source": "USDA_FDC" or "fallback"
}
```

---

## 11. APPENDIX: MODEL SELECTION RATIONALE

### Why 3B Models?

- **Memory:** 3B parameters ≈ 6–8GB VRAM (quantized Q4_K_M) → fits 16GB Mac Mini comfortably
- **Speed:** ~3–5 tokens/sec on M4 GPU acceleration → ~5–10s inference per agent ✓
- **Capability:** State-of-the-art 3B models (Qwen, Llama 3.2) rival 7B from 2023
- **Reasoning:** Llama 3.2 3B includes "extended thinking"; newer distilled reasoning models (QwQ-1B) available

### Why NOT Cloud APIs?

- **Privacy:** No calorie data leaves the device
- **Cost:** USDA API is free; cloud LLM queries = $$$/month
- **Latency:** Local inference is latency-bound but predictable
- **Sovereignty:** User owns their meal history

### Why These Specific Models?

| Model | Reason |
|-------|--------|
| **Llama 3.2 1B** | Fastest router; good instruction-following |
| **Qwen 2.5 3B** | Multilingual (EN + ZH), strong instruction-following, widespread usage |
| **LLaVA-NeXT 2B** | Best food recognition in <3B class; MIT License |
| **Llama 3.2 3B Instruct** | Extended thinking potential, agent-capable, proven |

**Alternative:** If QwQ-1.5B or Deepseek-R1-Distill-1.5B available → single reasoning model for all agents (faster, simpler orchestration).

---

## 12. QUESTIONS FOR CLARIFICATION (Andy's Check-In)

Before implementing, validate:

1. **Meal Types:** Do you want to track `meal_type` (breakfast/lunch/dinner/snack) or infer from timestamp?
   - *Recommended:* Ask user or infer from time window (12–2pm = lunch, etc.)

2. **Micronutrients:** Start with macros only (calories, protein, fat, carbs), or include micronutrients (Vitamin C, Iron, Sodium)?
   - *Recommended:* Start with macros; add micronutrients in v1.1 (adds USDA query complexity)

3. **Cooking Method:** Always ask, or only for proteins?
   - *Recommended:* Always ask for proteins & oils; optional for vegetables

4. **User Mood/Notes:** Store optional fields (how they felt, notes) or just core nutrition?
   - *Recommended:* Optional fields; allow capture but don't require

5. **Privacy/Data Retention:** How long to keep meal history? Purge after 1 year?
   - *Recommended:* Keep indefinitely locally; no cloud sync

6. **Multi-language:** Default to English + Chinese, or English only for v1.0?
   - *Recommended:* Use Qwen multilingual; support both EN + ZH from start

---

**END OF SPECIFICATION**

---

### How to Use This Document

1. **Share with Claude Code:** Copy sections 3, 4, 5 into Claude Code prompts
2. **Implementation Order:** Follow the roadmap (Phase 1 → Phase 4)
3. **Reference Schemas:** Use sections 6 & 10 as copy-paste templates
4. **Troubleshooting:** Section 7 lists known issues + solutions

**Next Step:** Start with Phase 1 (Router + Text Agent). Test locally, then integrate Image Agent.

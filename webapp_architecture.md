# Calorie Tracking WebApp - Architecture Specification

**Date:** April 7, 2026  
**Purpose:** Frontend architecture for local calorie tracking with chat interface, meal history, and analytics  
**Output Format:** Text-only specification (exportable to Claude Code)

---

## 1. WEBAPP OVERVIEW

### 1.1 Core Components

```
┌─────────────────────────────────────────────────────────┐
│                   CALORIE TRACKER WEBAPP                │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────────────┐  ┌──────────────────────────────┐ │
│  │  Chat Interface  │  │  Sidebar / Navigation        │ │
│  │  (text input)    │  │  - Today's Summary           │ │
│  │  (conversation   │  │  - Meal History             │ │
│  │   memory)        │  │  - Weekly Stats              │ │
│  │  (agent response)│  │  - Monthly Analytics         │ │
│  └──────────────────┘  └──────────────────────────────┘ │
│                                                         │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  Meal Details Panel (right side)                    │ │
│  │  - Current meal being logged (if active)            │ │
│  │  - Ingredient list with amounts                     │ │
│  │  - Nutrition breakdown                              │ │
│  │  - Confirm / Edit / Delete buttons                  │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                         │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  Analytics View (expandable tabs)                   │ │
│  │  - Daily summary (Today's totals)                   │ │
│  │  - Day history (all meals for selected date)        │ │
│  │  - Weekly chart (7-day trend)                       │ │
│  │  - Monthly chart (30-day trend)                     │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 2. CHAT INTERFACE (Persistent Conversation Memory)

### 2.1 Requirements

1. **User sends text** → "Had pasta with tomato sauce"
2. **Agent processes** → Asks clarifying questions if needed
3. **User provides more info** → "About 2 cups of pasta"
4. **Agent updates state** → Recalculates completeness
5. **Loop continues** → Until completeness ≥ 0.75 OR user confirms
6. **Meal finalized** → Logged to database
7. **Conversation remembered** → Same meal context retained for edits/confirmations

**Key: NO context loss between user messages**

### 2.2 Chat Data Structure

```json
{
  "session_id": "session_20260407_1",
  "meal_id": "meal_20260407_lunch_001",
  "meal_context": {
    "timestamp": "2026-04-07T12:30:00",
    "meal_type": "lunch",
    "status": "in_progress",
    "parsed_ingredients": [
      {
        "name": "pasta",
        "amount": 0.0,
        "unit": "unknown",
        "confidence": 0.0,
        "verified": false
      },
      {
        "name": "tomato sauce",
        "amount": 0.0,
        "unit": "unknown",
        "confidence": 0.0,
        "verified": false
      }
    ],
    "completeness_score": 0.30,
    "last_question": "How did you measure your pasta? (e.g. cups, grams, or pieces)"
  },
  "conversation_history": [
    {
      "turn": 1,
      "user_message": "Had pasta with tomato sauce",
      "agent_response": "I found pasta and tomato sauce. How much pasta did you eat? (e.g., cups, grams, or pieces)",
      "timestamp": "2026-04-07T12:30:15"
    },
    {
      "turn": 2,
      "user_message": "About 2 cups of cooked pasta",
      "agent_response": "Got it, 2 cups of pasta. How much tomato sauce?",
      "meal_context_updated": true,
      "timestamp": "2026-04-07T12:30:45"
    },
    {
      "turn": 3,
      "user_message": "Maybe 1.5 cups of sauce",
      "agent_response": "Perfect! Let me log this meal... ✓ Meal logged (310 kcal, 25g protein)",
      "meal_status": "finalized",
      "timestamp": "2026-04-07T12:31:10"
    }
  ]
}
```

### 2.3 Chat UI Features

**Display:**
```
┌─────────────────────────────────────────────────┐
│ CHAT - Logging Lunch                     [×]    │
├─────────────────────────────────────────────────┤
│                                                 │
│ Agent: I found pasta and tomato sauce.         │
│        How much pasta did you eat?             │
│        (e.g., cups, grams, or pieces)         │
│                                                 │
│ You: About 2 cups of cooked pasta              │
│                                                 │
│ Agent: Got it, 2 cups of pasta.                │
│        How much tomato sauce?                  │
│                                                 │
│ You: Maybe 1.5 cups of sauce                   │
│                                                 │
│ Agent: ✓ Perfect! Meal logged.                 │
│        310 kcal | 25g protein | 44.6g carbs    │
│                                                 │
├─────────────────────────────────────────────────┤
│ [Input] "Add olive oil, 1 tbsp"               │
│ [Send] [Cancel] [Edit Meal]                   │
└─────────────────────────────────────────────────┘
```

**Buttons:**
- `[Send]` — Submit next user message
- `[Cancel]` — Discard meal, close chat
- `[Edit Meal]` — Modify any ingredient in current meal (keeps context)
- `[Copy to Clipboard]` — Export meal data as JSON

### 2.4 Conversation Memory Implementation

**Store in TinyDB (local JSON):**

```python
# meals.db.json (TinyDB format)
{
  "session_id": "session_20260407_1",
  "meal_id": "meal_20260407_lunch_001",
  "meal_context": {...},
  "conversation_history": [...]
}

# When user continues editing:
db = TinyDB('meals.db.json')
Meal = Query()

# Retrieve session context
current_meal = db.search(Meal.meal_id == "meal_20260407_lunch_001")[0]
context = current_meal['meal_context']
history = current_meal['conversation_history']

# Append new user message
history.append({
    "turn": len(history) + 1,
    "user_message": "Add olive oil, 1 tbsp",
    "timestamp": "2026-04-07T12:32:00"
})

# Re-process ingredients with full context
updated_context = agent.process(
    user_message="Add olive oil, 1 tbsp",
    existing_context=context,
    conversation_history=history
)

# Update database
db.update(
    {'meal_context': updated_context, 'conversation_history': history},
    Meal.meal_id == "meal_20260407_lunch_001"
)
```

---

## 3. TODAY'S SUMMARY (Right Sidebar)

### 3.1 Display Format

```
┌──────────────────────────────────┐
│  TODAY'S SUMMARY                 │
├──────────────────────────────────┤
│  Date: April 7, 2026             │
│  Meals Logged: 3                 │
│                                  │
│  Total Calories:    2145 kcal    │
│  Total Protein:     95.3g        │
│  Total Fat:         62.1g        │
│  Total Carbs:       285.4g       │
│  Total Fiber:       12.6g        │
│                                  │
│  Meals:                          │
│  • Breakfast (7:15 AM)           │
│    ├─ Banana smoothie            │
│    └─ 301 kcal, 20g protein     │
│                                  │
│  • Lunch (12:30 PM)              │
│    ├─ Pasta & tomato sauce       │
│    └─ 644 kcal, 25g protein     │
│                                  │
│  • Dinner (6:45 PM)              │
│    ├─ Grilled chicken + broccoli │
│    └─ 316 kcal, 47.4g protein   │
│                                  │
│  [+ Add Meal] [View History]     │
│  [Delete Meal]                   │
└──────────────────────────────────┘
```

### 3.2 Features

1. **Summary Card:**
   - Date header
   - Meal count
   - Aggregated macros (calories, protein, fat, carbs, fiber)

2. **Meal List:**
   - Each meal shows time, name, key ingredients
   - Inline calories + macros
   - Expandable (click to see full details)

3. **Action Buttons:**
   - `[+ Add Meal]` — Open chat interface for new meal
   - `[View History]` — Switch to "Day History" tab
   - `[Delete Meal]` — Next to each meal (click to remove)

### 3.3 Data Structure

```json
{
  "summary_date": "2026-04-07",
  "meals_today": [
    {
      "id": "meal_20260407_breakfast_001",
      "timestamp": "2026-04-07T07:15:00",
      "meal_type": "breakfast",
      "name": "Banana smoothie",
      "ingredients": [
        {"name": "banana", "amount": 120, "unit": "g"},
        {"name": "greek yogurt", "amount": 227, "unit": "g"},
        {"name": "honey", "amount": 21, "unit": "g"}
      ],
      "nutrition": {
        "calories": 301,
        "protein_g": 20,
        "fat_g": 1,
        "carbs_g": 54,
        "fiber_g": 2.6
      }
    },
    {...},
    {...}
  ],
  "daily_totals": {
    "calories": 2145,
    "protein_g": 95.3,
    "fat_g": 62.1,
    "carbs_g": 285.4,
    "fiber_g": 12.6
  }
}
```

---

## 4. MEAL HISTORY (Day-by-Day View)

### 4.1 Display Format

```
┌────────────────────────────────────────┐
│  MEAL HISTORY - April 2026             │
├────────────────────────────────────────┤
│                                        │
│  Date Selector:                        │
│  [← Apr 5] [Apr 6] [Apr 7 ✓] [Apr 8]   │
│            (Today)                     │
│                                        │
│  April 7, 2026                         │
│  ────────────────────────────────────  │
│                                        │
│  ☐ 7:15 AM - Breakfast                │
│    Banana smoothie                     │
│    301 kcal | 20g protein             │
│    [View] [Edit] [Delete]             │
│                                        │
│  ☐ 12:30 PM - Lunch                   │
│    Pasta & tomato sauce                │
│    644 kcal | 25g protein             │
│    [View] [Edit] [Delete]             │
│                                        │
│  ☐ 6:45 PM - Dinner                   │
│    Grilled chicken + broccoli          │
│    316 kcal | 47.4g protein           │
│    [View] [Edit] [Delete]             │
│                                        │
│  Day Totals:                           │
│  2145 kcal | 92.4g protein | 285g carbs│
│                                        │
└────────────────────────────────────────┘
```

### 4.2 Features

1. **Date Navigation:**
   - Previous/Next day arrows
   - Quick jump to today
   - Date picker (calendar optional)

2. **Meal List:**
   - Time of meal
   - Meal type (breakfast/lunch/dinner/snack)
   - Name/description
   - Quick macros (calories, protein)
   - Expandable for full details

3. **Action Buttons (per meal):**
   - `[View]` — Show full meal details + ingredients
   - `[Edit]` — Open chat to modify/add ingredients (context preserved)
   - `[Delete]` — Remove meal with confirmation

4. **Daily Totals:**
   - Aggregated macros at bottom
   - Easy to compare across days

### 4.3 Data Source

```python
# Query TinyDB for meals on specific date
db = TinyDB('meals.db.json')
Meal = Query()

meals_on_date = db.search(
    Meal.meal_date == "2026-04-07"
)

# Return sorted by timestamp
meals_sorted = sorted(meals_on_date, key=lambda x: x['timestamp'])
```

---

## 5. WEEKLY SUMMARY (Chart View)

### 5.1 Display Format

```
┌─────────────────────────────────────────────────────────┐
│  WEEKLY SUMMARY - Week of April 1–7, 2026              │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  [Weekly] [Monthly]  [Export Data]                     │
│                                                         │
│  Daily Breakdown:                                       │
│  ┌─────────────────────────────────────────────────┐   │
│  │ 2500 ┤                                           │   │
│  │      │         ╭─╮   ╭─╮                       │   │
│  │ 2000 ┤         │ │   │ │     ╭─╮               │   │
│  │      │   ╭─╮   │ │   │ │   ╭─╯ ╰─╮             │   │
│  │ 1500 ┤───│ │───│ │───│ │───│ 2145│─────────   │   │
│  │      │   │ │   │ │   │ │   ╰─┬─╯             │   │
│  │ 1000 ┤   │ │   │ │   │ │     │               │   │
│  │      │   ╰─╯   ╰─╯   ╰─╯     │               │   │
│  │  500 ┤                         ╰─╯               │   │
│  │      │                                           │   │
│  │    0 ├─┴─────┴─────┴─────┴─────┴─────┴─────┴──│   │
│  │      Apr 1  Apr 2  Apr 3  Apr 4  Apr 5  Apr 6  │   │
│  │                                                 │   │
│  │  Calories (kcal)                                │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  Protein (g):                                          │
│  ┌─────────────────────────────────────────────────┐   │
│  │ 110g  [Apr 7 ✓] 95.3g                          │   │
│  │ 105g  [Apr 6 ✓] 102.1g                         │   │
│  │ 100g  [Apr 5 ✓] 108.4g                         │   │
│  │  95g  [Apr 4 ✓] 98.7g                          │   │
│  │  90g  [Apr 3 ✓] 92.1g                          │   │
│  │  85g  [Apr 2 ✓] 87.3g                          │   │
│  │  80g  [Apr 1 ✓] 95.2g                          │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  Weekly Averages:                                      │
│  • Avg Calories:  1876 kcal/day                        │
│  • Avg Protein:   97.2 g/day                           │
│  • Avg Fat:       58.3 g/day                           │
│  • Avg Carbs:     267.8 g/day                          │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 5.2 Data Structure

```json
{
  "period": "weekly",
  "week_start": "2026-04-01",
  "week_end": "2026-04-07",
  "daily_data": [
    {
      "date": "2026-04-01",
      "day_of_week": "Tuesday",
      "meals_count": 3,
      "calories": 1850,
      "protein_g": 95.2,
      "fat_g": 60.1,
      "carbs_g": 250.5,
      "fiber_g": 11.2
    },
    {...},
    {...}
  ],
  "weekly_totals": {
    "total_calories": 13132,
    "total_protein_g": 680.4,
    "total_fat_g": 408.1,
    "total_carbs_g": 1874.6
  },
  "weekly_averages": {
    "avg_calories": 1876,
    "avg_protein_g": 97.2,
    "avg_fat_g": 58.3,
    "avg_carbs_g": 267.8
  }
}
```

---

## 6. MONTHLY SUMMARY (Chart View)

### 6.1 Display Format

```
┌─────────────────────────────────────────────────────────┐
│  MONTHLY SUMMARY - April 2026                           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  [Weekly] [Monthly]  [Export Data]                     │
│                                                         │
│  Calories by Week:                                      │
│  ┌─────────────────────────────────────────────────┐   │
│  │ 14000 ┤                                          │   │
│  │       │     ╭─╮   ╭─╮       ╭─╮                 │   │
│  │ 13000 ┤     │ │   │ │   ╭─╮ │ │                 │   │
│  │       │ ╭─╮ │ │   │ │   │ │ │ │                 │   │
│  │ 12000 ┤─│ │─│ │───│ │───│ │─│ │──────────       │   │
│  │       │ ╰─╯ ╰─╯   ╰─╯   ╰─╯ ╰─╯                 │   │
│  │ 11000 ┤                                          │   │
│  │       │                                          │   │
│  │ 10000 ├──────┴──────┴──────┴──────┴───────────  │   │
│  │       Week 1  Week 2  Week 3  Week 4            │   │
│  │  Calories per Week (kcal)                       │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  Weekly Breakdown:                                      │
│  Week 1 (Apr 1–7):   13,132 kcal  (1,876/day avg)     │
│  Week 2 (Apr 8–14):  12,945 kcal  (1,849/day avg)     │
│  Week 3 (Apr 15–21): 13,512 kcal  (1,930/day avg)     │
│  Week 4 (Apr 22–30): 12,756 kcal  (1,824/day avg)     │
│                                                         │
│  Monthly Totals:                                        │
│  • Total Calories:    52,345 kcal                       │
│  • Avg Daily:         1,745 kcal                        │
│  • Total Protein:     3,842 g (avg 128g/day)           │
│  • Total Carbs:       7,455 g (avg 248g/day)           │
│                                                         │
│  Trends:                                               │
│  • Week over week: -1.41% (calories)                   │
│  • Protein consistency: ±5.2%                          │
│  • Most active day: Apr 15 (2,145 kcal)               │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 6.2 Data Structure

```json
{
  "period": "monthly",
  "month": "2026-04",
  "weekly_summaries": [
    {
      "week_number": 1,
      "week_start": "2026-04-01",
      "week_end": "2026-04-07",
      "total_calories": 13132,
      "avg_daily_calories": 1876,
      "total_protein_g": 680.4,
      "avg_daily_protein_g": 97.2,
      "total_carbs_g": 1874.6,
      "days_logged": 7
    },
    {...},
    {...},
    {...}
  ],
  "monthly_totals": {
    "total_calories": 52345,
    "total_protein_g": 3842,
    "total_fat_g": 1632.4,
    "total_carbs_g": 7455,
    "days_logged": 28
  },
  "monthly_averages": {
    "avg_daily_calories": 1745,
    "avg_daily_protein_g": 128,
    "avg_daily_fat_g": 54.4,
    "avg_daily_carbs_g": 248.5
  },
  "trends": {
    "week_over_week_change_percent": -1.41,
    "protein_consistency_percent": 5.2,
    "highest_day": {
      "date": "2026-04-15",
      "calories": 2145
    },
    "lowest_day": {
      "date": "2026-04-02",
      "calories": 1654
    }
  }
}
```

---

## 7. EXPORT DATA (Text-Only)

### 7.1 Export Formats

**Button:** `[Export Data]` (available on all chart views)

**Exports produce plain text files:**

#### Format 1: Daily Summary (Text)

```
═══════════════════════════════════════
CALORIE TRACKER - DAILY SUMMARY
═══════════════════════════════════════

Date: April 7, 2026

Meals Logged:
─────────────────────────────────────

1. Breakfast - 7:15 AM
   Banana smoothie
   Ingredients:
   • Banana: 120g (107 kcal)
   • Greek yogurt: 227g (130 kcal)
   • Honey: 21g (64 kcal)
   
   Nutrition:
   Calories: 301 kcal
   Protein: 20g
   Fat: 1g
   Carbs: 54g
   Fiber: 2.6g

2. Lunch - 12:30 PM
   Pasta & tomato sauce
   Ingredients:
   • Pasta: 280g (1039 kcal)
   • Tomato sauce: 150g (95 kcal)
   
   Nutrition:
   Calories: 1134 kcal
   Protein: 32.1g
   Fat: 5.2g
   Carbs: 196.4g
   Fiber: 4.1g

3. Dinner - 6:45 PM
   Grilled chicken + broccoli
   Ingredients:
   • Chicken breast: 145g (245 kcal)
   • Broccoli: 90g (24 kcal)
   • Olive oil: 5ml (45 kcal)
   
   Nutrition:
   Calories: 316 kcal
   Protein: 47.4g
   Fat: 10.4g
   Carbs: 8g
   Fiber: 1.2g

═══════════════════════════════════════
DAILY TOTALS
═══════════════════════════════════════
Calories:       2145 kcal
Protein:        95.3g
Fat:            62.1g
Carbs:          285.4g
Fiber:          12.6g

Meals logged: 3
═══════════════════════════════════════
```

#### Format 2: Weekly Summary (Text)

```
═══════════════════════════════════════
CALORIE TRACKER - WEEKLY SUMMARY
═══════════════════════════════════════

Week: April 1–7, 2026

Daily Breakdown:
─────────────────────────────────────
Apr 1 (Tue):  1,850 kcal | 95.2g protein | 250.5g carbs
Apr 2 (Wed):  1,923 kcal | 98.5g protein | 268.3g carbs
Apr 3 (Thu):  1,745 kcal | 92.1g protein | 241.6g carbs
Apr 4 (Fri):  1,988 kcal | 98.7g protein | 279.2g carbs
Apr 5 (Sat):  2,012 kcal | 108.4g protein | 285.4g carbs
Apr 6 (Sun):  1,876 kcal | 102.1g protein | 262.7g carbs
Apr 7 (Mon):  2,145 kcal | 95.3g protein | 285.4g carbs

═══════════════════════════════════════
WEEKLY TOTALS
═══════════════════════════════════════
Total Calories:    13,132 kcal
Total Protein:       680.4g
Total Fat:           408.1g
Total Carbs:       1,874.6g

Weekly Averages:
─────────────────────────────────────
Avg Daily Calories:  1,876 kcal
Avg Daily Protein:     97.2g
Avg Daily Fat:         58.3g
Avg Daily Carbs:      267.8g

Days Logged: 7
═══════════════════════════════════════
```

#### Format 3: Monthly Summary (Text)

```
═══════════════════════════════════════
CALORIE TRACKER - MONTHLY SUMMARY
═══════════════════════════════════════

Month: April 2026

Weekly Breakdown:
─────────────────────────────────────
Week 1 (Apr 1–7):
  Total: 13,132 kcal | Protein: 680.4g | Avg: 1,876 kcal/day

Week 2 (Apr 8–14):
  Total: 12,945 kcal | Protein: 650.2g | Avg: 1,849 kcal/day

Week 3 (Apr 15–21):
  Total: 13,512 kcal | Protein: 705.1g | Avg: 1,930 kcal/day

Week 4 (Apr 22–30):
  Total: 12,756 kcal | Protein: 661.3g | Avg: 1,824 kcal/day

═══════════════════════════════════════
MONTHLY TOTALS
═══════════════════════════════════════
Total Calories:    52,345 kcal
Total Protein:      3,842g
Total Fat:          1,632.4g
Total Carbs:        7,455g

Monthly Averages:
─────────────────────────────────────
Avg Daily Calories:  1,745 kcal
Avg Daily Protein:     128g
Avg Daily Fat:        54.4g
Avg Daily Carbs:      248.5g

Days Logged: 28

Trends:
─────────────────────────────────────
Week over week change: -1.41%
Protein consistency: ±5.2%
Highest day: Apr 15 (2,145 kcal)
Lowest day: Apr 2 (1,654 kcal)
═══════════════════════════════════════
```

### 7.2 Export Implementation

```python
# Export button click handler
def export_data(period="daily", date=None):
    """
    period: "daily", "weekly", or "monthly"
    date: specific date for daily export, or week/month for others
    
    Returns: Plain text string (no charts, no images)
    """
    
    if period == "daily":
        data = fetch_daily_summary(date)
        text = format_daily_text(data)
    
    elif period == "weekly":
        data = fetch_weekly_summary(date)
        text = format_weekly_text(data)
    
    elif period == "monthly":
        data = fetch_monthly_summary(date)
        text = format_monthly_text(data)
    
    # Return as plain text (user can copy or save)
    return text

# Usage: Export file
# File: calorie_export_20260407_daily.txt
# Content: plain text formatted above
```

---

## 8. WEBAPP LAYOUT (Visual Grid)

```
┌─────────────────────────────────────────────────────────────┐
│                      NAVBAR (fixed top)                     │
│   Logo | Home | History | Stats | Settings | [Profile]     │
└─────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ SIDEBAR (left)              │ MAIN CONTENT (center)          │
│                             │                                │
│ Navigation:                 │                                │
│ • Today (quick access)      │  [Chat Interface]              │
│ • History                   │                                │
│ • Weekly Stats              │  User: Had pasta with...       │
│ • Monthly Analytics         │  Agent: How much pasta?        │
│                             │  User: About 2 cups            │
│ Quick Summary:              │  Agent: ✓ Logged               │
│ • Meals today: 3            │                                │
│ • Calories: 2145 kcal       │  [Input box with send button] │
│ • Protein: 95.3g            │                                │
│ • [+ Add Meal]              │                                │
│                             │                                │
│ Recent Meals:               │  MEAL DETAILS (expandable)     │
│ • Breakfast (301 kcal)      │                                │
│ • Lunch (644 kcal)          │  Pasta & tomato sauce          │
│ • Dinner (316 kcal)         │  Ingredients:                  │
│                             │  • Pasta: 280g                 │
│                             │  • Tomato: 150g                │
│                             │                                │
│                             │  Nutrition:                    │
│                             │  1134 kcal | 32g protein       │
│                             │                                │
│                             │  [Edit] [Delete] [Confirm]    │
│                             │                                │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ BOTTOM TABS (Charts/Analytics)                               │
│                                                              │
│ [Today's Summary] [Day History] [Weekly] [Monthly]          │
│                                                              │
│ Content changes based on selected tab                        │
│ (Charts displayed as text-exportable data)                   │
└──────────────────────────────────────────────────────────────┘
```

---

## 9. STATE MANAGEMENT

### 9.1 Session State (In-Memory)

```python
class SessionState:
    def __init__(self):
        self.current_meal_id = None          # Active meal being logged
        self.conversation_history = []       # Current conversation
        self.meal_context = {}               # Parsed ingredients, questions
        self.selected_date = today()         # Date for history view
        self.selected_period = "daily"       # Chart period (daily/weekly/monthly)
        self.view_mode = "chat"              # chat, history, weekly, monthly

# Usage in UI:
session_state = SessionState()

# When user starts new meal:
session_state.current_meal_id = generate_meal_id()
session_state.conversation_history = []
session_state.view_mode = "chat"

# When user switches to history:
session_state.view_mode = "history"
session_state.selected_date = selected_date

# When user exports:
export_data(
    period=session_state.selected_period,
    date=session_state.selected_date
)
```

### 9.2 Persistent State (TinyDB)

```python
# meals.db.json (local database)
# Stores all meal data with conversation history

# meals_summary.db.json (optional cache)
# Stores pre-calculated daily/weekly/monthly summaries for speed
```

---

## 10. FEATURE CHECKLIST

### Chat Interface:
- ✅ Text input field (send button)
- ✅ Conversation display (scrollable)
- ✅ Persistent memory per meal (no context loss)
- ✅ Agent response streaming (optional)
- ✅ [Cancel], [Edit Meal], [Copy to Clipboard] buttons
- ✅ Show current meal status (in_progress, completed, etc.)

### Today's Summary:
- ✅ Date header
- ✅ Meal count
- ✅ Aggregated macros (calories, protein, fat, carbs, fiber)
- ✅ Meal list with times and macros
- ✅ Expandable meal details
- ✅ [+ Add Meal], [View History], [Delete Meal] buttons

### Day History:
- ✅ Date navigation (prev/next)
- ✅ Meal list for selected date
- ✅ [View], [Edit], [Delete] per meal
- ✅ Daily totals at bottom

### Weekly Summary:
- ✅ Line chart (calories by day)
- ✅ Daily breakdown (protein/fat/carbs)
- ✅ Weekly averages
- ✅ [Export Data] button

### Monthly Summary:
- ✅ Line chart (calories by week)
- ✅ Weekly breakdown
- ✅ Monthly totals and averages
- ✅ Trends (week-over-week, highest/lowest day)
- ✅ [Export Data] button

### Export:
- ✅ Text-only format (no images/charts)
- ✅ Daily export (all meals + macros)
- ✅ Weekly export (daily breakdown + averages)
- ✅ Monthly export (weekly breakdown + trends)
- ✅ Downloadable or copy-to-clipboard

---

## 11. TECHNOLOGY STACK (Frontend Only)

**Framework:** React.js or Vue.js (lightweight)  
**State Management:** Context API or Zustand (simple)  
**Styling:** Tailwind CSS or CSS modules  
**Data Fetching:** Fetch API (local Python backend)  
**Database Connection:** Fetch to local API endpoint (TinyDB via Python backend)  
**Charts:** Chart.js or D3.js (render as SVG, exportable as text)

---

## 12. API ENDPOINTS (Backend Communication)

**The webapp communicates with your local calorie agent via HTTP endpoints:**

```
POST /api/chat
Body: { "user_message": "...", "meal_id": "..." }
Response: { "agent_response": "...", "meal_context": {...} }

GET /api/meals/today
Response: { "meals": [...], "daily_totals": {...} }

GET /api/meals/{date}
Response: { "meals": [...], "daily_totals": {...} }

GET /api/summary/weekly?start_date=2026-04-01
Response: { "weekly_data": [...], "weekly_averages": {...} }

GET /api/summary/monthly?month=2026-04
Response: { "monthly_data": [...], "trends": {...} }

DELETE /api/meals/{meal_id}
Response: { "success": true }

POST /api/export
Body: { "period": "daily", "date": "2026-04-07" }
Response: { "text": "..." } (plain text)
```

---

## 13. EXPORT TO CLAUDE CODE

**When ready to build:**

1. Copy this specification to Claude Code
2. Specify frontend framework (React or Vue)
3. Claude builds component structure:
   - `ChatInterface.jsx` — Persistent conversation memory
   - `TodaySummary.jsx` — Today's macros + meal list
   - `MealHistory.jsx` — Day-by-day navigation
   - `WeeklySummary.jsx` — Weekly chart + export
   - `MonthlySummary.jsx` — Monthly chart + export
   - `ExportData.js` — Text formatting functions
4. Include mock API calls (data fetching from backend)
5. Integration: Connect each component to TinyDB queries

---

**END OF SPECIFICATION**

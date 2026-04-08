"""
chainlit_app.py — COTD Chainlit chat interface
Run: chainlit run chainlit_app.py
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import chainlit as cl

from agents import (
    Ingredient, Meal, NutritionData,
    text_agent, reasoning_agent, nutrition_lookup_agent,
    get_db, save_meal, get_daily_summary,
    _infer_meal_type, _apply_user_clarification,
    MAX_QUESTIONS,
)

# ── Session state ──────────────────────────────────────────────────────────────
@dataclass
class SessionState:
    ingredients: list            = field(default_factory=list)
    pending_q: Optional[str]     = None
    asked_questions: set         = field(default_factory=set)
    phase: str                   = "idle"   # idle | clarifying
    turn: int                    = 0
    last_meal_summary: Optional[str] = None

# ── Chainlit handlers ──────────────────────────────────────────────────────────
@cl.on_chat_start
async def on_start():
    cl.user_session.set("state", SessionState())
    await cl.Message(content=(
        "Hi! 👋 Tell me what you ate and I'll track your calories.\n\n"
        "**Examples:**\n"
        "- `I had pasta and tomato sauce`\n"
        "- `1 banana, 1 cup greek yogurt, 1 tbsp honey`\n\n"
        "**Commands:** `last meal` · `today summary` · `skip`"
    )).send()

@cl.on_message
async def on_message(message: cl.Message):
    state: SessionState = cl.user_session.get("state")
    text = message.content.strip()
    low  = text.lower()

    # ── Built-in commands ──────────────────────────────────────────────────────
    if any(w in low for w in ["last meal", "my last meal", "what did i eat"]):
        reply = (f"Your last meal: **{state.last_meal_summary}**"
                 if state.last_meal_summary else "No meal logged yet in this session.")
        await cl.Message(content=reply).send()
        return

    if any(w in low for w in ["today summary", "daily total", "how much today"]):
        conn = get_db()
        s = get_daily_summary(date.today(), conn)
        conn.close()
        await cl.Message(content=(
            f"**Today's total ({s['meal_count']} meal(s))**\n"
            f"🔥 {s['total_calories']:.0f} kcal  |  "
            f"💪 {s['total_protein_g']:.1f}g protein  |  "
            f"🥑 {s['total_fat_g']:.1f}g fat  |  "
            f"🌾 {s['total_carbs_g']:.1f}g carbs"
        )).send()
        return

    # ── Skip current question ──────────────────────────────────────────────────
    if low in ("skip", "no", "n/a") and state.phase == "clarifying":
        for ing in state.ingredients:
            if ing.amount <= 0:
                ing.amount = 100.0; ing.unit = "g"; ing.source = "fallback_average"
        state.pending_q = None
        await _finalize(state)
        return

    # ── Answer to clarification (same meal context retained) ──────────────────
    if state.phase == "clarifying" and state.pending_q:
        before = {i.name: i.amount for i in state.ingredients}
        state.ingredients = await cl.make_async(_apply_user_clarification)(
            state.ingredients, text, state.pending_q)
        after   = {i.name: i.amount for i in state.ingredients}
        changed = [n for n in before if before[n] != after[n]]
        if not changed:
            # Parse failed → set defaults and move on
            for ing in state.ingredients:
                if ing.amount <= 0:
                    ing.amount = 100.0; ing.unit = "g"; ing.source = "fallback_average"
        state.pending_q = None
        await _check_and_ask(state)
        return

    # ── New meal description ───────────────────────────────────────────────────
    state.ingredients     = []
    state.pending_q       = None
    state.asked_questions = set()
    state.phase           = "idle"
    state.turn            = 0

    async with cl.Step(name="Parsing meal..."):
        try:
            state.ingredients = await cl.make_async(text_agent)(text)
        except Exception as e:
            await cl.Message(content=f"Couldn't parse that: `{e}`. Try rephrasing.").send()
            return

    if not state.ingredients:
        await cl.Message(content="I couldn't find any foods. Try describing what you ate.").send()
        return

    parsed = ", ".join(
        f"**{i.name}** ({i.amount} {i.unit})" if i.amount > 0 else f"**{i.name}**"
        for i in state.ingredients
    )
    await cl.Message(content=f"Got it! I see: {parsed}").send()
    await _check_and_ask(state)

# ── Internal helpers ───────────────────────────────────────────────────────────
async def _check_and_ask(state: SessionState):
    if state.turn >= MAX_QUESTIONS:
        await _finalize(state)
        return

    check    = await cl.make_async(reasoning_agent)(state.ingredients)
    score    = check.get("completeness_score", 1.0)
    question = check.get("next_question")

    if score >= 0.75 or not question:
        await _finalize(state)
        return

    q_key = question.lower()[:60]
    if q_key in state.asked_questions:
        await _finalize(state)
        return

    state.asked_questions.add(q_key)
    state.phase     = "clarifying"
    state.pending_q = question
    state.turn     += 1

    await cl.Message(content=f"🤖 {question}\n\n*(Type `skip` to use default values)*").send()

async def _finalize(state: SessionState):
    state.phase = "idle"

    async with cl.Step(name="Looking up nutrition..."):
        conn = get_db()
        state.ingredients = await cl.make_async(nutrition_lookup_agent)(state.ingredients, conn)

    meal_type = _infer_meal_type()
    meal = Meal(
        meal_type=meal_type,
        ingredients=state.ingredients,
        total_calories     = sum(i.nutrition.calories   for i in state.ingredients if i.nutrition),
        total_protein_g    = sum(i.nutrition.protein_g  for i in state.ingredients if i.nutrition),
        total_fat_g        = sum(i.nutrition.fat_g      for i in state.ingredients if i.nutrition),
        total_carbs_g      = sum(i.nutrition.carbs_g    for i in state.ingredients if i.nutrition),
        total_fiber_g      = sum((i.nutrition.fiber_g or 0) for i in state.ingredients if i.nutrition),
        api_fallback_count = sum(1 for i in state.ingredients if i.source == "fallback_average"),
    )
    save_meal(meal, conn)
    conn.close()

    state.last_meal_summary = (
        f"{meal_type}: {meal.total_calories:.0f} kcal | "
        f"{meal.total_protein_g:.1f}g protein | "
        f"{meal.total_fat_g:.1f}g fat | "
        f"{meal.total_carbs_g:.1f}g carbs"
    )

    names = ", ".join(i.name for i in state.ingredients)
    await cl.Message(content=(
        f"✅ **{meal_type.capitalize()} logged!**\n\n"
        f"**Foods:** {names}\n\n"
        f"| | |\n|---|---|\n"
        f"| 🔥 Calories | {meal.total_calories:.0f} kcal |\n"
        f"| 💪 Protein  | {meal.total_protein_g:.1f} g |\n"
        f"| 🥑 Fat      | {meal.total_fat_g:.1f} g |\n"
        f"| 🌾 Carbs    | {meal.total_carbs_g:.1f} g |\n"
        f"| 🥦 Fiber    | {meal.total_fiber_g:.1f} g |"
    )).send()

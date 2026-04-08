"""
api_server.py — REST API for the COTD dashboard
Run alongside chainlit:  python api_server.py
Serves on http://localhost:8001
"""

from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
import uvicorn

from agents import get_db

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# Serve dashboard.html at /
DASHBOARD = Path(__file__).parent / "dashboard.html"

@app.get("/", response_class=HTMLResponse)
def root():
    return DASHBOARD.read_text()

# ── Today's meals (with ingredients) ──────────────────────────
@app.get("/api/meals/today")
def meals_today():
    conn = get_db()
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT id, meal_timestamp, meal_type, total_calories, total_protein_g, "
        "total_fat_g, total_carbs_g, total_fiber_g FROM meals WHERE meal_date=? ORDER BY meal_timestamp",
        (today,)
    ).fetchall()
    result = []
    for r in rows:
        meal_id = r[0]
        ings = conn.execute(
            "SELECT ingredient_name, amount_value, unit, calories FROM meal_ingredients WHERE meal_id=?",
            (meal_id,)
        ).fetchall()
        result.append({
            "id": meal_id,
            "meal_timestamp": r[1],
            "meal_type": r[2],
            "total_calories": r[3],
            "total_protein_g": r[4],
            "total_fat_g": r[5],
            "total_carbs_g": r[6],
            "total_fiber_g": r[7],
            "ingredients": [{"ingredient_name": i[0], "amount_value": i[1],
                             "unit": i[2], "calories": i[3]} for i in ings],
        })
    conn.close()
    return result

# ── Today's macro summary ──────────────────────────────────────
@app.get("/api/summary/today")
def summary_today():
    conn = get_db()
    row = conn.execute(
        "SELECT SUM(total_calories), SUM(total_protein_g), SUM(total_fat_g), "
        "SUM(total_carbs_g), SUM(total_fiber_g), COUNT(*) FROM meals WHERE meal_date=?",
        (date.today().isoformat(),)
    ).fetchone()
    conn.close()
    return {"total_calories": row[0] or 0, "total_protein_g": row[1] or 0,
            "total_fat_g": row[2] or 0, "total_carbs_g": row[3] or 0,
            "total_fiber_g": row[4] or 0, "meal_count": row[5] or 0}

# ── Weekly: last 7 days ────────────────────────────────────────
@app.get("/api/summary/weekly")
def summary_weekly():
    conn = get_db()
    today = date.today()
    days = []
    totals = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0, "days": 0}
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        row = conn.execute(
            "SELECT SUM(total_calories), SUM(total_protein_g), SUM(total_fat_g), "
            "SUM(total_carbs_g), COUNT(*) FROM meals WHERE meal_date=?", (d,)
        ).fetchone()
        kcal = row[0] or 0
        days.append({"date": d, "total_calories": kcal,
                     "total_protein_g": row[1] or 0, "total_fat_g": row[2] or 0,
                     "total_carbs_g": row[3] or 0, "meal_count": row[4] or 0})
        if kcal > 0:
            totals["calories"] += kcal; totals["protein"] += row[1] or 0
            totals["fat"] += row[2] or 0; totals["carbs"] += row[3] or 0
            totals["days"] += 1
    conn.close()
    n = totals["days"] or 1
    return {
        "days": days,
        "averages": {
            "avg_calories":   round(totals["calories"] / n),
            "avg_protein_g":  round(totals["protein"] / n, 1),
            "avg_fat_g":      round(totals["fat"] / n, 1),
            "avg_carbs_g":    round(totals["carbs"] / n, 1),
            "days_logged":    totals["days"],
        }
    }

# ── Monthly: current month day-by-day + weekly buckets ────────
@app.get("/api/summary/monthly")
def summary_monthly():
    conn = get_db()
    today = date.today()
    month_start = today.replace(day=1)
    days_raw = []
    d = month_start
    while d <= today:
        row = conn.execute(
            "SELECT SUM(total_calories), SUM(total_protein_g), SUM(total_fat_g), "
            "SUM(total_carbs_g), COUNT(*) FROM meals WHERE meal_date=?",
            (d.isoformat(),)
        ).fetchone()
        days_raw.append({"date": d.isoformat(), "total_calories": row[0] or 0,
                         "total_protein_g": row[1] or 0, "total_fat_g": row[2] or 0,
                         "total_carbs_g": row[3] or 0, "meal_count": row[4] or 0})
        d += timedelta(days=1)

    # Group into weeks
    weeks, w = [], {}
    for day in days_raw:
        dt = date.fromisoformat(day["date"])
        wk = (dt.day - 1) // 7 + 1
        if wk not in w:
            w[wk] = {"label": f"Week {wk}", "days_logged": 0, "total_calories": 0,
                     "total_protein_g": 0, "total_carbs_g": 0, "avg_daily_calories": 0}
        b = w[wk]
        if day["total_calories"] > 0:
            b["days_logged"] += 1
            b["total_calories"]  += day["total_calories"]
            b["total_protein_g"] += day["total_protein_g"]
            b["total_carbs_g"]   += day["total_carbs_g"]
    for wk in sorted(w):
        b = w[wk]
        b["avg_daily_calories"] = round(b["total_calories"] / b["days_logged"]) if b["days_logged"] else 0
        weeks.append(b)

    # Totals + highlights
    logged = [d for d in days_raw if d["total_calories"] > 0]
    total_kcal = sum(d["total_calories"] for d in logged)
    conn.close()
    hi = max(logged, key=lambda d: d["total_calories"], default=None)
    lo = min(logged, key=lambda d: d["total_calories"], default=None)
    return {
        "days": days_raw,
        "weeks": weeks,
        "totals": {
            "total_calories": round(total_kcal),
            "total_protein_g": round(sum(d["total_protein_g"] for d in logged), 1),
            "total_fat_g": round(sum(d["total_fat_g"] for d in logged), 1),
            "total_carbs_g": round(sum(d["total_carbs_g"] for d in logged), 1),
            "days_logged": len(logged),
            "avg_daily_calories": round(total_kcal / len(logged)) if logged else 0,
        },
        "highest_day": {"date": hi["date"], "calories": hi["total_calories"]} if hi else None,
        "lowest_day":  {"date": lo["date"], "calories": lo["total_calories"]} if lo else None,
    }

# ── Delete meal ────────────────────────────────────────────────
@app.delete("/api/meals/{meal_id}")
def delete_meal(meal_id: int):
    conn = get_db()
    conn.execute("DELETE FROM meal_ingredients WHERE meal_id=?", (meal_id,))
    conn.execute("DELETE FROM meals WHERE id=?", (meal_id,))
    conn.commit()
    conn.close()
    return {"success": True}

# ── Edit meal ──────────────────────────────────────────────────
class MealPatch(BaseModel):
    meal_type: Optional[str] = None
    notes: Optional[str] = None

@app.patch("/api/meals/{meal_id}")
def edit_meal(meal_id: int, patch: MealPatch):
    conn = get_db()
    if patch.meal_type:
        conn.execute("UPDATE meals SET meal_type=? WHERE id=?", (patch.meal_type, meal_id))
    if patch.notes is not None:
        conn.execute("UPDATE meals SET notes=? WHERE id=?", (patch.notes, meal_id))
    conn.commit()
    conn.close()
    return {"success": True}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)

# 🤖 Mate Self-Evolution Quickstart

Mate now has autonomous self-evolution capabilities. Her personality (soul.md) updates based on user feedback.

## Quick Start (3 steps)

### 1. Enable Mate Evolution (Optional: Flash Thinking)
```bash
# In .env, enable Flash thinking for lightweight reasoning:
export MATE_FLASH_THINKING=true
```

### 2. Start Web UI
```bash
make web  # Starts FastAPI :8000 + Next.js UI :3000
```

### 3. Run Demo
```bash
source .venv/bin/activate
python scripts/mate_evolution_demo.py
```

This will:
1. Chat with Mate ("How to learn ML?")
2. Send negative feedback (rating=2)
3. Trigger soul.md evolution
4. Show updated personality

Check `prompt/mate/soul.md` — it's been updated!

---

## API Usage

### Chat with Mate (existing)
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is quantum computing?",
    "character": "mate",
    "model": "flash"
  }'
```

### User Rates Response (NEW)
```bash
curl -X POST http://localhost:8000/mate/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "chat_id": "abc123",
    "rating": 2,
    "feedback": "Too technical for beginners",
    "suggestion": "Use more everyday analogies"
  }'
```

**Fields:**
- `chat_id`: From chat response
- `rating`: 0-5 stars
- `feedback`: What didn't work
- `suggestion`: How to improve

**Response:**
```json
{
  "status": "recorded",
  "chat_id": "abc123",
  "rating": 2,
  "soul_suggestion": "...",
  "feedback_count": 1
}
```

### Trigger Evolution (NEW)
```bash
curl -X POST http://localhost:8000/mate/evolve
```

**Requirements:**
- Need ≥2 valuable feedback entries
- "Valuable" = rating < 3 OR explicit suggestion provided

**Response:**
```json
{
  "status": "evolved",
  "soul_file": "/path/to/soul.md",
  "changes": "Added focus on beginner-friendly explanations",
  "rationale": "Multiple users requested simpler language",
  "new_soul_preview": "..."
}
```

Or if not enough feedback:
```json
{
  "status": "skipped",
  "reason": "Not enough feedback (need >= 2)"
}
```

---

## How It Works

```
User Chat          User Rates            Evolve Triggered
     ↓                  ↓                      ↓
 [/chat]           [/mate/feedback]       [/mate/evolve]
     ↓                  ↓                      ↓
 Mate responds    Feedback stored ─→ ≥2 valuable? ─→ LLM processes
                                      │                    ↓
                                      └─ YES ────→ soul.md updated
                                           YES → Feedback cleared
```

### Feedback Filtering
- Only feedback with `rating < 3` or `suggestion` is "valuable"
- Top 5 valuable feedbacks are sent to LLM
- LLM generates coherent soul.md improvement

### Soul Update Process
1. Read current soul.md
2. Aggregate top 5 valuable feedbacks
3. Ask LLM: "How should Mate's personality evolve?"
4. Write new soul.md
5. Clear feedback log

### Personality Changes
Example evolution:

**Before:**
```
- Playful, witty, and warm
- Knows when to stop joking and switch to serious troubleshooting
```

**After (feedback: "too technical"):**
```
- Playful, witty, and warm
- Prioritize beginner-friendly explanations with analogies
- Knows when to stop joking and switch to serious troubleshooting
```

---

## Configuration

### Environment Variables

```bash
# Enable Flash thinking (512 token budget)
# Default: false
MATE_FLASH_THINKING=true

# Standard Mate model
MODEL=gemini-3-flash-preview  # default

# Pro model for deeper reasoning
PRO_MODEL=gemini-2.5-pro

# GCP location
GCP_LOCATION=us-central1
```

---

## Monitoring

### Check Feedback Status
```python
from src.web.server import _mate_feedback

print(f"Total feedback: {len(_mate_feedback)}")
for fb in _mate_feedback:
    print(f"  {fb['timestamp']} | Rating: {fb['rating']} | {fb['feedback']}")
```

### Check Soul Evolution Logs
```bash
tail -f prompt/mate/soul.md  # See updates in real-time
```

---

## Thinking Tokens Explained

- **Flash Model** (default, fast):
  - Without thinking: Direct answers
  - With thinking (512 budget): Light reasoning enabled
  - Useful for: Quick responses, simple questions

- **Pro Model**:
  - Default thinking: 2048 budget
  - Deep reasoning for complex planning
  - Useful for: Architecture decisions, deep analysis

**When to Use:**
- Use Flash for most chats (faster, cheaper)
- Enable MATE_FLASH_THINKING if Mate needs to reason about personality improvements
- Use Pro for deep technical discussions

---

## Testing

### Manual Test
```bash
# Terminal 1: Start web server
make web

# Terminal 2: Run demo
python scripts/mate_evolution_demo.py

# Verify soul.md changed
cat prompt/mate/soul.md
```

### Verify Evolution
```bash
# Before
curl http://localhost:8000/chat -d '{"message": "...", "character": "mate"}'

# Give feedback
curl http://localhost:8000/mate/feedback -d '{"chat_id": "...", "rating": 2, ...}'

# Trigger evolution
curl http://localhost:8000/mate/evolve

# After (chat should reflect new soul.md)
curl http://localhost:8000/chat -d '{"message": "...", "character": "mate"}'
```

---

## Troubleshooting

### "Not enough feedback"
- Need ≥2 feedback entries with `rating < 3` or explicit `suggestion`
- Test with demo script that sends 2 feedbacks

### "Mate's personality didn't change"
- Check that soul.md was actually updated: `cat prompt/mate/soul.md`
- New chats should pick up updated soul
- Clear browser cache if using web UI

### Evolution endpoint timeout
- LLM call taking too long
- Check GCP credentials and model availability
- Increase timeout: `requests.post(..., timeout=120)`

---

## Architecture Notes

For deep dive, see:
- `prompt/mate/EVOLUTION.md` — Full system documentation
- `src/web/server.py` — `/mate/feedback` and `/mate/evolve` endpoints
- `scripts/mate_evolution_demo.py` — Complete example

---

**Next Steps:**
1. ✅ Run the demo
2. ✅ Chat with evolved Mate
3. ✅ Collect more feedback → trigger more evolution
4. Consider: Persist feedback to DB (currently in-memory)

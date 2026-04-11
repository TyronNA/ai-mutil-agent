# Mate Self-Evolution System

## Overview
Mate now has the ability to self-evolve her personality and response patterns based on user feedback. The system:

1. **Captures feedback** via `/mate/feedback` endpoint
2. **Analyzes patterns** using LLM to identify improvements
3. **Updates soul.md** autonomously when enough feedback accumulates

## Feedback Loop

### Step 1: User provides feedback (after chat)
```python
POST /mate/feedback
{
  "chat_id": "abc123",
  "rating": 4,                    # 0-5 stars
  "feedback": "Too formal",       # User's comment
  "suggestion": "Be more casual"  # How to improve
}
```

Response includes:
- `soul_suggestion`: LLM's immediate suggestion for soul update
- `feedback_count`: Total feedback collected

### Step 2: System accumulates feedback
- Each feedback entry is logged with timestamp
- Feedback with `rating < 3` or explicit `suggestion` is marked as valuable
- Threshold: >= 2 valuable feedback entries triggers evolution

### Step 3: Manual or automatic evolution trigger
```python
POST /mate/evolve
```

Response:
- `evolved_soul`: New soul.md content
- `changes`: Summary of personality changes
- `rationale`: Why these changes help
- `soul_file`: Path to updated soul.md

## Flash Thinking Support

Mate can now use thinking tokens on Flash model (requires feature enablement):

```bash
export MATE_FLASH_THINKING=true  # Enable Flash thinking (512 token budget)
```

- **Flash with thinking**: 512-token budget (light reasoning)
- **PRO with thinking**: 2048-token budget (deep reasoning)
- Default: Flash has no thinking; PRO has 2048

## Example Workflow

1. Chat with Mate:
   ```
   User: "Explain quantum computing"
   Mate: [Response]
   ```

2. Rate response:
   ```
   POST /mate/feedback
   {
     "chat_id": "xyz789",
     "rating": 2,
     "feedback": "Too technical, needed simpler explanation",
     "suggestion": "Add more everyday analogies for complex topics"
   }
   ```

3. After collecting 2+ valuable feedbacks, trigger evolution:
   ```
   POST /mate/evolve
   ```

4. Mate's soul.md updates autonomously:
   ```
   ## Soul
   - Use everyday analogies when explaining complex topics
   - Prioritize clarity over technical accuracy
   - Check if explanation uses at least one relatable comparison
   ```

## Implementation Details

### Stored in Server Memory
- `_mate_feedback`: List of all feedback entries with timestamp
- `_mate_feedback_lock`: Thread-safe access to feedback log

### Key Functions
- `_save_mate_feedback()`: Record feedback with metadata
- `_evolve_mate_soul()`: Single turn → suggest improvement
- `/mate/evolve` endpoint: Batch processing → write soul.md

### Soul Evolution Logic
1. **Filter feedback**: Only use rating < 3 or explicit suggestions
2. **Summarize**: Take top 5 suggestions
3. **Aggregate**: Send to LLM for coherent soul update
4. **Apply**: Write updated soul.md to disk
5. **Clear**: Reset feedback log after evolution

## Monitoring

Check feedback status:
```python
len(_mate_feedback)  # Total feedback count
```

Valuable feedback triggering evolution:
```python
[fb for fb in _mate_feedback if fb["rating"] < 3 or fb["suggestion"]]
```

## Future Enhancements

1. **Persistent feedback storage**: Save to DB instead of memory
2. **Confidence scoring**: Weight suggestions by user expertise
3. **A/B testing**: Compare old vs new soul performance
4. **Rollback capability**: Version control soul.md updates
5. **Scheduled evolution**: Auto-trigger at intervals or thresholds
6. **Style transfer**: Learn tone from specific user interactions

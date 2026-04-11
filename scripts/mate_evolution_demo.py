#!/usr/bin/env python3
"""
Mate Evolution Demo — Simulate feedback and trigger soul evolution.

This script demonstrates the self-evolution system for Mate.
Run after: make install && make web  (starts FastAPI server)

Usage:
    source .venv/bin/activate
    python scripts/mate_evolution_demo.py
"""

from __future__ import annotations

import json
import requests
import sys
from pathlib import Path


def main():
    BASE_URL = "http://localhost:8000"
    
    print("🤖 Mate Evolution Demo")
    print("=" * 50)
    
    # Step 1: Chat with Mate
    print("\n1️⃣ Chat with Mate...")
    chat_payload = {
        "message": "How can I learn machine learning?",
        "character": "mate",
        "model": "flash",
    }
    
    try:
        resp = requests.post(f"{BASE_URL}/chat", json=chat_payload, timeout=30)
        chat_result = resp.json()
        chat_id = chat_result.get("chat_id", "demo-123")
        response = chat_result.get("response", "")
        
        print(f"   Chat ID: {chat_id}")
        print(f"   Mate: {response[:200]}...")
        
    except Exception as e:
        print(f"   ✗ Chat failed: {e}")
        print("   Make sure: make web  is running on :8000")
        sys.exit(1)
    
    # Step 2: Send negative feedback (rating < 3 triggers evolution)
    print("\n2️⃣ Sending user feedback (rating=2, too technical)...")
    feedback_payload = {
        "chat_id": chat_id,
        "rating": 2.0,
        "feedback": "Way too technical. I'm a beginner.",
        "suggestion": "For beginners, focus on practical projects instead of theory. Use simpler language.",
    }
    
    try:
        resp = requests.post(f"{BASE_URL}/mate/feedback", json=feedback_payload, timeout=15)
        result = resp.json()
        print(f"   ✓ Feedback recorded")
        print(f"   Soul suggestion: {result.get('soul_suggestion', '')[:100]}...")
        print(f"   Feedback count: {result.get('feedback_count', 0)}")
        
    except Exception as e:
        print(f"   ✗ Feedback submission failed: {e}")
        sys.exit(1)
    
    # Step 3: Send more feedback to trigger evolution
    print("\n3️⃣ Sending additional feedback to trigger evolution (need >= 2)...")
    feedback_payload_2 = {
        "chat_id": "demo-456",
        "rating": 2.5,
        "feedback": "Also, please use more analogies.",
        "suggestion": "Analogies help beginners understand abstract concepts.",
    }
    
    try:
        resp = requests.post(f"{BASE_URL}/mate/feedback", json=feedback_payload_2, timeout=15)
        result = resp.json()
        print(f"   ✓ Second feedback recorded")
        print(f"   Feedback count: {result.get('feedback_count', 0)}")
        
    except Exception as e:
        print(f"   ✗ Second feedback failed: {e}")
    
    # Step 4: Trigger evolution
    print("\n4️⃣ Triggering Mate evolution (POST /mate/evolve)...")
    try:
        resp = requests.post(f"{BASE_URL}/mate/evolve", timeout=60)
        result = resp.json()
        
        if result.get("status") == "evolved":
            print(f"   ✓ Mate evolved!")
            print(f"   Changes: {result.get('changes', '')}")
            print(f"   Rationale: {result.get('rationale', '')}")
            print(f"\n   New soul.md preview:")
            print(f"   {result.get('new_soul_preview', '')}")
            print(f"\n   ✅ Check {result.get('soul_file')} for full update")
            
        else:
            print(f"   ⏭️  {result.get('status', 'unknown')}: {result.get('reason', '')}")
    
    except Exception as e:
        print(f"   ✗ Evolution failed: {e}")
        sys.exit(1)
    
    print("\n" + "=" * 50)
    print("✨ Demo complete! Mate's soul.md has been updated autonomously.")
    print("\nNext steps:")
    print("• Review the changes in prompt/mate/soul.md")
    print("• Chat with Mate again to see the new personality")
    print("• More feedback → more evolution!")


if __name__ == "__main__":
    main()

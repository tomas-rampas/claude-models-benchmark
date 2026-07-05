#!/usr/bin/env python3
"""
Simple Anthropic API Test Script
================================

A minimal, standalone script to quickly check:
1. If your ANTHROPIC_API_KEY is set and valid
2. Which models your key can actually access
3. A tiny test call to confirm everything works

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python3 simple_anthropic_test.py
"""

import os
import sys
from anthropic import Anthropic


def test_anthropic_api():
    print("=" * 60)
    print("🔍 Simple Anthropic API Connectivity Test")
    print("=" * 60)

    # 1. Check for API key
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ERROR: ANTHROPIC_API_KEY environment variable is not set.")
        print("   Please run: export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)

    print(f"✅ API key found (starts with: {api_key[:15]}...)")

    try:
        client = Anthropic(api_key=api_key)
    except Exception as e:
        print(f"❌ Failed to create Anthropic client: {e}")
        sys.exit(1)

    # 2. List all models available to this key
    print("\n📋 Fetching list of models available to your API key...")
    try:
        models_response = client.models.list()
        available_models = [m.id for m in models_response.data]
        print(f"✅ Successfully connected to Anthropic API.")
        print(f"   Found {len(available_models)} models available to your key:\n")

        for model_id in sorted(available_models):
            print(f"   • {model_id}")

    except Exception as e:
        print(f"❌ Failed to list models: {e}")
        print("   This usually means the key is invalid or has network issues.")
        sys.exit(1)

    # 3. Quick test call using a reliable fast model
    print("\n🧪 Running a tiny test call (using claude-haiku-4-5)...")
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=30,
            temperature=0,
            messages=[
                {"role": "user", "content": "Reply with exactly one word: 'working'"}
            ]
        )
        reply = response.content[0].text.strip()
        print(f"✅ Test call SUCCESSFUL!")
        print(f"   Model replied: '{reply}'")
        print(f"   Input tokens: {response.usage.input_tokens}, Output tokens: {response.usage.output_tokens}")

    except Exception as e:
        print(f"❌ Test call FAILED: {e}")
        print("   Even if model listing worked, this model might not be enabled for your key.")

    # 4. Summary + next steps
    print("\n" + "=" * 60)
    print("📊 SUMMARY")
    print("=" * 60)

    target_models = ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-5"]
    print("\nYour target models status:")

    for mid in target_models:
        status = "✅ AVAILABLE" if mid in available_models else "❌ NOT AVAILABLE (or not enabled for this key)"
        print(f"   {mid:25} → {status}")

    print("\n💡 Recommendations:")
    if all(m in available_models for m in target_models):
        print("   → All three target models are available! You can now run the full benchmark.")
    else:
        print("   → Some of your target models are not available yet.")
        print("   → Use the models that show ✅ above in your benchmark script.")
        print("   → Contact Anthropic support or upgrade your account tier to unlock the newest models.")

    print("\n✅ Test complete. You can now safely run your benchmark with working models.")


if __name__ == "__main__":
    test_anthropic_api()


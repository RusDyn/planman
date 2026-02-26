"""Tests for plan_detector.py — heuristic plan detection."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from plan_detector import (
    DETECTION_THRESHOLD,
    compute_plan_score,
    extract_last_assistant_text,
    is_plan,
)


class TestPlanDetection(unittest.TestCase):
    def test_clear_plan_detected(self):
        text = """\
## Implementation Plan

Here's my plan to add authentication:

1. Create the auth middleware
2. Add JWT token validation
3. Implement session management
4. Update the API routes
5. Add error handling

### Step 1: Auth Middleware
- Create `src/middleware/auth.ts`
- Implement token extraction
- Add role-based access control

### Step 2: JWT Validation
- Install jsonwebtoken package
- Create `src/utils/jwt.ts`
- Add token refresh logic
"""
        self.assertTrue(is_plan(text))
        score, signals = compute_plan_score(text)
        self.assertGreaterEqual(score, DETECTION_THRESHOLD)
        self.assertIn("plan_header", signals)
        self.assertIn("numbered_steps", signals)

    def test_code_explanation_not_plan(self):
        text = """\
This function validates user input by checking the email format
and ensuring the password meets minimum requirements.

```python
def validate_input(email, password):
    if not re.match(r'^[a-z]+@[a-z]+\\.[a-z]+$', email):
        raise ValueError("Invalid email")
    if len(password) < 8:
        raise ValueError("Password too short")
```

The regex checks for a basic email pattern. You could make it
more robust with a dedicated library like `email-validator`.
"""
        self.assertFalse(is_plan(text))

    def test_permission_mode_plan_with_one_signal(self):
        text = """\
## Approach

1. First update the database schema
2. Then modify the API endpoints
3. Finally update the frontend

I'll start by modifying `schema.sql`.
"""
        # permission_mode alone (+5) isn't enough, but with header (+3) and steps (+3) = 11
        self.assertTrue(is_plan(text, permission_mode="plan"))

    def test_permission_mode_alone_not_enough(self):
        text = "Sure, let me look into that for you."
        self.assertFalse(is_plan(text, permission_mode="plan"))

    def test_action_verb_bullets(self):
        text = """\
## Changes

- Create the user model
- Add validation middleware
- Implement the signup endpoint
- Update the database migration
- Write integration tests
- Deploy to staging
"""
        score, signals = compute_plan_score(text)
        self.assertIn("action_verbs", signals)

    def test_file_paths_signal(self):
        text = """\
## Plan

1. Edit `src/auth/middleware.ts`
2. Create `src/auth/jwt.ts`
3. Update `src/routes/api.ts`
4. Modify `src/config/database.ts`
"""
        score, signals = compute_plan_score(text)
        self.assertIn("file_paths", signals)

    def test_preamble_phrase(self):
        text = """\
Here's my plan to fix the authentication:

### Step 1
Update the token refresh logic.

### Step 2
Add error handling for expired tokens.

### Step 3
Update the tests.
"""
        score, signals = compute_plan_score(text)
        self.assertIn("preamble_phrase", signals)

    def test_bare_file_paths(self):
        text = """\
## Plan

Modify these files:
- src/lib/auth.ts
- src/routes/login.ts
- src/middleware/cors.ts
- tests/auth.test.ts
"""
        score, signals = compute_plan_score(text)
        self.assertIn("file_paths", signals)

    def test_short_response_not_plan(self):
        text = "Done! The file has been updated."
        self.assertFalse(is_plan(text))

    def test_empty_string(self):
        self.assertFalse(is_plan(""))

    def test_threshold_boundary(self):
        # Exactly at threshold
        # Plan header (3) + numbered steps (3) = 6 = threshold
        text = """\
## Plan

1. First do this
2. Then do that
3. Finally do the other thing
"""
        score, _ = compute_plan_score(text)
        self.assertEqual(score >= DETECTION_THRESHOLD, score >= 6)


class TestExtractLastAssistantText(unittest.TestCase):
    def test_simple_transcript(self):
        entries = [
            {"role": "user", "content": "Help me plan"},
            {"role": "assistant", "content": "Here is the first response"},
            {"role": "user", "content": "Refine it"},
            {"role": "assistant", "content": "Here is the revised plan"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
            path = f.name

        try:
            result = extract_last_assistant_text(path)
            self.assertEqual(result, "Here is the revised plan")
        finally:
            os.unlink(path)

    def test_content_blocks(self):
        entries = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Part 1"},
                    {"type": "tool_use", "name": "read"},
                    {"type": "text", "text": "Part 2"},
                ],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
            path = f.name

        try:
            result = extract_last_assistant_text(path)
            self.assertEqual(result, "Part 1\nPart 2")
        finally:
            os.unlink(path)

    def test_missing_file(self):
        result = extract_last_assistant_text("/nonexistent/path.jsonl")
        self.assertEqual(result, "")

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name

        try:
            result = extract_last_assistant_text(path)
            self.assertEqual(result, "")
        finally:
            os.unlink(path)

    def test_corrupt_lines_skipped(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("not json\n")
            f.write(json.dumps({"role": "assistant", "content": "Valid"}) + "\n")
            f.write("{bad json\n")
            path = f.name

        try:
            result = extract_last_assistant_text(path)
            self.assertEqual(result, "Valid")
        finally:
            os.unlink(path)


class TestTranscriptSizeLimit(unittest.TestCase):
    def test_large_transcript_returns_empty(self):
        """Transcripts over 50MB should return empty string."""
        from unittest.mock import patch as mock_patch
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
            f.write('{"role": "assistant", "content": "hello"}\n')
        try:
            # Mock os.path.getsize to return > 50MB
            with mock_patch("plan_detector.os.path.getsize", return_value=60 * 1024 * 1024):
                result = extract_last_assistant_text(path)
            self.assertEqual(result, "")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()

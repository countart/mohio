"""
mohio_ai.py
Mohio Language — AI Runtime
Phase 1: Anthropic API backend for ai.decide blocks.

Drop-in replacement for MockAiRuntime. Same interface, real reasoning.

The runtime packages the weigh inputs into a structured prompt,
calls the Anthropic API, parses the response into a typed result
with a confidence score, and returns an AiDecision.

Design principles:
  - The model never sees raw code — it sees business intent
  - Inputs are labeled plainly: "transaction amount: 75000"
  - The model returns JSON: { result, confidence, explanation }
  - Confidence below threshold → fell_back = True, not_confident fires
  - Every call is auditable — full input/output stored in AiDecision

Usage:
    from mohio_ai import AnthropicAiRuntime
    ai = AnthropicAiRuntime(api_key="sk-ant-...")
    interp = MohioInterpreter(ai=ai)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from mohio_interpreter import AiDecision, MohioValue


# ── Model config ──────────────────────────────────────────────────────────────

DEFAULT_MODEL   = "claude-sonnet-4-20250514"
MAX_TOKENS      = 512
DEFAULT_THRESHOLD = 0.85


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_system_prompt(decision_name: str, return_type: str) -> str:
    """
    Build the system prompt for an ai.decide call.

    The model acts as a domain-aware reasoning engine.
    It must return structured JSON — nothing else.
    """
    type_guidance = {
        "boolean": "true or false",
        "text":    "a short descriptive string",
        "number":  "a numeric value",
        "result":  "approved, denied, pending, or flagged",
    }.get(return_type, "an appropriate value")

    return f"""You are a reasoning engine embedded in the Mohio programming language.

Your job is to evaluate a decision called "{decision_name}" based on the inputs provided.

You must respond with ONLY a JSON object — no explanation, no preamble, no markdown.

The JSON must have exactly these three fields:
  result      — {type_guidance}
  confidence  — a number between 0.0 and 1.0 representing how certain you are
  explanation — one sentence explaining your reasoning in plain English

Example response format:
{{"result": true, "confidence": 0.92, "explanation": "Transaction amount exceeds typical pattern for this member."}}

If you are genuinely uncertain, return a lower confidence score (below 0.85).
Do not hedge with language — hedge with the confidence number."""


def _build_user_prompt(decision_name: str, inputs: dict,
                       return_type: str) -> str:
    """
    Build the user prompt from the weigh inputs.
    Formats them as readable labeled pairs — never raw code.
    """
    lines = [f"Decision: {decision_name}", ""]

    if inputs:
        lines.append("Inputs:")
        for key, value in inputs.items():
            # Unwrap MohioValue
            if isinstance(value, MohioValue):
                value = value.to_python()
            # Format key: strip dotted prefix for readability
            label = key.split(".")[-1].replace("_", " ")
            lines.append(f"  {label}: {value}")
    else:
        lines.append("No inputs provided.")

    lines.append("")
    lines.append(f"Return type expected: {return_type}")
    lines.append("")
    lines.append("Respond with only the JSON object.")

    return "\n".join(lines)


def _parse_response(raw: str, return_type: str) -> tuple[Any, float, str]:
    """
    Parse the model's JSON response.
    Returns (result, confidence, explanation).
    Falls back gracefully on malformed output.
    """
    # Strip any accidental markdown fences
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try extracting the first JSON object from the response
        match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return None, 0.0, f"Model returned unparseable response: {text[:100]}"
        else:
            return None, 0.0, f"No JSON found in response: {text[:100]}"

    result     = data.get("result")
    confidence = float(data.get("confidence", 0.0))
    explanation = str(data.get("explanation", ""))

    # Coerce result to the declared return type
    if return_type == "boolean":
        if isinstance(result, bool):
            pass
        elif isinstance(result, str):
            result = result.lower() in ("true", "yes", "1", "approved")
        elif isinstance(result, (int, float)):
            result = bool(result)

    elif return_type == "number":
        try:
            result = float(result)
        except (TypeError, ValueError):
            result = 0.0

    elif return_type == "text":
        result = str(result) if result is not None else ""

    return result, confidence, explanation


# ── Anthropic runtime ─────────────────────────────────────────────────────────

class AnthropicAiRuntime:
    """
    Real AI runtime backed by the Anthropic API.

    Drop-in replacement for MockAiRuntime — same interface,
    same AiDecision return type. Swap in by passing to MohioInterpreter:

        ai = AnthropicAiRuntime()
        interp = MohioInterpreter(ai=ai)
    """

    def __init__(self,
                 api_key: Optional[str] = None,
                 model: str = DEFAULT_MODEL,
                 verbose: bool = False):
        """
        Args:
            api_key: Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
            model:   Model to use. Defaults to claude-sonnet-4-20250514.
            verbose: Print prompts and raw responses to stdout.
        """
        import anthropic as _anthropic

        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError(
                "Anthropic API key required.\n"
                "Set ANTHROPIC_API_KEY environment variable or pass api_key=."
            )

        self._client  = _anthropic.Anthropic(api_key=key)
        self._model   = model
        self._verbose = verbose

        # Cache of overrides — same as MockAiRuntime for test compatibility
        self._overrides: dict[str, AiDecision] = {}

    def set_response(self, decision_name: str, result: Any,
                     confidence: float = 0.95):
        """
        Pre-configure a response for a named decision.
        Overrides bypass the API — used for testing.
        """
        self._overrides[decision_name] = AiDecision(
            result=result,
            confidence=confidence,
            model="override",
            inputs={},
        )

    def decide(self, name: str, inputs: dict,
               threshold: float = DEFAULT_THRESHOLD,
               return_type: str = "boolean") -> AiDecision:
        """
        Execute an AI decision via the Anthropic API.

        Args:
            name:        The ai.decide block name (e.g. "isFraudulent")
            inputs:      The weigh inputs — dict of name → MohioValue
            threshold:   Confidence threshold from the block declaration
            return_type: Declared return type ("boolean", "text", etc.)

        Returns:
            AiDecision with result, confidence, model, inputs, explanation
        """
        # Override takes priority — useful for tests alongside real AI
        if name in self._overrides:
            d = self._overrides[name]
            d.inputs = inputs
            return d

        system = _build_system_prompt(name, return_type)
        user   = _build_user_prompt(name, inputs, return_type)

        if self._verbose:
            print(f"\n[ai.decide → API] {name}")
            print(f"  Model: {self._model}")
            print(f"  Inputs: {list(inputs.keys())}")
            print(f"  Prompt:\n{user}")

        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            raw = message.content[0].text

            if self._verbose:
                print(f"  Raw response: {raw}")

        except Exception as e:
            # API failure — return a fell_back decision rather than crashing
            return AiDecision(
                result=None,
                confidence=0.0,
                model=self._model,
                inputs=inputs,
                explanation=f"API call failed: {e}",
                fell_back=True,
            )

        result, confidence, explanation = _parse_response(raw, return_type)

        fell_back = confidence < threshold

        if self._verbose:
            print(f"  Result: {result}  Confidence: {confidence:.2f}  "
                  f"Threshold: {threshold}  Fell back: {fell_back}")

        return AiDecision(
            result=result,
            confidence=confidence,
            model=self._model,
            inputs=inputs,
            explanation=explanation,
            fell_back=fell_back,
        )

    def explain(self, decision: AiDecision,
                audience: str = "developer",
                fmt: str = "paragraph") -> str:
        """Return the explanation stored on the decision, or a formatted version."""
        if decision.explanation:
            return decision.explanation
        return (
            f"Decision: {decision.result} "
            f"(confidence {decision.confidence:.0%}, model {decision.model})."
        )

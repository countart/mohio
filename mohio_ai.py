# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
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


# ── Model config (environment-configurable per the standing rule) ───────────────
# Anything hardwired -- provider, model, endpoint, key -- becomes env-configured. Model names and
# endpoints, not just keys. A8, 2026-07-31.

def _envval(name, default=""):
    return (os.environ.get(name, "") or "").strip().strip('"').strip("'") or default

# Per-provider default text/decision model. Used when no model is named on the block, and to pick a
# startup default from whichever provider key is present.
DEFAULT_ANTHROPIC_MODEL = _envval("MOHIO_AI_MODEL") or _envval("MOHIO_AI_ANTHROPIC_MODEL") or "claude-sonnet-5"
DEFAULT_OPENAI_MODEL    = _envval("MOHIO_AI_OPENAI_MODEL") or "gpt-4o"
DEFAULT_GEMINI_MODEL    = _envval("MOHIO_AI_GEMINI_MODEL") or "gemini-1.5-pro"
DEFAULT_MODEL   = DEFAULT_ANTHROPIC_MODEL          # back-compat alias
DEFAULT_IMAGE_MODEL = _envval("MOHIO_AI_IMAGE_MODEL") or "gpt-image-1"
DEFAULT_VIDEO_MODEL = _envval("MOHIO_AI_VIDEO_MODEL") or "sora-2"
DEFAULT_AUDIO_MODEL = _envval("MOHIO_AI_AUDIO_MODEL") or "tts-1"      # OpenAI TTS by default (see A8)
MAX_TOKENS      = int(_envval("MOHIO_AI_MAX_TOKENS", "512"))
DEFAULT_THRESHOLD = 0.85
CALL_CAP        = int(_envval("MOHIO_AI_CALL_CAP", "0"))              # 0 = unlimited; classroom sets a cap

# ── Per-model USD pricing (A8 cost-cap fix, 2026-08-06) ───────────────────────────────
# $ per 1,000,000 tokens, input and output priced separately -- output is usually pricier.
# Sourced from each provider's own published pricing as of 2026-08-06. This table WILL
# drift as providers change pricing; it is the one place to update, not scattered across
# call sites. Longest-prefix match against the model name (same style as the existing
# claude*/gpt*/gemini* routing). An unrecognized model within a known vendor family falls
# back to that family's most expensive listed rate, and a totally unknown model falls back
# to the most expensive rate overall -- a cost cap must never silently under-count an
# unrecognized model as free or cheap; the safe direction to be wrong is "counts as more."
MODEL_PRICING_PER_1M = {
    "claude-opus":      (15.00, 75.00),
    "claude-sonnet":    (3.00, 15.00),
    "claude-haiku":     (0.80, 4.00),
    "gpt-4o":           (2.50, 10.00),
    "gpt-4":            (30.00, 60.00),
    "gpt-3.5":          (0.50, 1.50),
    "o1":               (15.00, 60.00),
    "o3":               (2.00, 8.00),
    "gemini-1.5-pro":   (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-2":         (1.25, 5.00),
}
_FALLBACK_PRICING_PER_1M = {
    "claude": (15.00, 75.00),   # opus rate -- worst case for an unrecognized claude model
    "gpt":    (30.00, 60.00),   # gpt-4 rate -- worst case for an unrecognized gpt model
    "o":      (15.00, 60.00),   # o1 rate -- worst case for an unrecognized o-series model
    "gemini": (1.25, 5.00),     # 1.5-pro rate -- worst case for an unrecognized gemini model
}

def _price_for_model(model):
    """(input_$_per_1M, output_$_per_1M) for a model name. Longest matching prefix wins,
    so 'claude-opus-4' matches 'claude-opus' rather than a shorter 'claude-' entry."""
    m = (model or "").lower()
    best = None
    for prefix, rate in MODEL_PRICING_PER_1M.items():
        if m.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, rate)
    if best:
        return best[1]
    for prefix, rate in _FALLBACK_PRICING_PER_1M.items():
        if m.startswith(prefix):
            return rate
    return _FALLBACK_PRICING_PER_1M["claude"]  # totally unknown model -- worst case, never free

def _cost_for_tokens(model, input_tokens, output_tokens):
    """Real USD cost for one call's real token usage, using the pricing table above."""
    in_rate, out_rate = _price_for_model(model)
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate

@dataclass
class CompletionResult:
    """_complete()'s real return shape (2026-08-06). Every provider's raw response DOES
    carry real usage/token counts -- _complete() used to discard all of it and return bare
    text, which is why the cost-cap boundary gate in ai.agent's `limits` block could never
    fire: nothing upstream ever had a real number to check. Text-only callers still work
    unchanged (CompletionResult.text), callers that need cost now have it too."""
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class AiProviderError(RuntimeError):
    """Raised by decide() when NO real model answer was obtained: the API call itself
    failed (network/auth/timeout, chain exhaustion) or it returned but could not be
    parsed into a decision (malformed/empty response). Ruled 2026-08-04: this must
    stay distinct from a genuine low-confidence answer, which returns normally with
    fell_back=True instead of raising. Every caller of decide() (ai.decide, ai.agent,
    ai.compare, ai.respond, mio ai-check) catches this and surfaces it loudly --
    on.failure / not_confident -- rather than letting it look like a real result."""
    pass


# ── Chain runtime ─────────────────────────────────────────────────────────────

class ResolvedChain:
    """
    A resolved ai.connect — knows which provider is active.
    Handles two failure modes:

    1. Pre-loop resolution: resolve() is called once before processing.
       All records in the loop use the cached active_provider — free.

    2. Mid-loop forced fallback: if the active provider fails during
       execution (credits exhausted, rate limit, timeout), fallback()
       advances to the next provider and updates active_provider in place.
       Record 48 automatically uses the new provider without any
       re-resolution cost — and so do records 49, 50, ..., 100.

    The chain never goes backwards. Once a provider is abandoned mid-loop,
    it stays abandoned for the rest of that execution.
    """

    def __init__(self, name: str, providers: list[str]):
        self.name = name
        self.providers = providers
        self.active_provider: Optional[str] = None
        self._resolved = False
        self._active_index = 0          # index into providers list
        self._fallback_count = 0        # how many mid-loop fallbacks occurred
        self._fallback_log: list[str] = []  # audit trail of forced switches

    def resolve(self, test_fn) -> bool:
        """
        Pre-loop resolution: try each provider, cache the first available one.
        test_fn(provider) → bool (True = provider is available).
        """
        for i, provider in enumerate(self.providers):
            if test_fn(provider):
                self.active_provider = provider
                self._active_index = i
                self._resolved = True
                return True
        return False

    def fallback(self, reason: str = "") -> Optional[str]:
        """
        Mid-loop forced fallback: the current provider failed during execution.
        Advances to the next available provider and updates active_provider
        IN PLACE so all subsequent calls in this execution use the new provider.

        Returns the new provider name, or None if all providers exhausted.

        This is the critical behavior: record 48 triggers a fallback,
        records 49-100 automatically use the new provider at zero cost.
        No re-resolution. No re-evaluation. The switch persists.
        """
        previous = self.active_provider
        next_index = self._active_index + 1

        if next_index >= len(self.providers):
            log_entry = (f"Chain '{self.name}' exhausted all providers. "
                         f"Last active: {previous}. Reason: {reason}")
            self._fallback_log.append(log_entry)
            print(f"  [ai.connect] EXHAUSTED — {log_entry}")
            return None

        self._active_index = next_index
        self.active_provider = self.providers[next_index]
        self._fallback_count += 1

        log_entry = (f"Chain '{self.name}' fallback #{self._fallback_count}: "
                     f"{previous} → {self.active_provider}. Reason: {reason}")
        self._fallback_log.append(log_entry)
        print(f"  [ai.connect] FALLBACK — {log_entry}")
        print(f"  [ai.connect] Remaining records will use: {self.active_provider}")

        return self.active_provider

    @property
    def resolved(self) -> bool:
        return self._resolved

    @property
    def has_fallbacks(self) -> bool:
        return self._fallback_count > 0


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_system_prompt(decision_name, return_type,
                         prompt=None, persona=None):
    """
    Build the system prompt for an ai.decide call.

    Three layers — all optional:
    1. prompt   — replaces the generic decision description (focuses the vote)
    2. [core]   — structured JSON contract — always present, never overridden
    3. persona  — shapes the explanation field only (voice, not verdict)
    """
    type_guidance = {
        "boolean": "true or false",
        "text":    "a short descriptive string",
        "number":  "a numeric value",
        "result":  "approved, denied, pending, or flagged",
    }.get(return_type, "an appropriate value")

    if prompt:
        decision_description = "Your specific task: " + prompt.strip()
    else:
        decision_description = (
            'Your job is to evaluate a decision called "' + decision_name + '" '
            "based on the inputs provided."
        )

    persona_instruction = ""
    if persona:
        persona_instruction = (
            "\n\nFor the explanation field only: " + persona.strip() +
            " The result and confidence must remain objective."
        )

    core = (
        "You are a reasoning engine embedded in the Mohio programming language."
        "\n\n" + decision_description + "\n\n"
        "You must respond with ONLY a JSON object — no explanation, no preamble, no markdown."
        "\n\nThe JSON must have exactly these three fields:"
        "\n  result      — " + type_guidance +
        "\n  confidence  — a number between 0.0 and 1.0 representing how certain you are"
        "\n  explanation — one sentence explaining your reasoning in plain English"
        "\n\nExample response format:"
        '\n{"result": true, "confidence": 0.92, '
        '"explanation": "Transaction amount exceeds typical pattern for this member."}'
        "\n\nIf you are genuinely uncertain, return a lower confidence score (below 0.85)."
        "\nDo not hedge with language — hedge with the confidence number."
        + persona_instruction
    )
    return core


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

    The API call SUCCEEDED to reach here -- a response came back. If that response
    cannot be read as a decision (no JSON, unparseable JSON), that is not a low-confidence
    opinion, it is no opinion at all -- raises AiProviderError instead of faking a
    result, so it is never mistaken for the model genuinely being unsure (2026-08-04).
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
                raise AiProviderError(f"Model returned unparseable response: {text[:100]}")
        else:
            raise AiProviderError(f"No JSON found in response: {text[:100]}")

    result     = data.get("result")
    confidence = float(data.get("confidence", 0.0))
    explanation = str(data.get("explanation", ""))

    # Coerce result to the declared return type
    if return_type == "boolean":
        if isinstance(result, bool):
            pass
        elif isinstance(result, str):
            # A string result that isn't a recognized true/false token used to fall
            # through to `in (...)` silently, so "maybe"/"unsure"/"N/A" all became a
            # plain False -- indistinguishable from the model genuinely answering no.
            # Same disease as the top-level "no JSON" / "unparseable JSON" cases just
            # above: the model answered, but not with a real boolean, so this raises
            # instead of guessing (2026-08-04).
            _norm = result.strip().lower()
            _true_strings  = ("true", "yes", "1", "approved")
            _false_strings = ("false", "no", "0", "denied")
            if _norm in _true_strings:
                result = True
            elif _norm in _false_strings:
                result = False
            else:
                raise AiProviderError(
                    f"Model returned a boolean decision as an unrecognized string: "
                    f"{result!r}. Expected true/false, yes/no, 1/0, or approved/denied.")
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
                 model: Optional[str] = None,
                 verbose: bool = False):
        """
        Args:
            api_key: Anthropic API key (back-compat). Any provider key from the environment
                     (ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY) also activates the runtime.
            model:   Default model. If unset, chosen from whichever provider key is present.
            verbose: Print prompts and raw responses to stdout.

        A8 (2026-07-31): startup accepts ANY provider key and picks a default model from whichever is
        present. The Anthropic client is built LAZILY (only when a claude model actually runs), so a
        developer with only an OpenAI or Gemini key can run the headline feature.
        """
        self._anthropic_key = (api_key or _envval("ANTHROPIC_API_KEY")) or ""
        self._openai_key    = _envval("OPENAI_API_KEY")
        self._gemini_key    = _envval("GEMINI_API_KEY") or _envval("GOOGLE_API_KEY")
        if not (self._anthropic_key or self._openai_key or self._gemini_key):
            raise RuntimeError(
                "No AI provider key found. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY "
                "(or run with MOHIO_AI=mock for the labeled mock provider).")

        # Default model: explicit arg > env (MOHIO_AI_MODEL) > the default for the key that IS present.
        self._model = model or (
            DEFAULT_ANTHROPIC_MODEL if self._anthropic_key else
            DEFAULT_OPENAI_MODEL    if self._openai_key else
            DEFAULT_GEMINI_MODEL)
        self._client  = None            # lazy Anthropic client (built on first claude model use)
        self._verbose = verbose
        self._overrides: dict[str, AiDecision] = {}
        self._chains: dict[str, ResolvedChain] = {}
        self._calls    = 0              # per-session AI call counter (cost-cap hook)
        self._call_cap = CALL_CAP       # 0 = unlimited

    def _anthropic_client(self):
        """Build the Anthropic client on first claude use. A gpt/gemini-only deployment never
        reaches this, so it needs neither the key nor the `anthropic` package."""
        if self._client is None:
            if not self._anthropic_key:
                raise RuntimeError(
                    "This request needs a Claude model but ANTHROPIC_API_KEY is not set. Set it, or "
                    "name a gpt*/gemini* model, or set MOHIO_AI_MODEL to a model your key supports.")
            import anthropic as _anthropic
            try:
                self._client = _anthropic.Anthropic(api_key=self._anthropic_key)
            except Exception as e:
                raise RuntimeError(f"Failed to initialize Anthropic client: {e}")
        return self._client

    def _tick(self):
        """Count one provider call and enforce the per-session cap (MOHIO_AI_CALL_CAP; 0 = unlimited).
        Per-session = per runtime instance (one per request under `mio serve`). Uses getattr so a
        bare object built via __new__ (used in unit tests of _complete routing) still works."""
        self._calls = getattr(self, "_calls", 0) + 1
        cap = getattr(self, "_call_cap", 0)
        if cap and self._calls > cap:
            raise RuntimeError(
                f"AI call cap reached: {cap} calls per session (MOHIO_AI_CALL_CAP). "
                f"This session attempted {self._calls}.")

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

    def register_chain(self, chain_name: str, providers: list[str]) -> ResolvedChain:
        """Register an ai.connect declaration. Resolution happens lazily on first use."""
        chain = ResolvedChain(chain_name, providers)
        self._chains[chain_name] = chain
        return chain

    def resolve_chain(self, chain_name: str) -> Optional[str]:
        """
        Resolve which provider in the chain is currently available.
        Tests each provider with a minimal ping. Caches the result.
        This is the on.resolve step — pay this cost once, before any loops.
        """
        if chain_name not in self._chains:
            return None

        chain = self._chains[chain_name]
        if chain.resolved:
            return chain.active_provider  # Already resolved — free

        def test_provider(provider: str) -> bool:
            """Ping a provider with a minimal request to check availability.
            Works for any vendor via the _complete dispatcher."""
            try:
                self._complete(provider, "Reply with: ok", "ping",
                               temperature=0, max_tokens=5)
                return True
            except Exception as e:
                print(f"  [ai.connect] Provider {provider!r} unavailable: {type(e).__name__}")
                return False

        resolved = chain.resolve(test_provider)
        if resolved:
            print(f"  [ai.connect] '{chain_name}' resolved → {chain.active_provider}")
        else:
            print(f"  [ai.connect] WARNING: no providers available for '{chain_name}'")
        return chain.active_provider

    def _complete(self, model, system, user, temperature=None, max_tokens=None):
        """Dispatch a completion to the right provider based on model name.
        Returns a CompletionResult (text + real input/output token counts, 2026-08-06 --
        see CompletionResult's docstring for why). Reads each provider's key from env.
        This is the single seam that makes ai.connect multi-provider work."""
        self._tick()
        mt = max_tokens or MAX_TOKENS
        m = (model or "").lower()
        if m.startswith("claude") or "sonnet" in m or "haiku" in m or "opus" in m:
            msg = self._anthropic_client().messages.create(
                model=model, max_tokens=mt, system=system,
                temperature=temperature if temperature is not None else 1.0,
                messages=[{"role": "user", "content": user}],
            )
            if not msg.content:
                raise ValueError("Anthropic returned empty content")
            usage = getattr(msg, 'usage', None)
            return CompletionResult(
                text=msg.content[0].text,
                input_tokens=(getattr(usage, 'input_tokens', 0) or 0) if usage else 0,
                output_tokens=(getattr(usage, 'output_tokens', 0) or 0) if usage else 0,
            )
        if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3") or m.startswith("openai"):
            return self._complete_openai(model, system, user, temperature, mt)
        if m.startswith("gemini") or m.startswith("google"):
            return self._complete_gemini(model, system, user, temperature, mt)
        raise RuntimeError(
            f"ai.connect: unknown provider/model '{model}'. "
            f"Supported prefixes: claude*, gpt*/o1*/o3*, gemini*.")

    def _complete_openai(self, model, system, user, temperature, max_tokens):
        import os, json as _json, urllib.request
        key = (os.environ.get("OPENAI_API_KEY", "") or "").strip().strip('"').strip("'")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        body = _json.dumps({
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature if temperature is not None else 1.0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions", data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            data = _json.loads(r.read().decode())
        usage = data.get("usage") or {}
        return CompletionResult(
            text=data["choices"][0]["message"]["content"],
            input_tokens=usage.get("prompt_tokens", 0) or 0,
            output_tokens=usage.get("completion_tokens", 0) or 0,
        )

    def _complete_gemini(self, model, system, user, temperature, max_tokens):
        import os, json as _json, urllib.request
        key = ((os.environ.get("GEMINI_API_KEY", "") or
                os.environ.get("GOOGLE_API_KEY", "")) or "").strip().strip('"').strip("'")
        if not key:
            raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) not set")
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        body = _json.dumps({
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": temperature if temperature is not None else 1.0,
                "maxOutputTokens": max_tokens},
        }).encode()
        req = urllib.request.Request(url, data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            data = _json.loads(r.read().decode())
        # Field names per Gemini's documented generateContent response shape
        # (usageMetadata.promptTokenCount / .candidatesTokenCount). NOT live-verified
        # against a real Gemini response in this build (no key available) -- flagging
        # that explicitly rather than overclaiming; verify against a real call before
        # relying on Gemini cost figures specifically.
        usage = data.get("usageMetadata") or {}
        return CompletionResult(
            text=data["candidates"][0]["content"]["parts"][0]["text"],
            input_tokens=usage.get("promptTokenCount", 0) or 0,
            output_tokens=usage.get("candidatesTokenCount", 0) or 0,
        )

    # ── Pre-call token estimation (C, 2026-08-06 -- the cost-cap fix's second slice) ──
    # Real per-provider token counting, no heuristic, ever: a character-count guess was
    # considered and rejected (requirements.txt has the full reasoning) because pre-call
    # refusal exists specifically to catch a breach before it happens, and an inaccurate
    # estimate defeats that in either direction -- wrongly refusing a valid call, or
    # wrongly letting a real breach through. Each provider gets its own accurate method:
    # Claude has messages.count_tokens (SDK-native), Gemini has its own :countTokens REST
    # endpoint (confirmed 2026-08-06 against ai.google.dev -- NOT the same bucket as GPT,
    # checked rather than assumed), GPT has neither, so it genuinely needs tiktoken.
    def estimate_input_tokens(self, model, messages, system=None, tools=None):
        m = (model or "").lower()
        if m.startswith("claude") or "sonnet" in m or "haiku" in m or "opus" in m:
            return self._estimate_tokens_claude(model, messages, system, tools)
        if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3") or m.startswith("openai"):
            return self._estimate_tokens_openai(model, messages, system)
        if m.startswith("gemini") or m.startswith("google"):
            return self._estimate_tokens_gemini(model, messages, system)
        raise RuntimeError(
            f"ai.agent pre-call cost estimate: unknown provider/model '{model}'. "
            f"Supported prefixes: claude*, gpt*/o1*/o3*, gemini*.")

    def _estimate_tokens_claude(self, model, messages, system=None, tools=None):
        """Real, exact input-token count via the Anthropic SDK's own count_tokens --
        the same call agent_turn() and _complete() would send, so the estimate reflects
        the real upcoming request, not an approximation of it."""
        kwargs = dict(model=model, messages=messages or [])
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        result = self._anthropic_client().messages.count_tokens(**kwargs)
        return result.input_tokens

    def _estimate_tokens_openai(self, model, messages, system=None):
        """GPT has no count-tokens API call -- tiktoken is the accurate, offline
        alternative (network-free, so this adds no latency to the boundary-gate check).
        Falls back to a modern general encoding for a model tiktoken does not
        recognize by name, rather than failing the estimate outright."""
        import tiktoken
        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            enc = tiktoken.get_encoding("o200k_base")
        total = len(enc.encode(system)) if system else 0
        for msg in (messages or []):
            content = msg.get("content", "") if isinstance(msg, dict) else msg
            total += len(enc.encode(str(content)))
        return total

    def _estimate_tokens_gemini(self, model, messages, system=None):
        """Real, exact input-token count via Gemini's own :countTokens REST endpoint --
        confirmed to exist 2026-08-06 (ai.google.dev/api/tokens), same class of real
        provider-native method as Claude's count_tokens, not a GPT-style tiktoken
        workaround. Response field is totalTokens (distinct from generateContent's own
        usageMetadata.promptTokenCount -- a different endpoint, a different response
        shape). NOT live-verified against a real Gemini response in this build (no key
        available) -- flagging rather than overclaiming, same as _complete_gemini's own
        usage-field note."""
        import os, json as _json, urllib.request
        key = ((os.environ.get("GEMINI_API_KEY", "") or
                os.environ.get("GOOGLE_API_KEY", "")) or "").strip().strip('"').strip("'")
        if not key:
            raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) not set")
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:countTokens?key={key}")
        contents = [{"role": "user", "parts": [{"text": str(
            msg.get("content", "") if isinstance(msg, dict) else msg)}]}
            for msg in (messages or [])]
        if system:
            body = {"generateContentRequest": {
                "contents": contents,
                "systemInstruction": {"parts": [{"text": system}]}}}
        else:
            body = {"contents": contents}
        req = urllib.request.Request(url, data=_json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            data = _json.loads(r.read().decode())
        return data.get("totalTokens", 0) or 0

    # ── ai.create generators: text / image / video (multi-provider) ──────────
    def generate_text(self, *, goal, persona="", context="", style="",
                      model=None, temperature=None, max_tokens=None):
        """Text generation — reuses the multi-provider _complete dispatch
        (Claude / GPT / Gemini, routed by model name)."""
        model = model or self._model
        system = ("You are a generation assistant. Produce only the requested "
                  "content, with no preamble or commentary.")
        if persona:
            system += f" Voice: {persona}."
        if style:
            system += f" Style: {style}."
        user = goal or ""
        if context:
            user = f"{user}\n\nContext: {context}"
        return self._complete(model, system, user, temperature, max_tokens).text

    def generate_image(self, *, goal, style="", negative="", size="1024x1024",
                       model=None, n=1):
        """Image generation. Routes by model:
        dall-e-* / gpt-image-* -> OpenAI ; imagen-* -> Google."""
        self._tick()
        model = model or DEFAULT_IMAGE_MODEL
        m = model.lower()
        prompt = goal or ""
        if style:
            prompt += f". Style: {style}"
        if negative:
            prompt += f". Do not include: {negative}"
        if m.startswith(("dall-e", "gpt-image", "openai")):
            return self._image_openai(model, prompt, size, n)
        if m.startswith(("imagen", "gemini", "google")):
            return self._image_google(model, prompt)
        raise RuntimeError(
            f"ai.create image: unknown model '{model}'. "
            f"Supported: dall-e-3, gpt-image-1, imagen-3.0-*.")

    def _image_openai(self, model, prompt, size, n=1):
        import os, json as _json, urllib.request
        key = (os.environ.get("OPENAI_API_KEY", "") or "").strip().strip('"').strip("'")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        body = _json.dumps({"model": model, "prompt": prompt,
                            "size": size or "1024x1024", "n": n}).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/images/generations", data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            data = _json.loads(r.read().decode())
        item = data["data"][0]
        return item.get("url") or item.get("b64_json")

    def _image_google(self, model, prompt):
        import os, json as _json, urllib.request
        key = ((os.environ.get("GEMINI_API_KEY", "") or
                os.environ.get("GOOGLE_API_KEY", "")) or "").strip().strip('"').strip("'")
        if not key:
            raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) not set")
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:predict?key={key}")
        body = _json.dumps({"instances": [{"prompt": prompt}],
                            "parameters": {"sampleCount": 1}}).encode()
        req = urllib.request.Request(url, data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            data = _json.loads(r.read().decode())
        preds = data.get("predictions", [{}])
        return preds[0].get("bytesBase64Encoded") or preds[0].get("image")

    def generate_video(self, *, goal, style="", duration=None, size=None, model=None):
        """Video generation (async create + poll). Routes by model:
        sora-* -> OpenAI ; veo-* -> Google. Returns a URL or job reference."""
        self._tick()
        model = model or DEFAULT_VIDEO_MODEL
        m = model.lower()
        prompt = goal or ""
        if style:
            prompt += f". Style: {style}"
        if m.startswith(("sora", "openai")):
            return self._video_openai(model, prompt, duration, size)
        if m.startswith(("veo", "gemini", "google")):
            return self._video_google(model, prompt, duration)
        raise RuntimeError(
            f"ai.create video: unknown model '{model}'. Supported: sora-2, veo-3.0-*.")

    def _video_openai(self, model, prompt, duration, size):
        import os, json as _json, time as _t, urllib.request
        key = (os.environ.get("OPENAI_API_KEY", "") or "").strip().strip('"').strip("'")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        hdr = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        payload = {"model": model, "prompt": prompt}
        if duration:
            payload["seconds"] = str(int(duration))
        if size:
            payload["size"] = size
        req = urllib.request.Request("https://api.openai.com/v1/videos",
                                     data=_json.dumps(payload).encode(),
                                     headers=hdr, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            job = _json.loads(r.read().decode())
        job_id = job.get("id")
        for _ in range(120):  # poll up to ~10 min
            if job.get("status") in ("completed", "succeeded"):
                break
            if job.get("status") in ("failed", "cancelled"):
                raise RuntimeError(f"video job {job_id} {job.get('status')}")
            _t.sleep(5)
            preq = urllib.request.Request(
                f"https://api.openai.com/v1/videos/{job_id}", headers=hdr)
            with urllib.request.urlopen(preq, timeout=30) as r:
                job = _json.loads(r.read().decode())
        return job.get("url") or job_id

    def _video_google(self, model, prompt, duration):
        import os, json as _json, time as _t, urllib.request
        key = ((os.environ.get("GEMINI_API_KEY", "") or
                os.environ.get("GOOGLE_API_KEY", "")) or "").strip().strip('"').strip("'")
        if not key:
            raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) not set")
        base = "https://generativelanguage.googleapis.com/v1beta"
        url = f"{base}/models/{model}:predictLongRunning?key={key}"
        params = {}
        if duration:
            params["durationSeconds"] = int(duration)
        body = _json.dumps({"instances": [{"prompt": prompt}],
                            "parameters": params}).encode()
        req = urllib.request.Request(url, data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            op = _json.loads(r.read().decode())
        op_name = op.get("name")
        for _ in range(120):
            if op.get("done"):
                break
            _t.sleep(5)
            preq = urllib.request.Request(f"{base}/{op_name}?key={key}")
            with urllib.request.urlopen(preq, timeout=30) as r:
                op = _json.loads(r.read().decode())
        resp = op.get("response", {})
        vids = resp.get("generatedVideos") or resp.get("videos") or [{}]
        return (vids[0].get("video", {}).get("uri")
                or vids[0].get("uri") or op_name)

    def generate_audio(self, *, goal, voice="", pace=None, style="", model=None, **kw):
        """Text-to-speech. A8 (2026-07-31): OpenAI TTS by default (reuses the existing OpenAI seam,
        one REST call, no new dependency). Set MOHIO_AI_AUDIO_MODEL to an `eleven*` model (or pass a
        model starting `eleven`) to route to ElevenLabs for higher voice quality (podblaster.ai).
        Returns raw audio bytes."""
        self._tick()
        model = model or DEFAULT_AUDIO_MODEL
        if str(model).lower().startswith("eleven"):
            return self._audio_elevenlabs(goal or "", voice, model)
        return self._audio_openai(goal or "", voice or "alloy", model, pace)

    def _audio_openai(self, text, voice, model, pace=None):
        import json as _json, urllib.request
        key = _envval("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set (needed for OpenAI TTS audio).")
        payload = {"model": model if str(model).startswith("tts") else "tts-1",
                   "input": text, "voice": voice or "alloy"}
        if pace:
            try: payload["speed"] = float(pace)
            except Exception: pass
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/speech", data=_json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()          # audio bytes (mp3)

    def _audio_elevenlabs(self, text, voice, model):
        import json as _json, urllib.request
        key = _envval("ELEVENLABS_API_KEY")
        if not key:
            raise RuntimeError("ELEVENLABS_API_KEY not set (needed for ElevenLabs audio).")
        voice_id = voice or _envval("MOHIO_AI_ELEVENLABS_VOICE", "21m00Tcm4TlvDq8ikWAM")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        body = _json.dumps({"text": text,
                            "model_id": _envval("MOHIO_AI_ELEVENLABS_MODEL", "eleven_multilingual_v2")}).encode()
        req = urllib.request.Request(url, data=body,
            headers={"xi-api-key": key, "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()          # audio bytes (mp3)

    def agent_turn(self, *, messages, tools=None, model=None,
                   temperature=None, max_tokens=None):
        """One real agent turn: a single Messages API call with tools, translated
        into the simple AgentTurn contract the loop understands. The provider
        either answers (text) or asks to use one tool; the content-block detail
        is handled here so the loop never sees it. A provider failure RAISES
        AiProviderError (2026-08-04, Unit 2 of the same ruling as decide()):
        it used to return a fake text turn ("[provider error: ...]") that the
        loop's `if turn.kind == 'text'` treated as the agent finishing normally
        -- structurally identical to a genuine completion, with the failure
        text bound as if it were the agent's real answer. The interpreter's
        loop already correctly catches a raised exception here and routes to
        on.failure / not confident (now that AiAgentBlock's body is scanned
        for them the same way ai.decide's is)."""
        from mohio_interpreter import AgentTurn
        m  = model or self._model
        ml = m.lower()
        mt = max_tokens or 1024
        is_claude = ml.startswith("claude") or "sonnet" in ml or "haiku" in ml or "opus" in ml
        if not is_claude:
            # A8: route by prefix. gpt/gemini agent turns run the TEXT path -- tool-calling on those
            # providers needs per-provider tool-schema translation, deferred (see backlog).
            if tools:
                raise RuntimeError(
                    "multi-provider agent tool-calling is declared but not yet built: ai.agent tools "
                    f"run on Claude only for now. Use a claude* model for tool-using agents, or run "
                    f"without tools on '{m}'. Tracked for a future release.")
            try:
                _user = "\n".join(str(x.get("content", "")) if isinstance(x, dict) else str(x)
                                  for x in (messages or []))
                _res = self._complete(m, "You are an agent. Answer the request directly.",
                                      _user, temperature, mt)   # _complete ticks the cap
            except Exception as e:
                raise AiProviderError(f"ai.agent provider call failed: {type(e).__name__}: {e}")
            _tokens = _res.input_tokens + _res.output_tokens
            _cost = _cost_for_tokens(m, _res.input_tokens, _res.output_tokens)
            return AgentTurn(kind='text', text=_res.text or "Done.", tokens=_tokens, cost=_cost)
        # Claude: the full tool-capable path.
        self._tick()
        kwargs = dict(model=m, max_tokens=mt, messages=messages,
                      temperature=temperature if temperature is not None else 1.0)
        if tools:
            kwargs["tools"] = tools
        try:
            msg = self._anthropic_client().messages.create(**kwargs)
        except Exception as e:
            raise AiProviderError(f"ai.agent provider call failed: {type(e).__name__}: {e}")
        usage  = getattr(msg, 'usage', None)
        in_tok  = (getattr(usage, 'input_tokens', 0) or 0) if usage else 0
        out_tok = (getattr(usage, 'output_tokens', 0) or 0) if usage else 0
        tokens = in_tok + out_tok
        cost = _cost_for_tokens(m, in_tok, out_tok)
        if getattr(msg, 'stop_reason', None) == 'tool_use':
            for block in (getattr(msg, 'content', None) or []):
                if getattr(block, 'type', None) == 'tool_use':
                    return AgentTurn(kind='tool', tool_name=block.name,
                                     tool_input=dict(block.input or {}),
                                     tool_id=block.id, tokens=tokens, cost=cost)
        text = "".join(getattr(b, 'text', '') for b in (getattr(msg, 'content', None) or [])
                       if getattr(b, 'type', None) == 'text')
        return AgentTurn(kind='text', text=text or "Done.", tokens=tokens, cost=cost)

    def decide(self, name: str, inputs: dict,
               threshold: float = DEFAULT_THRESHOLD,
               return_type: str = "boolean",
               chain_name: Optional[str] = None,
               system_prompt: Optional[str] = None,
               persona: Optional[str] = None,
               context: Optional[str] = None,
               temperature: Optional[float] = None,
               max_tokens_override: Optional[int] = None,
               model_override: Optional[str] = None) -> AiDecision:
        """Public entry point. CONTRACT (revised 2026-08-04, ruling: the two must never
        share a behavior):

        - A genuine model answer -- even a low-confidence one -- returns normally, with
          fell_back set from the confidence-vs-threshold check. That is a real opinion
          the model formed; not_confident is the right, quiet handler for it.
        - A HARD failure -- network/auth/timeout, chain exhaustion, a response that
          could not be parsed into a decision at all -- means there IS no model answer.
          This raises AiProviderError instead of returning a fell_back AiDecision that
          looks identical to a real low-confidence one. The old guarantee ("no runtime
          failure ever escapes decide()") conflated "the app doesn't crash" with "the
          caller never learns the real answer didn't come from the model" -- only the
          first half is still true. Every caller (ai.decide, ai.agent, ai.compare,
          ai.respond, mio ai-check) catches AiProviderError and surfaces it loudly --
          on.failure, not a silent success-shaped fallback. The explicit signature is
          kept (rather than *args/**kwargs) so the runtime stays in parity with the
          Mock and so genuine wrong-argument programming errors still surface loudly
          at the call boundary.
        """
        return self._decide_impl(
            name, inputs, threshold=threshold, return_type=return_type,
            chain_name=chain_name, system_prompt=system_prompt,
            persona=persona, context=context, temperature=temperature,
            max_tokens_override=max_tokens_override,
            model_override=model_override)

    def _decide_impl(self, name: str, inputs: dict,
               threshold: float = DEFAULT_THRESHOLD,
               return_type: str = "boolean",
               chain_name: Optional[str] = None,
               system_prompt: Optional[str] = None,
               persona: Optional[str] = None,
               context: Optional[str] = None,
               temperature: Optional[float] = None,
               max_tokens_override: Optional[int] = None,
               model_override: Optional[str] = None) -> AiDecision:
        """
        Execute an AI decision via the Anthropic API.

        Args:
            name:          The ai.decide block name (e.g. "isFraudulent")
            inputs:        The weigh inputs — dict of name → MohioValue
            threshold:     Confidence threshold from the block declaration
            return_type:   Declared return type ("boolean", "text", etc.)
            chain_name:    If set, use the resolved chain's active provider
            system_prompt: From goal/prompt keyword — focuses the vote.
                           Replaces generic "evaluate decision X" description.
                           Does NOT override the JSON contract.
            context:       From context keyword — appended to user prompt.
                           Adds situational info without changing the mechanism.
            temperature:   From temperature keyword — controls creativity.
            max_tokens_override: From max tokens keyword.

        Returns:
            AiDecision with result, confidence, model, inputs, explanation
        """
        # Override takes priority — useful for tests alongside real AI
        if name in self._overrides:
            d = self._overrides[name]
            d.inputs = inputs
            return d

        # Model resolution order (2026-08-04 ruling, Stage 3 of the model-selection
        # sequence): explicit call > active chain > app default. An explicit
        # model_override on the block is the developer's own instruction for THIS
        # call, not a silent default -- it wins outright and short-circuits chain
        # resolution entirely. This was backwards: a resolved chain used to silently
        # overrule an explicit model_override, the opposite of what a developer
        # naming a specific model for a specific decision would expect.
        if model_override:
            model = model_override
        elif chain_name:
            chain = self._chains.get(chain_name)
            if chain is not None and chain.resolved and chain.active_provider:
                model = chain.active_provider  # any provider (claude/gpt/gemini)
            else:
                # A chain was explicitly named for this decision, but it produced no
                # usable provider -- never registered, never resolved (every provider
                # failed its ping), or exhausted mid-loop -- and there is no explicit
                # model_override to fall back to either (that case already returned
                # above). Silently using self._model here would answer from an
                # unnamed, possibly out-of-policy model while the decision comes back
                # looking like the chain worked (real confidence, fell_back=False):
                # the exact same disease as an ai.decide hard failure, just with a
                # wrong answer instead of no answer. Ruled 2026-08-04: never
                # indistinguishable from a normal success.
                if chain is None:
                    reason = (f"ai.connect chain '{chain_name}' is not registered "
                              f"(no ai.connect block declared it) -- this ai.decide "
                              f"named it with 'using {chain_name}' but there is "
                              f"nothing to resolve.")
                elif not chain.resolved:
                    reason = (f"ai.connect chain '{chain_name}' failed to resolve -- "
                              f"every declared provider was unreachable.")
                else:
                    reason = (f"ai.connect chain '{chain_name}' is exhausted -- "
                              f"every provider in it has failed.")
                raise AiProviderError(reason)
        else:
            model = self._model  # app default -- the runtime's own configured model

        # Build system prompt with layered model:
        # prompt  → replaces the "what to decide" description (focuses the vote)
        # persona → shapes explanation field only (voice, not verdict)
        # Build system prompt with clean separation:
        # goal/prompt → focuses the vote (what to decide)
        # persona     → shapes explanation field only
        system = _build_system_prompt(
            name, return_type,
            prompt=system_prompt,
            persona=persona,
        )

        # context → appended to user prompt (situational info, safe)
        user = _build_user_prompt(name, inputs, return_type)
        if context:
            user = f"Additional context:\n{context.strip()}\n\n{user}"

        if self._verbose:
            print(f"\n[ai.decide → API] {name}")
            print(f"  Model: {self._model}")
            print(f"  Inputs: {list(inputs.keys())}")
            print(f"  Prompt:\n{user}")

        raw = None
        res = None
        try:
            res = self._complete(model, system, user, temperature,
                                 max_tokens_override or MAX_TOKENS)
            raw = res.text
            print(f"  Raw response: {raw}")  # Always print — critical for debugging

        except Exception as e:
            error_msg = f"API call failed: {type(e).__name__}: {e}"
            print(f"  [ai.decide ERROR] {error_msg}")
            if raw is not None:
                print(f"  Partial raw: {raw!r:.200}")

            # Mid-loop forced fallback:
            # If this decide is using a chain, advance to the next provider
            # and retry ONCE with the new provider.
            # This updates the chain's active_provider in place so all
            # subsequent records in the loop use the new provider automatically.
            if chain_name and chain_name in self._chains:
                chain = self._chains[chain_name]
                new_provider = chain.fallback(reason=error_msg)
                if new_provider:
                    # Retry once with the next provider in the chain (any vendor)
                    print(f"  [ai.connect] Retrying with {new_provider}...")
                    try:
                        retry_res = self._complete(new_provider, system, user,
                                             temperature, max_tokens_override or MAX_TOKENS)
                        print(f"  Raw response (retry): {retry_res.text}")
                        result, confidence, explanation = _parse_response(retry_res.text, return_type)
                        fell_back = confidence < threshold
                        return AiDecision(
                            result=result,
                            confidence=confidence,
                            model=new_provider,
                            inputs=inputs,
                            explanation=explanation,
                            fell_back=fell_back,
                            tokens=retry_res.input_tokens + retry_res.output_tokens,
                            cost=_cost_for_tokens(new_provider, retry_res.input_tokens, retry_res.output_tokens),
                        )
                    except Exception as retry_e:
                        print(f"  [ai.connect] Retry also failed: {retry_e}")
                        # Chain will advance again on next call — no action needed here

            # No real model answer was obtained -- not even from a chain retry. This is
            # the "dead model / network failure" case, not a low-confidence opinion, so
            # it raises rather than returning a fell_back AiDecision that would be
            # indistinguishable from one (2026-08-04 ruling).
            raise AiProviderError(error_msg)

        result, confidence, explanation = _parse_response(raw, return_type)

        fell_back = confidence < threshold

        if self._verbose:
            print(f"  Result: {result}  Confidence: {confidence:.2f}  "
                  f"Threshold: {threshold}  Fell back: {fell_back}")

        return AiDecision(
            result=result,
            confidence=confidence,
            model=model,   # the model ACTUALLY contacted -- chain/override/default, not
                           # always self._model (pre-existing bug, found alongside Unit 1:
                           # a resolved chain's decision.model still reported the runtime's
                           # own default, undermining the very audit trail this ruling wants)
            inputs=inputs,
            explanation=explanation,
            fell_back=fell_back,
            tokens=res.input_tokens + res.output_tokens,
            cost=_cost_for_tokens(model, res.input_tokens, res.output_tokens),
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

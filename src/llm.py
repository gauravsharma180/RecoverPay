"""The only place a model is allowed to speak.

Rules this module enforces, in code, not in a prompt:

1. The model never decides. It receives a decision that has already been made
   by `policy.py` and turns it into words.
2. Output is validated before use: length cap, no injected URLs, and the rupee
   amount in the copy must match the real amount to the paisa. Any failure
   falls back to a template. A wrong number in a payment message is worse than
   a boring one.
3. No key, no network, no problem. Templates cover every reason code, so the
   whole pipeline runs offline and the metrics never depend on a model call.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .domain import FailureReason, Payment

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MAX_COPY_CHARS = 320
URL_RE = re.compile(r"https?://|www\.", re.I)

# Deterministic Hinglish copy, one per reason. These ship as-is when the model
# is unavailable or its output fails validation.
NUDGE_TEMPLATES: dict[FailureReason, str] = {
    FailureReason.INSUFFICIENT_FUNDS:
        "Hi! Aapka {amount} ka payment balance kam hone ki wajah se fail ho gaya. "
        "Balance add karke dobara try karein.",
    FailureReason.UPI_COLLECT_EXPIRED:
        "Hi! Aapki UPI collect request expire ho gayi thi. Humne {amount} ke liye "
        "nayi request bheji hai, app mein approve kar dein.",
    FailureReason.AUTH_3DS_DROPOFF:
        "Hi! {amount} ka payment bank verification par ruk gaya tha. "
        "Order complete karne ke liye verification poora karein.",
    FailureReason.CARD_EXPIRED:
        "Hi! Aapka card expire ho chuka hai, isliye {amount} ka payment nahi hua. "
        "Naya card add karein ya UPI se pay karein.",
    FailureReason.INVALID_VPA:
        "Hi! Jo UPI ID di gayi thi wo valid nahi hai, {amount} ka payment fail ho gaya. "
        "Sahi UPI ID daal kar dobara try karein.",
    FailureReason.LIMIT_EXCEEDED:
        "Hi! Aapke bank ki daily limit ki wajah se {amount} ka payment fail hua. "
        "Kal try karein ya doosra method use karein.",
    FailureReason.DO_NOT_HONOUR:
        "Hi! Aapke bank ne {amount} ka payment decline kar diya. "
        "Doosre payment method se try karein.",
    FailureReason.ISSUER_DOWN:
        "Hi! Aapka bank abhi temporarily down tha, {amount} ka payment nahi hua. "
        "Thodi der mein dobara try karein.",
    FailureReason.GATEWAY_TIMEOUT:
        "Hi! Technical issue ki wajah se {amount} ka payment complete nahi hua. "
        "Please dobara try karein.",
}

FALLBACK_TEMPLATE = (
    "Hi! Aapka {amount} ka payment complete nahi ho paya. "
    "Please dobara try karein."
)


def _rupees(paise: int) -> str:
    return f"Rs {paise / 100:,.2f}"


def _template_copy(payment: Payment) -> str:
    tpl = NUDGE_TEMPLATES.get(payment.failure_reason, FALLBACK_TEMPLATE)
    return tpl.format(amount=_rupees(payment.amount_paise))


def validate_copy(text: str, payment: Payment) -> bool:
    """Reject anything unsafe to send on a merchant's behalf."""
    if not text or len(text) > MAX_COPY_CHARS:
        return False
    if URL_RE.search(text):
        return False
    # If the copy states an amount, it must be the right one.
    amounts = re.findall(r"(?:Rs\.?|₹)\s?([\d,]+(?:\.\d{1,2})?)", text)
    if amounts:
        want = round(payment.amount_paise / 100, 2)
        for raw in amounts:
            try:
                if abs(float(raw.replace(",", "")) - want) > 0.01:
                    return False
            except ValueError:
                return False
    return True


def _call_gemini(prompt: str, system: str, max_tokens: int) -> str | None:
    """Free-tier default. Key from aistudio.google.com/apikey."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )
    # Current Gemini models reason before they answer, spending part of
    # maxOutputTokens on hidden "thinking" tokens the caller never sees.
    # thinkingBudget is only a hint - on a complex prompt this model has been
    # observed spending 10x its stated budget - so the real fix is a large
    # ceiling, not a small trusted one. Ignored by any model that doesn't
    # support thinking.
    body = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens + 1800,
            "temperature": 0.4,
            "thinkingConfig": {"thinkingBudget": 64},
        },
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip()


def _call_anthropic(prompt: str, system: str, max_tokens: int) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        ANTHROPIC_API_URL, data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    return "".join(b.get("text", "") for b in data.get("content", [])).strip()


PROVIDERS = {
    "gemini": _call_gemini,
    "anthropic": _call_anthropic,
}


def active_provider() -> str:
    return os.environ.get("LLM_PROVIDER", "gemini").strip().lower()


def _call_model(prompt: str, system: str, max_tokens: int = 400) -> str | None:
    provider = PROVIDERS.get(active_provider())
    if provider is None:
        return None
    try:
        return provider(prompt, system, max_tokens)
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError):
        return None


def nudge_copy(payment: Payment, reason_code: str) -> tuple[str, str]:
    """Return (copy, source) where source is 'model' or 'template'."""
    system = (
        "You write one short Hinglish payment recovery message for an Indian "
        "merchant. Plain Latin script. No links, no emoji, no marketing. State "
        "the amount exactly as given. Under 240 characters. Output the message "
        "only."
    )
    prompt = (
        f"Amount: {_rupees(payment.amount_paise)}\n"
        f"Failure: {payment.failure_reason.value}\n"
        f"Action taken by the system: {reason_code}\n"
        "Write the message."
    )
    out = _call_model(prompt, system, max_tokens=200)
    if out and validate_copy(out, payment):
        return out, "model"
    return _template_copy(payment), "template"


def root_cause_brief(summary: dict) -> tuple[str, str]:
    """Merchant-facing narration of what broke in this batch.

    Summarising aggregates is a genuine language task, so the model earns its
    place here. It is handed pre-computed numbers and cannot recompute them.
    """
    system = (
        "You are a payments analyst writing for a merchant ops team. Use only "
        "the numbers given. No speculation, no advice about what the system "
        "should have done. Six sentences maximum, plain prose."
    )
    out = _call_model(json.dumps(summary, indent=2), system, max_tokens=500)
    if out and len(out) < 2000:
        return out, "model"

    top = summary.get("top_reasons", [])[:3]
    parts = [
        f"{summary['total']} failed payments worth "
        f"{_rupees(summary['at_risk_paise'])} entered recovery."
    ]
    if top:
        parts.append(
            "The largest buckets were "
            + ", ".join(f"{r['reason']} ({r['count']})" for r in top)
            + "."
        )
    parts.append(
        f"{summary['recovered']} were recovered for "
        f"{_rupees(summary['recovered_paise'])} using "
        f"{summary['attempts']} charge attempts and {summary['nudges']} messages."
    )
    return " ".join(parts), "template"

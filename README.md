# Vera — magicpin Merchant AI

**Principle: decide deterministically, write with an LLM, verify before anything is sent.**

## Approach
1. **Planner (per trigger kind).** A planner exists for each of 30+ trigger kinds, plus a generic fallback for unseen kinds. It picks only facts that exist in the four contexts. It also chooses the *why-now* angle, the persuasion levers, exactly one CTA (as the last sentence) and the language. Merchants whose languages include `hi` get Hinglish; customers get their own `language_pref` (Roman-script Hindi, Hinglish, or English with a regional greeting). Where the data supports it, the planner adds judgement:
   - Saturday IPL match → push delivery, not a dine-in promo, and keep the Tue–Thu BOGO for weeknights.
   - Seasonal gym dip → focus on retention, not ad spend (≈25 members at risk).
   - New competitor → don't start a price war; lead with the merchant's own 5-star reviews.
   - Refill due → check the batch against the current atorvastatin recall.

   Placeholder triggers (45 of 100 have no payload) build their story from real numbers, such as peer gaps or a CTR at 2.1× the category average. They never invent a slot, price, competitor or duration.
2. **Writer.** Groq `gpt-oss-120b`, falling back to `qwen3.8-27b`, at temperature 0 with a fixed seed and a prompt-hash cache. The rule-based draft is both the LLM's starting point and the fallback.
3. **Validator.** Every number, ₹ price, %, multiplier and duration must be traceable to the facts. It also rejects URLs, banned words, internal jargon and preambles. It checks that there is one CTA and that it comes last, that the language is right, that customer messages name the business, and that the rewrite keeps at least as many concrete figures as the draft. The LLM gets one repair attempt; otherwise the template is used.
4. **Serving.** Messages are written in the background when a context arrives, cached against the version of every input. `/v1/tick` therefore answers in milliseconds, and new context versions automatically trigger a rewrite. On a cache miss, the bot writes live within its time budget, then falls back to the template, so it never times out.
5. **Replies.** Rules come first:
   - **Auto-reply** (canned phrasing, or the same text repeated *across conversations*): one short note flagging it for the owner → wait 24h → end.
   - **Explicit stop:** end and suppress the merchant.
   - **Hostility:** a one-line apology with a STOP option.
   - **Off-topic (GST):** decline politely and return to the open thread.
   - **"Let's do it":** switch to action mode — deliver the draft and ask for a single CONFIRM, never another qualifying question.
   - **Slot pick:** confirm the booking.

   The bot mirrors the merchant's language each turn, never repeats itself, and has a turn cap. The LLM is used only for answers and deliverables, and its output goes through the same validator.
6. **Restraint.** At most one merchant-facing message per merchant per tick, 10 minutes apart. It dedupes on suppression key, checks customer consent, and honours opt-outs and backoffs.

## Tradeoffs
- **Free-tier LLM** (about 8K tokens/min per model). This led to compact fact-sheet prompts and rotation across models, and it's why the templates are strong enough to score well on their own.
- **Grounded beats pretty.** The validator rejects fluent LLM text if it adds any unsupported figure. The case studies' "complimentary fluoride", "₹1,420 total" and "22 affected customers" are *not* in the data, so this bot doesn't say them.
- **In-memory state with a single worker**, as the brief allows. The seed dataset backs lookups but isn't counted in healthz.

## Context that would help most
- Real open slots and a per-service price list (recall, win-back and appointment flows currently have to ask).
- Payloads for the 45 placeholder triggers.
- Customer `services_received` for generated customers.
- Locality-level peer stats, actual review text, and the merchant's approved WhatsApp template names.

## Run
`pip install -r requirements.txt` · `uvicorn bot:app --port 8080` · `pytest` · `python scripts/harness.py` (lifecycle + replay checks) · `python scripts/generate_submission.py` → `submission.jsonl` · `python scripts/eval_judge.py` (local rubric judge). Deployment: see `DEPLOY.md`.

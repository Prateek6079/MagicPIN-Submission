# Deploying Vera for free (Render) + judging-day checklist

This is written for you, the submitter. `README.md` is the one-page write-up for the judges.

## 0. One-time local check (5 min)
```bash
pip install -r requirements.txt
pytest -q                                   # 31 tests, ~1s, no LLM needed
uvicorn bot:app --host 127.0.0.1 --port 8080
python scripts/harness.py                   # in a 2nd terminal: full lifecycle + replay checks → "ALL CHECKS PASSED"
```

## 1. Push the code to GitHub
Render deploys from a Git repo. Create an **empty private repo** on github.com (no README), then:
```bash
git init && git add . && git commit -m "Vera bot"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```
`.env` (which holds your Groq key) is in `.gitignore`, so it is **not** pushed.

## 2. Create the Render service (free)
1. Go to render.com → sign in with GitHub → **New + → Blueprint** → pick the repo. Render reads `render.yaml`
   (free plan, Singapore region, Python 3.12, health check `/v1/healthz`, auto-deploy off).
2. When it asks for `GROQ_API_KEY`, paste your key. Everything else is pre-filled.
3. Wait for "Live". Your URL looks like `https://vera-magicpin-bot.onrender.com`.
4. Check it: open `https://<your-url>/v1/healthz` → `{"status":"ok",...}` and `/v1/metadata`.

*Without a Blueprint:* New + → Web Service → the repo → Runtime Python → Build `pip install -r requirements.txt`
→ Start `uvicorn bot:app --host 0.0.0.0 --port $PORT --workers 1` → Instance type **Free** → add the env vars from `.env.example`.

## 3. Keep it awake (important on the free tier)
Render free services **sleep after 15 minutes with no traffic**. Waking one up takes ~50s, which is longer than the
judge's timeout, and it also wipes in-memory state. Fix this with a free uptime pinger:
- **UptimeRobot** (free): New monitor → HTTP(s) → URL `https://<your-url>/v1/healthz` → interval **5 minutes**.
  (cron-job.org works too.)
- One always-on service is ~744 hours/month, inside Render's 750 free hours. Don't run a second free service on the same account.

Once the judge starts, it polls `/v1/healthz` every 60s itself, so the service stays awake for the whole test.

## 4. Submit
Submit the **base URL only**, e.g. `https://vera-magicpin-bot.onrender.com` (no `/v1`). Also upload `bot.py`,
`submission.jsonl`, `README.md` and `conversation_handlers.py` if the portal asks for files.

## 5. Judging-day rules (these protect your score)
- **Don't redeploy or restart** during the judging window. State lives in memory and a restart wipes it.
  Auto-deploy is off; don't click "Manual Deploy".
- **Save your Groq quota.** The free tier gives ~200K tokens/day per model. Don't run `generate_submission.py` or
  `eval_judge.py` that day. If the quota runs out, the bot still works: it falls back to rule-based templates automatically.
- **15-30 min before:** open `/v1/healthz` once to make sure it's awake and `contexts_loaded` is all zeros.
  (If you tested against it earlier, call `POST /v1/teardown` to wipe test data so the judge's version-1 pushes aren't rejected.)
  ```bash
  curl -X POST https://<your-url>/v1/teardown
  ```
- Optional sanity run against the live URL (it cleans up after itself):
  `python scripts/harness.py --url https://<your-url>` then `curl -X POST https://<your-url>/v1/teardown`.

## 6. If Render free doesn't work out
The same `Dockerfile` runs on other free hosts:
- **Hugging Face Spaces** (Docker Space, free CPU, 16 GB RAM, sleeps only after 48h idle). Set the Space's port to
  8080 (`app_port: 8080` in the Space README) and add `GROQ_API_KEY` as a Secret. The Space must be public for the
  judge to reach it, so the code is publicly visible.
- **Koyeb** free instance: deploy from GitHub with the Dockerfile.

## 6b. Running magicpin's own `judge_simulator.py`
Edit the config block at the top of `challenge/judge_simulator.py`:
```python
BOT_URL = "http://127.0.0.1:8080"     # not "localhost" — on Windows that adds ~2s per call (IPv6 fallback)
LLM_PROVIDER = "groq"
LLM_API_KEY = "<your groq key>"
LLM_MODEL = "openai/gpt-oss-120b"     # the file's default Groq model (llama-3.1-70b-versatile) no longer exists
TEST_SCENARIO = "all"                 # or "phase2_short" to see scored messages
```
Then `cd challenge && python judge_simulator.py`. It uses your Groq quota too.

## 7. Settings you might touch (env vars)
| Var | Default | Why |
|---|---|---|
| `GROQ_API_KEY` | — | required for LLM writing (without it: deterministic templates only) |
| `LLM_MODELS` | `openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b` | rotation order for replies |
| `COMPOSE_MODELS` | `openai/gpt-oss-120b,qwen/qwen3.8-27b` | models allowed to write proactive messages |
| `TICK_BUDGET_SECONDS` / `REPLY_BUDGET_SECONDS` | 8 / 8 | hard latency budgets (judge allows 30s; examples say 10s) |
| `CONTACT_EMAIL` | empty | set it if you want it shown in `/v1/metadata` |
| `LLM_ENABLED` | true | `false` = fully deterministic |

## 8. Security
Your Groq key was pasted into a chat session, so **rotate it after the challenge** (console.groq.com → API Keys).
Only the Render dashboard and your local `.env` should hold it.

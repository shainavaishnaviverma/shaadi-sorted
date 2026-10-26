# Shaadi Sorted

An AI agent app that plans a whole Indian wedding. Pick one tradition and it builds a standard plan. Pick two (for example a Tamil Hindu family and a Punjabi Sikh family) and it merges both sets of customs into one timeline and flags where they clash on food, dress, timing and who pays.

You get: a list of functions with dates, a dated checklist, a budget split, a "Next step today" card, a guest/RSVP table with one column per function, and a chat assistant that edits the plan through tools ("Add the Sharma family, 4 people, groom side, for the Sangeet").

Model: `gpt-6-luna` through Portkey (`PORTKEY_API_KEY`). Nothing else is used.

## Run it (about 3 minutes)

You need **Python 3.10 or newer** and an internet connection for the first install.

1. **Clone and enter the folder**

   ```
   git clone <this repo's URL>
   cd shaadi-sorted
   ```

2. **Add your key.** Copy the example file and paste your Portkey key into it:

   ```
   cp .env.example .env        # Windows: copy .env.example .env
   ```

   Open `.env` and set `PORTKEY_API_KEY=your-key`. The app reads `.env` from **its own folder first, then from the folder one level above** (so a shared `.env` next to the cloned repo also works). Empty values are ignored. `OPENAI_API_KEY` is optional and not required.

3. **Start the app (one command)**

   ```
   python run.py
   ```

   (Use `python3 run.py` if `python` is not found.) The first run creates a `.venv` and installs the 5 dependencies, then opens http://localhost:8000. If the browser does not open, visit that address yourself. Set `PORT=8010` first if port 8000 is busy.

4. **Try it**
   - Click **Instant demo (no AI)**. It loads the sample couple and 12 guests from `data/` and uses zero tokens, so you can look around the whole app right away.
   - Then click **Start over**, choose **Fill in a sample couple**, and press **Create my plan with AI**. The Planner agent picks the functions and, because the sample has two traditions, the Merge agent builds the combined timeline and clashes.
   - Open the chat panel and type: `Add the Sharma family, 4 people, groom side, for the Sangeet`. Watch the Guests tab update.

5. **See the token meter.** The **Tokens** badge in the top bar shows input, cached and output tokens per agent for everything you have run. Every call is also appended to `data/usage_log.jsonl`.

If there is no key, the banner says so; the instant demo and every non-AI screen still work.

## Safety defenses (and how to see them work)

Run the safety tests (no key needed). They play the part of a model that has been tricked into misbehaving:

```
.venv/bin/python -m tests.test_injection        # Windows: .venv\Scripts\python -m tests.test_injection
```

What they show: guest names containing instructions are blocked; the model can never delete a guest (removal only creates a pending request that you confirm with a button in the UI); a flood of 8 deletion calls in one turn is cut off at 5 and none execute; any reply that states an auspicious date is rewritten to "ask your pandit/imam/priest/granthi/monk"; links in invite drafts are rejected; a token cap stops runaway spending. Add `--live` to also send three real attack messages to the model (uses a few thousand tokens). In the app, the **Safety** badge lists every defense that fires.

Other checks: `.venv/bin/python -m tests.test_plan` (dates, budget sums to 100%, single and dual plans, input validation).

## How it works

```
Intake -> Planner agent -> (two traditions? -> Tradition-Merge agent) -> code adds dates, deadlines, budget -> plan
                                                                       Guest+Budget agent (chat) edits the plan via tools
```

- **Planner agent** picks and orders functions from `data/traditions.json` (religion plus optional regional variant layered on top).
- **Tradition-Merge agent** runs only for two traditions: one combined timeline plus clash flags with a one-line rationale each.
- **Guest+Budget agent** (chat) calls tools: `add_guest`, `update_rsvp`, `set_rsvp`, `remove_guest`, `update_budget`, `complete_task`, `get_next_step`, `draft_invite_message` (returns text and a `wa.me` link; nothing is ever sent).
- Dates, checklist deadlines and budget math are plain code, not the model. If the model fails, deterministic fallbacks still produce a plan.

## Files

```
run.py                 one-command bootstrap and server
app/                   config.py (.env loading), llm.py (Portkey + token meter), planner.py, agent.py, tools.py, safety.py, store.py, main.py
data/                  traditions.json, budget_templates.json, sample_intake.json, sample_guests.json (test dataset)
static/                index.html, style.css, app.js
tests/                 test_plan.py, test_injection.py
```

## Notes and limits

- Customs vary by family and community. `traditions.json` marks lower-confidence details and names who to confirm with (pandit, priest, imam, granthi, monk). The app never states auspicious dates.
- Cost figures in the Tokens badge use assumed per-million-token rates in `app/config.py`; edit them to match your Portkey pricing. Token counts come straight from the API.
- State is stored in `data/state.json` (git-ignored). The app is single-user with no sign-in. No vendors, no real WhatsApp sending.
- Troubleshooting: "PORTKEY_API_KEY is not set" means `.env` was not found in the repo folder or the folder above it. A failed install usually means Python is older than 3.10.

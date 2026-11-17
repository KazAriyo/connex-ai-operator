# ConneX AI Operator: a small, working version

A chat assistant for an estate agency CRM with the two modes of the real AI Operator:

- **Ask** (read-only) looks things up and answers in plain language.
- **Run a task** (writes) proposes a change, shows you exactly what it will do, and only runs it after you say yes.

It is a real chat interface backed by a real language model. Seven typed tools, each with a declared input and output schema, run against an invented dataset of a fictional London agency: 10 leads, 5 tasks and 7 calendar events.

```
You: How many leads came in this week?
  [calls get_insights(period="this_week")]
AI Operator: 3 new leads this week, 2 are unassigned.

You: Create a task to call Aisha Khan on Friday
  [proposes create_task(title="Call Aisha Khan", due="friday", related_to="Aisha Khan") · waiting for your confirmation]
AI Operator: I'll do this: Create task "Call Aisha Khan", due Fri 20 Nov 2026, linked to Aisha Khan (L-001). Shall I go ahead? (yes / no)
You: Yes
  [calls create_task(title="Call Aisha Khan", due="friday", related_to="Aisha Khan")]
  ✓ done: Create task "Call Aisha Khan", due Fri 20 Nov 2026, linked to Aisha Khan (L-001).
AI Operator: Done, the task has been created.
```

---

## Run it

You need Python 3.10 or newer and one API key: **Groq (free)**, Anthropic or OpenAI.

```bash
git clone <this repo> && cd connex-ai-operator
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then paste your API key into .env
```

**Terminal chat:**

```bash
python cli.py                # Run a task mode (reads, plus writes that you confirm)
python cli.py --mode ask     # read-only
python cli.py --reset -v     # fresh mock data; -v also prints raw tool results
```

Commands inside the chat: `/ask`, `/run`, `/data` (prints the current CRM), `/reset`, `/quit`.

**Web UI:**

```bash
uvicorn web.app:app --reload
# open http://127.0.0.1:8000
```

The web UI has an Ask / Run a task switch, Confirm and Cancel buttons for writes, and a live panel showing leads, tasks and calendar. You can watch a record change after you confirm.

**Live check against the real model.** This runs the brief's own test conversation, plus the safety cases, against your configured model, and prints PASS or FAIL for each. It uses a temporary copy of the data and costs a few pence.

```bash
python live_check.py
```

It checks that:
- the Ask question uses a read tool only and reports 3 new leads, 2 unassigned;
- `create_task` waits for confirmation and saves nothing until "Yes", then saves the right title, date and lead;
- "no" cancels a status change;
- Ask mode never offers a write;
- a person who isn't in the CRM is reported, not invented.

It also prints the reply to a question the CRM can't answer, for you to read.

**Tests** run offline with a scripted model, so they need no API key:

```bash
pytest -q        # 74 tests
```

### Things to try

| Mode | Request | What should happen |
|---|---|---|
| Ask | How many leads came in this week? | `get_insights` returns 3 new leads, 2 unassigned |
| Ask | Find leads I haven't contacted this week | `search_leads(not_contacted_since="start of week")` |
| Ask | What's on tomorrow? | `get_calendar(range="tomorrow")` returns 2 events |
| Ask | Which tasks are overdue? | `list_tasks` or `get_insights` |
| Run | Create a task to call Aisha Khan on Friday | Confirmation, then the task is created on yes |
| Run | Mark Tom Fletcher as contacted | Confirmation; on yes, status and last-contacted date update |
| Run | Say **no**, or "actually make it Thursday" | Nothing is written; the model re-proposes |
| Ask | Create a task… (while in Ask mode) | Refuses and suggests switching mode |
| Either | What's Aisha's mortgage lender? | Says the CRM doesn't hold that and does not invent it |
| Run | Create a task to call Bob Nobody | Says no such lead exists |

The mock data is generated relative to today's date and refreshed automatically on a new day, so "this week" and "tomorrow" always have something in them. Use `--reset` or the **Reset demo** button to start fresh at any time.

---

## Model and provider

Three providers are supported. The app uses whichever key you set.

| Provider | Pattern | Default model | Cost |
|---|---|---|---|
| **Groq** | OpenAI-style function calling | `openai/gpt-oss-120b` | Free plan, no card needed |
| **Anthropic** | Native Claude tool use | `claude-sonnet-5-5` | Paid |
| **OpenAI** | Function calling | `gpt-4.1-mini` | Paid |

**Free key from Groq:** sign up at [console.groq.com](https://console.groq.com), open **API Keys**, click **Create API Key**, and paste the key into `.env` as `GROQ_API_KEY=...`. The free plan is rate limited (roughly 30 requests and 8,000 tokens per minute). The app waits and retries automatically when it hits the limit, so a reply can occasionally take a little longer.


All three sit behind one small interface in `ai_operator/providers.py`. The pattern is the same for each: the model proposes a typed call, our code validates and executes it, and the result goes back to the model. Switching vendor changes nothing else in the app.

Settings in `.env` (leave any you don't need empty):

| Setting | Purpose |
|---|---|
| `GROQ_API_KEY` | Free key from Groq. |
| `ANTHROPIC_API_KEY` | Key for Anthropic. |
| `OPENAI_API_KEY` | Key for OpenAI. |
| `OPERATOR_PROVIDER` | `groq`, `anthropic` or `openai`. Optional; if empty, the first key found is used (Anthropic, then OpenAI, then Groq). |
| `OPERATOR_MODEL` | Model name override, e.g. `openai/gpt-oss-20b`, `claude-haiku-5-5` or `gpt-4.1-mini`. |
| `OPERATOR_TODAY` | Optional `YYYY-MM-DD` to pin "today" for a repeatable demo. |


---

## The tools

Every tool is a typed function with a Pydantic **input** model and a Pydantic **output** model.
- **Input:** the JSON schema the model sees is generated from the input model, and the same model validates whatever the model sends back. Unknown or extra arguments are rejected.
- **Output:** every result is checked against the tool's output model before it goes back to the model. A tool that returned the wrong shape would fail loudly rather than feed the model something unexpected.

| Tool | Mode | What it does |
|---|---|---|
| `search_leads(query, status, unassigned_only, not_contacted_since, created_since, include_closed, limit)` | Ask | Keyword and filter search over leads. Won and lost leads are hidden unless asked for. |
| `get_insights(period)` | Ask | Computed stats: new leads in the period and how many are unassigned, pipeline by status, active leads not contacted in 7 days, open and overdue tasks, events in the next 7 days |
| `get_calendar(range \| start, end)` | Ask | Viewings, valuations, meetings and calls in a date range |
| `list_tasks(status, related_to)` | Ask | Tasks with overdue flags, optionally for one lead |
| `create_task(title, due, related_to)` | Run a task | Creates a task. **Confirmed first.** |
| `update_lead_status(lead_id, status)` | Run a task | Changes a lead's status. Setting it to `contacted` also stamps last-contacted, and the confirmation says so. **Confirmed first.** |
| `complete_task(task_id)` | Run a task | Marks a task done. **Confirmed first.** |

The brief asked for one Ask tool and one Run a task tool. All five from the brief are here, plus `list_tasks` and `complete_task` to cover "create, update, or get a summary" of tasks.

---

## How it's built: "the model talks, the server operates"

```
user ──▶ model (sees tool schemas only, never the data)
            │ proposes: create_task({"title": "...", "due": "friday", ...})
            ▼
        our code: is this a real tool? are the arguments valid? is it allowed in this mode?
            │
            ├─ read tool  ──▶ run it ──▶ result back to the model ──▶ plain-language answer
            │
            └─ write tool ──▶ prepare it (resolve the date, find the lead, build an exact summary)
                              ──▶ PAUSE and ask the user ──▶ "yes" ──▶ run it ──▶ result back to the model
                                                         └─ anything else ──▶ nothing changes; the model is told it was cancelled
```

Each of the brief's four rules is enforced in code, not left to the prompt.

**1. The model talks, your code operates.** The model never touches `data/crm.json`. It produces a tool name and some JSON, and that is all. `ai_operator/tools.py` decides whether the call is valid and what happens. Date arithmetic is code too: the model passes "friday" and `clock.py` resolves it to Fri 20 Nov 2026. Models are bad at counting days; code isn't.

**2. Confirm before you act.** The gate is in `agent.py`. When the model calls a write tool, the agent does not run it. It calls the tool's `prepare` step, which validates and resolves everything but changes nothing, then stops and shows the user a code-generated summary. The write runs only if the user's next message is an explicit yes. Anything else counts as a no ("default deny"), and the model is told the action was cancelled. This holds even when:
- the model forgets to ask, or claims "Done!" in the same breath (there's a test for this);
- the model proposes several writes at once (each one is confirmed separately);
- the record changes while waiting for confirmation (the write is refused rather than applied to stale data).

The summary is generated by code from the validated arguments, not written by the model. What you approve is exactly what runs.

**Ask mode is enforced twice.** Write tools are never offered to the model in Ask mode, and if one is called anyway, the agent refuses it.

**3. Never invent what you don't have.** Tools return explicit "not found" or "no matches" results. Ambiguous names (two leads matching "Aisha") come back as a question for the user rather than a guess. The system prompt tells the model to state only what tool results contain. Most importantly, the model has no way to write a made-up lead into the CRM: everything goes through validation and confirmation.

**4. Fail loud, not silent.** A missing key, a wrong key, an unknown model, a rate limit or no network each produce a specific error message, never a stack trace or a hang. Invalid arguments, unknown tools and past due dates go back to the model as errors it must explain. There's a step limit so a confused model can't loop forever. If the very first model call fails, the message is dropped so the user can retry.

### Why this matters

A language model is a decision-maker that is usually right and occasionally confidently wrong. If it can execute directly, one bad guess becomes a deleted lead or a task on the wrong date, with nothing in between to catch it. Splitting "decide" (model) from "do" (code) means every action passes through checks a person wrote and can test, and every change is something the user explicitly agreed to. That is what makes it safe to let an assistant act inside a business's real CRM.

---

## Project layout

```
ai_operator/
  agent.py      the loop and the confirm-before-write gate
  tools.py      typed tools: schemas, validation, read and write logic
  providers.py  Anthropic, OpenAI and Groq adapters
  prompts.py    system prompt (date, mode, grounding rules)
  clock.py      date expressions resolved by code
  store.py      JSON file store (atomic writes)
  seed.py       invented mock data, dated relative to today
cli.py          terminal chat
web/            FastAPI app and a single-page UI
live_check.py   runs the brief's test conversation against the real model
tests/          74 offline tests (tools, agent loop, providers, web API, live check) and a scripted model for them
data/crm.json   created on first run (git-ignored)
```

All data is invented. Names are fictional, emails use `example.com`, and phone numbers use Ofcom's reserved drama range (07700 900xxx). No ConneX, client or portal data is used.

---

## What I'd build next in a real sprint

1. **Real backend behind the same tools.** Swap `CRMStore` for the ConneX API. The tool contracts, the confirmation gate and the tests stay the same.
2. **Permissions.** Scope each tool by the user's role (admin, manager, agent, viewer) and branch, checked in code before `prepare`. Viewers would only ever get Ask tools.
3. **Audit log.** Record every proposed, confirmed, cancelled and executed action with who, when and before/after values. The `executed` and `cancelled` events already exist, so this is mostly storage.
4. **More tools.** Assign lead, reschedule or book a viewing (with calendar clash checks), log a call note, draft a WhatsApp follow-up (draft only, confirmed before sending).
5. **Undo.** A short window to reverse the last confirmed write.
6. **Voice input.** Speech-to-text into the same `Operator.send()`. Confirmation would need a spoken read-back plus an explicit yes.
7. **Evaluation set.** Fifty or so scripted conversations run against the real model on every prompt or model change, checking for invented facts and unconfirmed writes.
8. **Streaming replies, a prompt library, and LangGraph** if the flows grow into multi-step plans. The confirm step maps directly onto a LangGraph interrupt.

# 00 · Product Positioning & Market Judgment

## The original question

User's first prompt (2026-06-06 conversation):

> How do I build a local coding agent "boss dashboard" — see the execution state and output of different agents in real time, plus the topology between sessions. I need a very complete data structure abstraction so I can integrate different types of agents.

Three keywords: **local**, **boss view** (overlooking multiple agents), **topology** (parent/child relationships between sessions).

## This is not an extension of Pixie — it's the layer missing UNDER Pixie

The user was already running `~/sideproject/electron-sprite` (Pixie / 像素灵) — an "AI usage meta-layer" that gamifies AI sessions with XP / class / cost.

During the conversation I first suggested "add a boss-board feature inside Pixie", but the more we talked the clearer it got:

- Pixie does **post-hoc statistics** (results)
- The boss dashboard wants **process observation** (the engine)

These **are not the same species**. Pixie already does ccusage-style token collection, but only for Hermes / parts of Claude Code. The real scarce thing is "**a daemon that pulls every agent's runtime events into one unified stream**".

**Pixie doesn't die. Pixie should become the first gamified consumer of this lower layer.**

## The actual market gap

Similar tools in the industry:

| Product | Form | Audience |
|---|---|---|
| LangSmith / Langfuse / Helicone / Arize | Cloud SaaS | Enterprises shipping agents inside their products |
| Hermes Dashboard (built-in) | local web | Only Hermes's own sessions |
| Claude Code's own TUI | CLI | The current session of one tool |
| Hermes Achievements plugin | local web | Gamified stats over Hermes sessions |

**Nobody is doing**:

- ✅ Local-first (data never leaves the machine)
- ✅ For individual power users (not enterprise deployments)
- ✅ Passive collection from logs/DB (no agent code-change for instrumentation)
- ✅ Multi-source (Claude / Codex / Hermes / OpenCode / omp simultaneously)

These three axes run **opposite** to every existing commercial player — which is exactly why nobody is doing it (the commercial curve doesn't match enterprise SaaS) and exactly why it's valuable to an individual (you see the panorama of your whole AI workflow, not a partial view sold by one vendor).

## How market judgment evolved (honest record of my mid-flight mistake)

I blurted out "the business model doesn't work" during the conversation. **That was wrong**, and I retracted it. Re-evaluation:

**Niche but the right category.** Don't benchmark against LangSmith (enterprise SaaS) — benchmark against **Raycast / Obsidian / Logseq / Linear / Cursor** — developer-focused personal paid tools.

Rough sizing: price at $15–30/month, target 10k users = ~$1M ARR. That's enough to fund one person + sustain a decade of investment, which matches the user's stated strategy ("growth + passive income, don't all-in on a startup").

## More than one use case beyond "boss dashboard"

Once this lower layer exists, you can grow:

1. **Dashboard** — manage multiple agents live (current MVP)
2. **Replay** — git-replay for any past agent run; see what it was thinking at each step. Debug agent behavior, teach yourself to write better prompts
3. **Behavior pattern analysis** — "your high-output sessions with Claude average X Bash calls and Y Reads; low-output sessions are the opposite"
4. **Cross-agent working memory** — when agent B starts, automatically inject "agent A already touched these files"
5. **AI work-hours log** — objectively records "what AI actually did for me today" — this is the true answer to "how do you define AI's value output": **how many verifiable behavior changes did it produce** (N files edited, M tests passed, K PRs merged), not token count
6. **Personal RLHF data source** — every time you accept / reject / rewrite an agent's output, that signal is gold in the event stream
7. **Agent router** — "you sent this type of task to Claude 87% successfully and Codex 62%; auto-route to Claude"

## Naming candidates

Discussed during the conversation:

- **Witness** ✅ The current spike name. Passive observer, has soul
- **Loop** — agent loop observer, technically friendly
- **Pulse / Heartbeat** — emphasizes real-time
- **AgentLog** — honest but accurate

The user picked **agent-master** as the repo name, keeping the "boss / master" view metaphor. Internal components can still be called witness.

## On open-sourcing

If we really believe this is decade-scale infrastructure, **the collection layer + event bus + AgentEvent spec should be open**. Pixie and future Coach / Replay can be closed-source products.

References: Tesla opened a pile of patents, NVIDIA CUDA became the de-facto standard. Infrastructure only becomes infrastructure when open.

But park this decision — **prove it can run and find users first**, then think about open-sourcing.

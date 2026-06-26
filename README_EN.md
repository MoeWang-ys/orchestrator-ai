**English** | [中文](README.md)

# Worker-Checker · AI Quality Assurance

> Split tasks into Worker-Checker pairs — Workers build, Checkers blind-review, judge.py decides. Parallel execution, file handoffs, three-layer anti-cheat.
> File-based handoffs between Workers. loop.py as pure-code scheduler. Three-layer anti-cheat.

> "They just don't work. It's slop." — **Andrej Karpathy**, OpenAI co-founder.
>
> "AI gets you 70% of the way. The last 30% is just as hard." — **Addy Osmani**, Google Chrome Engineering Director.

**The problem isn't model capability. It's that nobody checks the output.**

![Orchestrator Pitch](pitch_en.gif)

## v2.0 vs v1.0

| Dimension | v1.0 | v2.0 |
|-----------|------|------|
| Worker granularity | One Worker for everything | Multiple minimal Couples |
| Scheduling | Orch schedules directly | loop.py generates instructions → Orch executes mechanically |
| Task decomposition | Orch decomposes | PM Couple decomposes |
| Parallelism | Not supported | Same-layer Couples run in parallel |
| File handoff | Not required | Enforced file-path handoff |
| Orch permissions | Full access | Only translate + relay + present |
| Anti-cheat | Basic | Three-layer defense (narrative + structural + verification) |
| Multi-platform | Claude Code only | CodeBuddy / Claude Code / Codex CLI / Generic |

## Core Philosophy

> One Worker does one kind of thing, uses one capability, communicates only via files.

```
User → Orch (Messenger) → loop.py (Architect) → Parallel Couples
                                                   ├── Couple A: Prod Worker → Check Worker → judge.py
                                                   ├── Couple B: Prod Worker → Check Worker → judge.py
                                                   └── Couple C: Prod Worker → Check Worker → judge.py
```

**The Messenger's Compact**: Orch is not "restricted" — it has willingly accepted a contract. Cannot create. Cannot judge. Cannot plan. Only deliver.

## Multi-Platform Support

The skill auto-detects your platform on load:

| Platform | Status |
|----------|--------|
| CodeBuddy | ✅ Ready |
| Claude Code | ✅ Ready |
| OpenAI Codex CLI | ✅ Ready |
| Other / Unknown | 🌱 Self-growing (Orch probes tools and maps dynamically) |

## Installation (4 ways, pick one)

### Method 1: npx skills (Recommended)

Works on all 68+ Agent Skills-compatible AI coding assistants (Claude Code / CodeBuddy / Codex / Cursor etc.):

```bash
npx skills add Gavin9902/orchestrator-ai
```

Install specific platform versions (optional):

```bash
# CodeBuddy only
npx skills add Gavin9902/orchestrator-ai --skill worker-checker

# Claude Code only
npx skills add Gavin9902/orchestrator-ai --skill worker-checker-claude
```

> `npx skills` is the open-source skill manager by [Vercel Labs](https://github.com/vercel-labs/skills). It auto-downloads if not already installed.

### Method 2: CodeBuddy SkillHub Marketplace

1. Open CodeBuddy → "Skill Marketplace" on the left
2. Search for **`worker-checker`**
3. Click "Add" to install

Or visit [skillhub.cn](https://skillhub.cn) to install directly.

### Method 3: Natural Language

In any AI coding assistant, type:

```
Install this skill: https://github.com/Gavin9902/orchestrator-ai
```

The AI will auto-fetch and install it locally.

### Method 4: Manual Install

```bash
git clone https://github.com/Gavin9902/orchestrator-ai.git
cp -r orchestrator-ai ~/.codebuddy/skills/worker-checker/
```

---

## Quick Start

**1. Summon**

In your AI assistant:

```
/worker-checker
```

Or trigger words: `worker-checker`, `worker-couple`, `parallel Worker`

On first load, it will auto-ask which platform you're on.

**2. Talk through requirements**

Orch guides you through clarifying what needs to be done. PM Couple auto-decomposes the task graph.

**3. Wait**

loop.py schedules parallel Couples. Check progress anytime.

**4. Approve**

All Couples pass judge.py review → Orch presents results. Nothing is delivered until you confirm.

## Architecture Docs

| Doc | Content |
|-----|---------|
| `core/ARCHITECTURE.md` | Role model, anti-cheat system, Worker splitting principles |
| `core/PROTOCOLS.md` | Data formats, Action types, state machine, loop.py interface |
| `codebuddy/SKILL.md` | CodeBuddy platform-specific version |
| `claude-code/SKILL.md` | Claude Code platform-specific version |
| `codex/SKILL.md` | Codex CLI platform-specific version |
| `generic/SKILL.md` | Generic version (self-growing) |

## Three-Layer Anti-Cheat

```
🪄 Layer 1 · Narrative — Messenger's Compact + Five Breaths + Urge Protocol
🔒 Layer 2 · Structural — File handoff + Context isolation + Mutual unawareness
🔐 Layer 3 · Verification — action_hash + orch_receipt + checksum + .lock
```

Covers 20 cheat paths. See `core/ARCHITECTURE.md` for details.

## v1.0 Archive

v1.0 (original orchestrator) is preserved in the `v1/` directory and remains usable.

## License

MIT

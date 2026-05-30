# Moltask Agent Bot

Dependency-free Python bot for agents earning MOLT on Moltask.

It can:

- poll `https://www.moltask.com/api/tasks`
- score tasks by capability, bounty size, deadline, and social-dependency risk
- rank tasks an autonomous agent can complete
- submit finished work with a wallet address
- maintain a local JSON ledger of submitted tasks
- check profile and gas-station endpoints

## Setup

```bash
python moltask_bot.py rank
```

Optional environment:

```bash
export MOLTASK_WALLET=0xYourDedicatedAgentWallet
export MOLTASK_LEDGER=moltask_ledger.json
```

## Rank open tasks

```bash
python moltask_bot.py rank --skills automation,coding,research --min-bounty 1000
```

The scorer rewards tasks that match the agent's capabilities and penalizes social or viral tasks so the agent avoids work that needs social accounts.

## Monitor for work

```bash
python moltask_bot.py monitor --interval 60 --cycles 10
```

## Submit completed work

```bash
python moltask_bot.py submit TASK_ID \
  --wallet 0xYourDedicatedAgentWallet \
  --message-file submission.md \
  --link-url https://github.com/you/moltask-agent-bot \
  --link-type github
```

Each submission is appended to `moltask_ledger.json`.

## Design notes

Moltask's public API exposes work submission but not a separate claim endpoint. This bot treats "claim" as the local selection/ranking step and submits only when a deliverable is complete. That keeps behavior aligned with the current API while making it easy to add a true claim call if Moltask exposes one later.

The bot uses only the Python standard library so it works inside constrained agent runtimes.

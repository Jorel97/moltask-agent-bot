#!/usr/bin/env python3
"""A small Moltask task bot for autonomous agents.

The bot intentionally has no third-party dependencies. It can list open asks,
score tasks by agent capability, prepare claim candidates, submit completed
work, and keep a local ledger of submissions/earnings attempts.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


API_BASE = os.environ.get("MOLTASK_API_BASE", "https://www.moltask.com/api")
DEFAULT_LEDGER = Path(os.environ.get("MOLTASK_LEDGER", "moltask_ledger.json"))


@dataclasses.dataclass
class TaskScore:
    task: dict[str, Any]
    score: int
    reasons: list[str]


def request_json(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {"Accept": "application/json", "User-Agent": "moltask-agent-bot/1.0"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return json.loads(text) if text else None
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(text)
        except json.JSONDecodeError:
            detail = text
        raise RuntimeError(f"HTTP {exc.code} for {path}: {detail}") from exc


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"created_at": now_iso(), "submissions": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    ledger["updated_at"] = now_iso()
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_tasks(status: str | None = None) -> list[dict[str, Any]]:
    query = f"?status={urllib.parse.quote(status)}" if status else ""
    data = request_json("GET", f"/tasks{query}")
    return data.get("tasks", []) if isinstance(data, dict) else []


def task_text(task: dict[str, Any]) -> str:
    parts = [
        str(task.get("title") or ""),
        str(task.get("description") or ""),
        str(task.get("category") or ""),
        " ".join(map(str, task.get("requirements") or [])),
        " ".join(map(str, task.get("deliverables") or [])),
    ]
    return "\n".join(parts).lower()


def score_task(task: dict[str, Any], skills: set[str], min_bounty: float) -> TaskScore:
    text = task_text(task)
    reasons: list[str] = []
    score = 0

    try:
        bounty = float(task.get("bounty_amount") or 0)
    except (TypeError, ValueError):
        bounty = 0

    if bounty >= min_bounty:
        score += 20
        reasons.append(f"bounty >= {min_bounty:g}")

    category = str(task.get("category") or "").lower()
    if category in skills:
        score += 25
        reasons.append(f"category match: {category}")

    for skill in sorted(skills):
        if re.search(rf"\b{re.escape(skill)}\b", text):
            score += 10
            reasons.append(f"text mentions {skill}")

    if "github" in text or "code" in text or "working code" in text:
        score += 15
        reasons.append("code/repo deliverable")

    if "social" in text or "viral" in text or "moltbook" in text:
        score -= 30
        reasons.append("social/viral dependency")

    if task.get("deadline"):
        try:
            deadline = dt.datetime.fromisoformat(str(task["deadline"]).replace("Z", "+00:00"))
            hours_left = (deadline - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600
            if hours_left < 0:
                score -= 100
                reasons.append("deadline expired")
            elif hours_left < 12:
                score -= 10
                reasons.append("short deadline")
        except ValueError:
            reasons.append("unparsed deadline")

    return TaskScore(task=task, score=score, reasons=reasons)


def top_tasks(args: argparse.Namespace) -> None:
    skills = {s.strip().lower() for s in args.skills.split(",") if s.strip()}
    tasks = list_tasks(status=args.status)
    scored = [score_task(task, skills, args.min_bounty) for task in tasks]
    scored.sort(key=lambda item: item.score, reverse=True)
    for item in scored[: args.limit]:
        task = item.task
        print(
            json.dumps(
                {
                    "id": task.get("id"),
                    "title": task.get("title"),
                    "category": task.get("category"),
                    "bounty_amount": task.get("bounty_amount"),
                    "score": item.score,
                    "reasons": item.reasons,
                },
                ensure_ascii=False,
            )
        )


def monitor(args: argparse.Namespace) -> None:
    seen: set[str] = set()
    skills = {s.strip().lower() for s in args.skills.split(",") if s.strip()}
    for _ in range(args.cycles):
        tasks = list_tasks(status=args.status)
        fresh = [task for task in tasks if task.get("id") not in seen]
        for task in tasks:
            if task.get("id"):
                seen.add(str(task["id"]))
        ranked = sorted(
            (score_task(task, skills, args.min_bounty) for task in fresh),
            key=lambda item: item.score,
            reverse=True,
        )
        if ranked:
            print(f"[{now_iso()}] {len(ranked)} new tasks")
            for item in ranked[: args.limit]:
                print(
                    json.dumps(
                        {
                            "id": item.task.get("id"),
                            "title": item.task.get("title"),
                            "score": item.score,
                            "reasons": item.reasons,
                        },
                        ensure_ascii=False,
                    )
                )
        time.sleep(args.interval)


def submit(args: argparse.Namespace) -> None:
    wallet = args.wallet or os.environ.get("MOLTASK_WALLET")
    if not wallet:
        raise SystemExit("wallet is required via --wallet or MOLTASK_WALLET")
    if not args.message and not args.message_file:
        raise SystemExit("provide --message or --message-file")

    message = args.message or Path(args.message_file).read_text(encoding="utf-8")
    payload = {
        "worker_address": wallet,
        "message": message,
        "link_url": args.link_url,
        "link_type": args.link_type,
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    response = request_json("POST", f"/tasks/{args.task_id}/submit", payload)

    ledger_path = Path(args.ledger)
    ledger = load_ledger(ledger_path)
    ledger.setdefault("submissions", []).append(
        {
            "task_id": args.task_id,
            "wallet": wallet,
            "submitted_at": now_iso(),
            "link_url": args.link_url,
            "link_type": args.link_type,
            "response": response,
        }
    )
    save_ledger(ledger_path, ledger)
    print(json.dumps(response, indent=2, ensure_ascii=False))


def profile(args: argparse.Namespace) -> None:
    wallet = args.wallet or os.environ.get("MOLTASK_WALLET")
    if not wallet:
        raise SystemExit("wallet is required via --wallet or MOLTASK_WALLET")
    data = request_json("GET", f"/profile?address={urllib.parse.quote(wallet)}")
    print(json.dumps(data, indent=2, ensure_ascii=False))


def gas(args: argparse.Namespace) -> None:
    wallet = args.wallet or os.environ.get("MOLTASK_WALLET")
    if not wallet:
        raise SystemExit("wallet is required via --wallet or MOLTASK_WALLET")
    if args.request:
        data = request_json("POST", "/gas-station", {"address": wallet})
    else:
        data = request_json("GET", "/gas-station")
    print(json.dumps(data, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Moltask agent task bot")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("rank", help="list and score tasks")
    p.add_argument("--skills", default="automation,coding,research,writing,data,testing")
    p.add_argument("--status", default="open")
    p.add_argument("--min-bounty", type=float, default=1000)
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=top_tasks)

    p = sub.add_parser("monitor", help="poll for new tasks")
    p.add_argument("--skills", default="automation,coding,research,writing,data,testing")
    p.add_argument("--status", default="open")
    p.add_argument("--min-bounty", type=float, default=1000)
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--cycles", type=int, default=5)
    p.set_defaults(func=monitor)

    p = sub.add_parser("submit", help="submit completed work")
    p.add_argument("task_id")
    p.add_argument("--wallet")
    p.add_argument("--message")
    p.add_argument("--message-file")
    p.add_argument("--link-url")
    p.add_argument("--link-type", default="github")
    p.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    p.set_defaults(func=submit)

    p = sub.add_parser("profile", help="show Moltask profile/reputation")
    p.add_argument("--wallet")
    p.set_defaults(func=profile)

    p = sub.add_parser("gas", help="check/request gas-station support")
    p.add_argument("--wallet")
    p.add_argument("--request", action="store_true")
    p.set_defaults(func=gas)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

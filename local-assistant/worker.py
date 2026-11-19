"""Opt-in, single-worker local inference queue. Standard library only.

Run `submit --input task.txt`, then `work`; results stay in the state directory.
Model output is data. This program never executes returned code or tool calls.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import multiprocessing as mp
import os
from pathlib import Path
import sqlite3
import time
import urllib.request
from urllib.parse import urlsplit
import uuid

SYSTEM = ("You are a local assistant working on a bounded subtask. Follow the requested output format. "
          "Use only the supplied evidence for factual extraction. State missing information explicitly. "
          "Preserve names, identifiers, dates, quantities, units, and which model and runtime each measurement belongs to. "
          "Separate the current working setup and verified results from proposed next steps. "
          "A component check does not establish that a combined workflow works. "
          "Do not invent completed actions, sources, measurements, or test results. "
          "Return the answer in the user's language. Be concise.")


def endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "::1", "localhost")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path.rstrip("/") != "/v1"):
        raise ValueError("Use a local HTTP endpoint ending in /v1, without credentials or query parameters")
    return value.rstrip("/")


def write_json(path: Path, data: dict):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def database(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "jobs.sqlite", timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, created REAL, updated REAL, status TEXT,
        prompt TEXT, max_tokens INTEGER, deadline REAL, reasoning TEXT, error TEXT)""")
    db.commit()
    try:
        with db:
            yield db
    finally:
        db.close()


@contextmanager
def worker_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "worker.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("A worker already owns this queue") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def enqueue(root: Path, prompt: str, max_tokens: int, deadline: float, reasoning: str) -> str:
    # A conservative byte cap, not a claim about the tokenizer's exact count.
    if not prompt.strip() or len(prompt.encode("utf-8")) > 24000:
        raise ValueError("A task must contain 1 to 24000 UTF-8 bytes; split larger inputs explicitly")
    if not 1 <= max_tokens <= 4096 or not 1 <= deadline <= 900:
        raise ValueError("Use 1..4096 output tokens and a deadline of 1..900 seconds")
    if reasoning not in ("none", "low"):
        raise ValueError("Reasoning must be none or low")
    job_id = uuid.uuid4().hex
    now = time.time()
    with database(root) as db:
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
                   (job_id, now, now, "queued", prompt, max_tokens, deadline, reasoning, None))
    return job_id


def generate(url: str, model: str, job: dict, result_path: Path, protocol: str = "openai"):
    started = time.monotonic()
    try:
        payload = {"model": model, "messages": [{"role": "system", "content": SYSTEM},
                   {"role": "user", "content": job["prompt"]}], "max_tokens": job["max_tokens"],
                   "temperature": 0.2, "top_p": 0.8, "stream": False,
                   "reasoning_effort": job["reasoning"],
                   "chat_template_kwargs": {"enable_thinking": job["reasoning"] != "none"}}
        if job["reasoning"] != "none":
            payload["reasoning_budget_tokens"] = min(512, max(1, job["max_tokens"] // 2))
        headers = {"Content-Type": "application/json"}
        if os.environ.get("LOCAL_ASSISTANT_API_KEY"):
            headers["Authorization"] = "Bearer " + os.environ["LOCAL_ASSISTANT_API_KEY"]
        address = endpoint(url) + "/chat/completions"
        if protocol == "lmstudio":
            address = endpoint(url)[:-3] + "/api/v1/chat"
            payload = {"model": model, "input": job["prompt"], "system_prompt": SYSTEM,
                       "max_output_tokens": job["max_tokens"], "temperature": 0.2,
                       "reasoning": "off" if job["reasoning"] == "none" else "on",
                       "stream": False, "store": False}
        req = urllib.request.Request(address,
                                     data=json.dumps(payload).encode(), headers=headers)
        # Local calls never go through HTTP_PROXY, and cannot redirect to a remote host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise ValueError("The local endpoint must not redirect")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        # Non-streaming generation may legitimately need more than 60 seconds.
        # The parent enforces the total deadline, including a stalled connection.
        with opener.open(req, timeout=job["deadline"]) as response:
            body = response.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise ValueError("Response exceeded 4 MiB")
        data = json.loads(body)
        if protocol == "lmstudio":
            message = {"role": "assistant", "content": "\n".join(x["content"] for x in data["output"] if x["type"] == "message")}
            stats = data.get("stats", {})
            # Native API does not expose finish_reason. Mark limit hits conservatively.
            count = stats.get("total_output_tokens")
            finish = "stop" if isinstance(count, int) and count < job["max_tokens"] else "length"
            data["usage"] = stats
        else:
            choice = data["choices"][0]
            message = choice["message"]
            finish = choice.get("finish_reason")
        status = "completed" if finish == "stop" and message.get("content") else "incomplete"
        result = {"status": status, "finish_reason": finish, "message": message,
                  "usage": data.get("usage"), "model": data.get("model", model), "protocol": protocol}
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    result.update(job_id=job["id"], elapsed_s=time.monotonic()-started)
    write_json(result_path, result)


def run_job(root: Path, url: str, model: str, job: dict, protocol: str = "openai") -> dict:
    result_path = root / (job["id"] + ".json")
    process = mp.get_context("spawn").Process(target=generate, args=(url, model, job, result_path, protocol))
    process.start()
    try:
        process.join(job["deadline"])
        if process.is_alive():
            process.terminate()
            process.join(5)
            result = {"job_id": job["id"], "status": "timed_out", "error": "Wall-clock deadline exceeded"}
            write_json(result_path, result)
        elif result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
        else:
            result = {"job_id": job["id"], "status": "failed", "error": f"Request process exited {process.exitcode} without a result"}
            write_json(result_path, result)
        return result
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        process.close()


def work(root: Path, url: str, model: str, once: bool = False, protocol: str = "openai"):
    endpoint(url)
    with worker_lock(root), database(root) as db:
        # A crash may leave a request with unknown outcome. Never silently replay it.
        db.execute("UPDATE jobs SET status='interrupted', updated=?, error='Previous worker exited before recording a result' WHERE status='running'", (time.time(),))
        db.commit()
        while True:
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created, id LIMIT 1").fetchone()
            if row is None:
                return
            job = dict(row)
            db.execute("UPDATE jobs SET status='running', updated=? WHERE id=?", (time.time(), job["id"]))
            db.commit()
            result = run_job(root, url, model, job, protocol)
            db.execute("UPDATE jobs SET status=?, updated=?, error=? WHERE id=?",
                       (result["status"], time.time(), result.get("error"), job["id"]))
            db.commit()
            print(json.dumps({"id": job["id"], "status": result["status"]}), flush=True)
            if once:
                return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path.home() / ".local-assistant-jobs")
    sub = parser.add_subparsers(dest="command", required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("--input", type=Path, required=True)
    submit.add_argument("--max-tokens", type=int, default=2048)
    submit.add_argument("--deadline", type=float, default=180)
    submit.add_argument("--reasoning", choices=("none", "low"), default="none")
    run = sub.add_parser("work")
    run.add_argument("--url", default="http://127.0.0.1:8091/v1")
    run.add_argument("--model", default="strata")
    run.add_argument("--protocol", choices=("openai", "lmstudio"), default="openai")
    run.add_argument("--once", action="store_true")
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "submit":
        print(enqueue(args.state, args.input.read_text(encoding="utf-8-sig"), args.max_tokens, args.deadline, args.reasoning))
    elif args.command == "work":
        work(args.state, args.url, args.model, args.once, args.protocol)
    else:
        with database(args.state) as db:
            print(json.dumps([dict(r) for r in db.execute("SELECT id,created,updated,status,error FROM jobs ORDER BY created")], indent=2))


if __name__ == "__main__":
    main()

"""Persistent chats, versioned jobs, and process supervision for the local UI."""
from __future__ import annotations

from contextlib import closing
import copy
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

from .agent import clean_env, codex_turn
from ..agents.Obtainer.datamixer.cas import ContentStore


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{16}", value):
        raise ValueError("Invalid identifier")
    return value


def preview(root: Path, limit=5) -> list:
    result = []
    output = root / "qa.jsonl"
    if output.exists():
        sources = []
        source_file = root / "qa.jsonl.sources.jsonl"
        if source_file.exists():
            with source_file.open(encoding="utf-8") as handle:
                for _, line in zip(range(limit), handle):
                    sources.append(json.loads(line))
        with output.open(encoding="utf-8") as handle:
            for index, line in zip(range(limit), handle):
                row = json.loads(line)
                result.append({**row, "source_url": sources[index].get("source_url") if index < len(sources) else None})
        return result
    warehouse = root / "warehouse"
    if not (warehouse / "catalog.db").exists():
        return []
    try:
        with closing(sqlite3.connect((warehouse / "catalog.db").as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
            cas = ContentStore(warehouse)
            for (cid,) in conn.execute("SELECT cid FROM samples WHERE quality_level='L3' ORDER BY created_at LIMIT ?", (limit,)):
                content = cas.get_json(cid)
                messages = content.get("messages", [])
                if len(messages) == 2:
                    result.append({"instruction": messages[0].get("content", ""), "input": "", "output": messages[1].get("content", ""),
                                   "source_url": content.get("provenance", {}).get("source_url")})
    except (sqlite3.Error, OSError, ValueError, KeyError):
        pass
    return result


class Workspace:
    def __init__(self, root: Path, agent=codex_turn):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.agent = agent
        self.processes = {}
        self.agent_processes = {}
        self.chats = {}
        self.closed = False
        self.config = read_json(self.root / "settings.json")
        self.config.update({"base_url": os.environ.get("DATAVOYAGER_BASE_URL", self.config.get("base_url", "")),
                            "model": os.environ.get("DATAVOYAGER_MODEL", self.config.get("model", "")),
                            "api_key": os.environ.get("DATAVOYAGER_API_KEY", ""),
                            "api_format": os.environ.get("DATAVOYAGER_API_FORMAT", self.config.get("api_format", "responses"))})
        for path in (self.root / "sessions").glob("*/state.json"):
            state = read_json(path)
            if not state.get("id"):
                continue
            identifier(state["id"])
            if state.get("busy"):
                state["busy"] = False
                self._message(state, "system", "服务重启，上次对话已中断。可以继续发送消息。")
            for turn in state.get("agent_turns", []):
                if turn["status"] == "running":
                    turn["status"] = "interrupted"
            for run in state.get("runs", []):
                if run["status"] in {"queued", "running"}:
                    run["status"] = "interrupted"
            self.chats[state["id"]] = state
            self._save(state)

    def configure(self, data: dict):
        with self.lock:
            config = {key: str(data.get(key, self.config.get(key, ""))).strip() for key in ("base_url", "model", "api_format")}
            config["api_key"] = str(data.get("api_key") or self.config.get("api_key", "")).strip()
            url = urlsplit(config["base_url"])
            if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("API 地址需为 http(s) URL，不能包含凭据、查询参数或片段")
            if not config["model"] or not config["api_key"]:
                raise ValueError("请填写模型名和 API Key")
            if config["api_format"] not in {"chat", "responses"}:
                raise ValueError("Unsupported API format")
            if len(config["api_key"]) > 4096 or len(config["model"]) > 200:
                raise ValueError("Configuration value too long")
            self.config = config
            write_json(self.root / "settings.json", {k: v for k, v in config.items() if k != "api_key"})
            return self.public_config()

    def public_config(self):
        return {**{k: v for k, v in self.config.items() if k != "api_key"}, "configured": all(self.config.get(k) for k in ("base_url", "model", "api_key"))}

    def _state(self, sid):
        identifier(sid)
        if sid not in self.chats:
            raise KeyError("Conversation not found")
        return self.chats[sid]

    def session_dir(self, sid):
        return self.root / "sessions" / identifier(sid)

    def _save(self, state):
        state["updated_at"] = time.time()
        write_json(self.session_dir(state["id"]) / "state.json", state)

    @staticmethod
    def _message(state, role, content):
        state["messages"].append({"id": uuid.uuid4().hex[:16], "role": role, "content": content, "created_at": time.time()})

    def create(self):
        with self.lock:
            sid = uuid.uuid4().hex[:16]
            state = {"id": sid, "title": "新数据集", "messages": [], "runs": [], "busy": False,
                     "thread_id": None, "agent_turns": [], "updated_at": time.time()}
            self.chats[sid] = state
            self._save(state)
            return self.snapshot(sid)

    def list_sessions(self):
        with self.lock:
            return [{k: s[k] for k in ("id", "title", "updated_at", "busy")} for s in sorted(self.chats.values(), key=lambda x: x["updated_at"], reverse=True)]

    def _run_view(self, sid, run):
        root = self.session_dir(sid) / "versions" / run["id"]
        progress = read_json(root / "run" / "progress.json")
        report = read_json(root / "run" / "report.json")
        # Supervisor terminal state is authoritative after cancel/crash.
        progress["status"] = run["status"]
        try:
            samples = preview(root)
        except (OSError, ValueError):
            samples = []  # An export may still be in the middle of writing a row.
        return {**run, "progress": progress, "report": report, "samples": samples,
                "downloadable": run["status"] == "completed" and (root / "qa.jsonl").is_file()}

    def snapshot(self, sid):
        with self.lock:
            state = copy.deepcopy(self._state(sid))
            state["runs"] = [self._run_view(sid, run) for run in state["runs"]]
            return state

    def send(self, sid, message):
        if not isinstance(message, str) or not message.strip() or len(message) > 12000:
            raise ValueError("请输入 1–12000 字的需求")
        with self.lock:
            state = self._state(sid)
            if state["busy"]:
                raise ValueError("主 agent 正在回复，请稍后再发")
            if not self.public_config()["configured"]:
                raise ValueError("请先配置 API")
            if self.closed:
                raise ValueError("服务正在关闭")
            state["busy"] = True
            self._message(state, "user", message.strip())
            if state["title"] == "新数据集":
                state["title"] = message.strip()[:32]
            config = dict(self.config)
            signature = [config["base_url"], config["model"]]
            if state.get("agent_signature") != signature:
                state["thread_id"] = None
                state["agent_signature"] = signature
            turn = {"status": "running", "usage": None}
            state["agent_turns"].append(turn)
            self._save(state)
            threading.Thread(target=self._turn, args=(sid, config, turn), daemon=True).start()
        return {"accepted": True}

    def _turn(self, sid, config, turn):
        def emit(event):
            with self.lock:
                state = self._state(sid)
                if event.get("type") == "_process_started":
                    process = event["process"]
                    if self.closed:
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        self.agent_processes[sid] = process
                    return
                if event.get("type") == "_process_finished":
                    self.agent_processes.pop(sid, None)
                    return
                if event.get("type") == "thread.started":
                    state["thread_id"] = str(event["thread_id"])
                if event.get("type") == "turn.completed":
                    raw = event.get("usage")
                    # The pinned Codex CLI emits thread-cumulative usage on resume.
                    # Persist the baseline with the thread, so reloads don't double count.
                    if isinstance(raw, dict):
                        tid = state.get("thread_id") or "unknown"
                        totals = state.setdefault("sdk_usage_totals", {})
                        previous = totals.get(tid, {})
                        reported = {k: v for k, v in raw.items() if isinstance(v, int) and not isinstance(v, bool) and v >= 0}
                        turn["sdk_thread_usage"] = reported
                        turn["usage"] = {k: v - previous.get(k, 0) if v >= previous.get(k, 0) else v for k, v in reported.items()}
                        totals[tid] = reported
                self._save(state)
        try:
            snapshot = self.snapshot(sid)
            # Limit context; sampled source text is explicitly untrusted data.
            context = {"thread_id": snapshot["thread_id"], "messages": snapshot["messages"][-16:],
                       "runs": [{k: r[k] for k in ("id", "action", "request", "status", "progress", "samples")} for r in snapshot["runs"][-6:]]}
            decision = self.agent(context, config, self.session_dir(sid), emit)
            with self.lock:
                state = self._state(sid)
                action = decision.get("action")
                if action not in {"reply", "build", "revise"} or not isinstance(decision.get("reply"), str):
                    raise ValueError("主 agent 返回了无效动作，请重试")
                if action != "reply":
                    self._launch(state, decision, config)
                self._message(state, "assistant", decision["reply"][:16000])
                turn["status"] = "completed"
        except Exception as exc:
            with self.lock:
                error = str(exc).replace(config["api_key"], "[redacted]")[:1500]
                self._message(self._state(sid), "system", "本轮未完成：" + error)
                turn["status"] = "failed"
        finally:
            with self.lock:
                state = self._state(sid)
                state["busy"] = False
                self._save(state)

    def _launch(self, state, decision, config):
        if self.closed:
            raise ValueError("服务正在关闭")
        if any(r["status"] in {"running", "queued"} for r in state["runs"]):
            raise ValueError("已有采集任务正在运行；请先停止，或等它结束后创建新版本")
        request = decision.get("request")
        pages = decision.get("max_pages")
        if not isinstance(request, str) or not request.strip() or len(request) > 16000:
            raise ValueError("Dataset request is empty or too long")
        if not isinstance(pages, int) or isinstance(pages, bool) or not 1 <= pages <= 100:
            raise ValueError("Page budget must be 1–100")
        base = None
        if decision["action"] == "revise":
            base = next((r for r in state["runs"] if r["id"] == decision.get("base_run_id")), None)
            if base is None:
                raise ValueError("找不到要复用的版本")
            view = self._run_view(state["id"], base)
            if not view["progress"].get("sources_accepted"):
                raise ValueError("该版本没有可复用的资料，请重新采集")
        rid = uuid.uuid4().hex[:16]
        root = self.session_dir(state["id"]) / "versions" / rid
        job = {"action": decision["action"], "request": request, "max_pages": pages}
        if base:
            job["base_warehouse"] = str(root.parent / base["id"] / "warehouse")
        write_json(root / "job.json", job)
        run = {"id": rid, "version": len(state["runs"]) + 1, "action": decision["action"],
               "request": request, "max_pages": pages, "base_run_id": base["id"] if base else None,
               "status": "queued", "created_at": time.time()}
        state["runs"].append(run)
        self._save(state)
        threading.Thread(target=self._work, args=(state["id"], rid, config), daemon=True).start()

    def _work(self, sid, rid, config):
        root = self.session_dir(sid) / "versions" / rid
        try:
            with self.lock:
                state = self._state(sid)
                run = next(r for r in state["runs"] if r["id"] == rid)
                if run["status"] != "queued" or self.closed:
                    return
                process = subprocess.Popen([sys.executable, "-m", "dataflowwebagent.chat.worker", str(root / "job.json")],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                           env=clean_env(config), start_new_session=True)
                self.processes[(sid, rid)] = process
                run["status"] = "running"
                self._save(state)
            # Drain without persisting a provider's raw error or credentials.
            last_line = ""
            for line in process.stderr:
                last_line = line.strip().replace(config["api_key"], "[redacted]")[:1500]
            code = process.wait()
            process.stderr.close()
            with self.lock:
                self.processes.pop((sid, rid), None)
                if run["status"] == "cancelled":
                    return
                report = read_json(root / "run" / "report.json")
                run["status"] = "completed" if code == 0 and report.get("status") == "completed" else "failed"
                if run["status"] == "completed":
                    self._message(state, "system", f"版本 {run['version']} 已完成，导出 {report['rows']} 条 QA。可以预览、下载，或继续描述修改要求。")
                else:
                    error = str(report.get("error") or last_line or "数据任务退出，未生成可用文件").replace(config["api_key"], "[redacted]")
                    run["error"] = error[:1500]
                    self._message(state, "system", f"版本 {run['version']} 失败：{run['error']}")
                self._save(state)
        except Exception as exc:
            with self.lock:
                state = self._state(sid)
                run = next(r for r in state["runs"] if r["id"] == rid)
                run["status"] = "failed"
                run["error"] = str(exc).replace(config["api_key"], "[redacted]")[:1500]
                self._message(state, "system", "数据任务未完成：" + run["error"])
                self._save(state)

    def cancel(self, sid, rid):
        with self.lock:
            state = self._state(sid)
            run = next((r for r in state["runs"] if r["id"] == identifier(rid)), None)
            if run is None:
                raise KeyError("Version not found")
            if run["status"] not in {"queued", "running"}:
                return {"status": run["status"]}
            run["status"] = "cancelled"
            process = self.processes.get((sid, rid))
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            self._message(state, "system", f"已停止版本 {run['version']}。保留已采集资料和用量记录，可继续调整需求。")
            self._save(state)
            return {"status": "cancelled"}

    def artifact(self, sid, rid, name):
        with self.lock:
            state = self._state(sid)
            run = next((r for r in state["runs"] if r["id"] == identifier(rid)), None)
            if run is None:
                raise KeyError("Version not found")
            paths = {"qa": "qa.jsonl", "sources": "qa.jsonl.sources.jsonl", "report": "run/report.json"}
            if name not in paths or run["status"] != "completed":
                raise ValueError("该版本尚未完成导出")
            path = self.session_dir(sid) / "versions" / rid / paths[name]
            if not path.is_file():
                raise KeyError("File not found")
            return path

    def close(self):
        with self.lock:
            self.closed = True
            for process in self.agent_processes.values():
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            for sid, rid in list(self.processes):
                self.cancel(sid, rid)

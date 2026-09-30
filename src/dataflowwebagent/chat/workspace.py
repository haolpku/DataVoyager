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
from ..qa_quantity import resolve_target, infer_target


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
        from ..qa_artifacts import stage_records
        for candidate in stage_records(warehouse, 'candidates'):
            if candidate.get('status') == 'source_supported':
                result.append({'instruction': candidate['question'], 'input': '', 'output': candidate['answer'],
                               'source_url': candidate['source_url']})
                if len(result) == limit:
                    return result
        if result:
            return result
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
        if run["status"] in {"cancelled", "interrupted", "failed", "accepted_partial", "continued"}:
            progress["stage"] = run["status"]
        try:
            samples = preview(root)
        except (OSError, ValueError):
            samples = []  # An export may still be in the middle of writing a row.
        from ..qa_artifacts import stage_counts, review_counts
        return {**run, "progress": progress, "report": report, "samples": samples,
                "stage_artifacts": stage_counts(root / "warehouse"), "review_counts": review_counts(root / "warehouse"),
                "downloadable": run["status"] in {"completed", "needs_confirmation", "accepted_partial", "continued"} and (root / "qa.jsonl").is_file()}

    def snapshot(self, sid):
        with self.lock:
            state = copy.deepcopy(self._state(sid))
            state["runs"] = [self._run_view(sid, run) for run in state["runs"]]
            return state

    def send(self, sid, message, stop_after="qa"):
        if not isinstance(message, str) or not message.strip() or len(message) > 12000:
            raise ValueError("请输入 1–12000 字的需求")
        if stop_after not in {"discover", "collect", "merge", "clean", "qa"}:
            raise ValueError("未知的数据处理阶段")
        with self.lock:
            state = self._state(sid)
            if state["busy"]:
                raise ValueError("主 agent 正在回复，请稍后再发")
            if not self.public_config()["configured"]:
                raise ValueError("请先配置 API")
            if self.closed:
                raise ValueError("服务正在关闭")
            state["busy"] = True
            state.pop("pending_plan", None)  # A changed requirement invalidates the old confirmation.
            self._message(state, "user", message.strip())
            state["messages"][-1]["stop_after"] = stop_after
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
                       "requested_stop_after": next((m.get("stop_after", "qa") for m in reversed(snapshot["messages"])
                                                      if m.get("role") == "user"), "qa"),
                       "pending_plan": snapshot.get("pending_plan"),
                       "runs": [{k: r.get(k) for k in ("id", "action", "request", "status", "progress", "samples", "report", "target_rows")} for r in snapshot["runs"][-6:]]}
            decision = self.agent(context, config, self.session_dir(sid), emit)
            with self.lock:
                state = self._state(sid)
                action = decision.get("action")
                if action not in {"reply", "build", "revise", "extend"} or not isinstance(decision.get("reply"), str):
                    raise ValueError("主 agent 返回了无效动作，请重试")
                if action != "reply":
                    if action == "build":
                        decision["stop_after"] = context["requested_stop_after"]
                    self._launch(state, decision, config)
                if not state.get("pending_plan"):
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

    def _launch(self, state, decision, config, *, confirmed=False):
        if self.closed:
            raise ValueError("服务正在关闭")
        if any(r["status"] in {"running", "queued"} for r in state["runs"]):
            raise ValueError("已有采集任务正在运行；请先停止，或等它结束后创建新版本")
        request = decision.get("request")
        pages = decision.get("max_source_rows", decision.get("max_pages"))
        if not isinstance(request, str) or not request.strip() or len(request) > 16000:
            raise ValueError("Dataset request is empty or too long")
        if not isinstance(pages, int) or isinstance(pages, bool) or not 1 <= pages <= 1000:
            raise ValueError("Dataset source row limit must be 1–1000")
        stop_after = {"raw": "collect", "corpus": "clean"}.get(decision.get("stop_after", "qa"), decision.get("stop_after", "qa"))
        if stop_after not in {"discover", "collect", "merge", "clean", "qa"}:
            raise ValueError("Unknown output stage")
        if decision["action"] != "build" and stop_after != "qa":
            raise ValueError("仅新建采集任务支持选择结束阶段")
        target = resolve_target(request, decision.get("target_rows"))
        if not confirmed:
            latest = next((m["content"] for m in reversed(state["messages"]) if m["role"] == "user"), "")
            target = resolve_target(request, infer_target(latest) or target)
        base = None
        if decision["action"] in {"revise", "extend"}:
            base = next((r for r in state["runs"] if r["id"] == decision.get("base_run_id")), None)
            if base is None:
                raise ValueError("找不到要复用的版本")
            view = self._run_view(state["id"], base)
            if not view["progress"].get("sources_accepted") and decision["action"] == "revise":
                raise ValueError("该版本没有可复用的资料，请重新采集")
            if decision["action"] == "extend":
                if base["status"] not in {"completed", "needs_confirmation", "accepted_partial"}:
                    raise ValueError("只能补充已结束的版本")
                request = base["request"]
                target = resolve_target(request, decision.get("target_rows") or base.get("target_rows"))
                if not target:
                    raise ValueError("该版本没有目标题数，请在对话中创建新需求")
        from ..agents.Obtainer.datamixer.operators.evidence import MAX_QA_PER_DOCUMENT
        capacity = (view["progress"].get("sources_accepted", 0) * MAX_QA_PER_DOCUMENT if base and decision["action"] == "revise"
                    else pages * MAX_QA_PER_DOCUMENT + (view["report"].get("rows", 0) if base else 0))
        if stop_after != "qa":
            target = None
        if stop_after != "discover" and target and target > capacity and not confirmed:
            state["pending_plan"] = {"id": uuid.uuid4().hex[:16], "decision": {**decision, "request": request, "target_rows": target},
                                     "target_rows": target, "capacity_upper_bound": capacity,
                                     "max_source_rows": pages, "max_pages": pages}
            basis = "已有资料" if decision["action"] == "revise" else "本次数据集样本上限"
            self._message(state, "system", f"目标是 {target} 条 QA。按当前每份资料最多生成六条候选的流程，{basis}最多支持 {capacity} 条，筛选后可能更少。请在确认卡片中调整或确认尝试；也可以在对话中修改需求。尚未开始数据任务。")
            self._save(state)
            return
        state.pop("pending_plan", None)
        rid = uuid.uuid4().hex[:16]
        root = self.session_dir(state["id"]) / "versions" / rid
        job = {"action": decision["action"], "request": request, "max_source_rows": pages,
               "max_pages": pages, "target_rows": target, "stop_after": stop_after}
        if decision.get("selected_dataset_ids"):
            job["selected_dataset_ids"] = decision["selected_dataset_ids"]
        if base:
            job["base_warehouse"] = str(root.parent / base["id"] / "warehouse")
        write_json(root / "job.json", job)
        run = {"id": rid, "version": len(state["runs"]) + 1, "action": decision["action"],
               "request": request, "max_source_rows": pages, "max_pages": pages,
               "target_rows": target, "stop_after": stop_after, "base_run_id": base["id"] if base else None,
               "status": "queued", "created_at": time.time()}
        state["runs"].append(run)
        self._save(state)
        threading.Thread(target=self._work, args=(state["id"], rid, config), daemon=True).start()

    def select_sources(self, sid, rid, data):
        with self.lock:
            state = self._state(sid)
            discovery = next((r for r in state["runs"] if r["id"] == identifier(rid)), None)
            if not discovery or discovery["status"] != "awaiting_source_selection":
                raise ValueError("这个版本没有待选择的数据集来源")
            if any(r["status"] in {"running", "queued"} for r in state["runs"]):
                raise ValueError("已有数据任务正在运行")
            root = self.session_dir(sid) / "versions" / rid
            report = read_json(root / "run" / "report.json")
            candidates = report.get("selection_candidates") or []
            allowed = {row.get("dataset_id") for row in candidates}
            selected = data.get("dataset_ids")
            if not isinstance(selected, list) or not 1 <= len(selected) <= 5 or any(item not in allowed for item in selected):
                raise ValueError("请选择 1–5 个当前列表中的数据集")
            if len(set(selected)) != len(selected):
                raise ValueError("数据集不能重复选择")
            stage = data.get("stop_after", "qa")
            if stage not in {"collect", "merge", "clean", "qa"}:
                raise ValueError("请选择有效的后续处理阶段")
            target = data.get("target_rows")
            if stage == "qa" and (type(target) is not int or not 1 <= target <= 10000):
                raise ValueError("请填写 1–10000 的 QA 目标数量")
            pages = data.get("max_source_rows", discovery.get("max_source_rows", 50))
            if type(pages) is not int or not 1 <= pages <= 1000:
                raise ValueError("来源样本上限需为 1–1000")
            decision = {"action": "build", "request": discovery["request"],
                        "max_source_rows": pages, "target_rows": target or 0,
                        "stop_after": stage, "selected_dataset_ids": selected}
            self._launch(state, decision, dict(self.config), confirmed=True)
            self._message(state, "user", f"已选择 {len(selected)} 个数据集，继续执行到：{stage}。")
            self._save(state)
            return {"status": "queued"}

    def confirm_plan(self, sid, data):
        with self.lock:
            state = self._state(sid)
            plan = state.get("pending_plan")
            if not plan or data.get("plan_id") != plan["id"]:
                raise ValueError("确认已过期，请查看最新需求")
            if state["busy"]:
                raise ValueError("主 agent 正在回复，请稍后确认")
            if data.get("action") == "cancel":
                state.pop("pending_plan")
                self._message(state, "user", "取消这次构建计划。")
                self._save(state)
                return {"status": "cancelled"}
            if data.get("action") != "start":
                raise ValueError("Invalid confirmation action")
            if not self.public_config()["configured"]:
                raise ValueError("请先配置 API")
            decision = {**plan["decision"], "target_rows": data.get("target_rows", plan["target_rows"]),
                        "max_source_rows": data.get("max_source_rows", data.get("max_pages", plan.get("max_source_rows", plan.get("max_pages"))))}
            # Explicit confirmation may choose a smaller goal, never silently rewrite history.
            if type(decision["target_rows"]) is not int or decision["target_rows"] < 1:
                raise ValueError("请输入正整数题数")
            if decision["target_rows"] != plan["target_rows"]:
                decision["request"] += f"\n用户确认：本次目标题数改为 {decision['target_rows']} 条，以此数量为准。"
            self._launch(state, decision, dict(self.config), confirmed=True)
            self._message(state, "user", f"确认尝试生成 {decision['target_rows']} 条，来源样本上限 {decision.get('max_source_rows', decision.get('max_pages'))} 条；不足时再次确认。")
            self._save(state)
            return {"status": "queued"}

    def resolve_shortfall(self, sid, rid, data):
        with self.lock:
            state = self._state(sid)
            run = next((r for r in state["runs"] if r["id"] == identifier(rid)), None)
            if not run or run["status"] != "needs_confirmation":
                raise ValueError("该版本没有待确认的数量缺口")
            if state["busy"]:
                raise ValueError("主 agent 正在回复，请稍后确认")
            root = self.session_dir(sid) / "versions" / rid
            report = read_json(root / "run" / "report.json")
            if data.get("action") == "accept":
                if not report.get("rows") or not (root / "qa.jsonl").is_file():
                    raise ValueError("没有可接受的 QA，请调整需求或增加数据集样本上限")
                run["status"] = "accepted_partial"
                report.update(status="accepted_partial", accepted_rows=report["rows"], accepted_at=time.time())
                write_json(root / "run" / "report.json", report)
                self._message(state, "user", f"接受版本 {run['version']} 当前的 {report['rows']} 条，原目标 {report['target_rows']} 条，结束本次构建。")
                self._save(state)
                return {"status": "accepted_partial"}
            if data.get("action") != "extend":
                raise ValueError("Invalid shortfall action")
            if not self.public_config()["configured"]:
                raise ValueError("请先配置 API")
            pages = data.get("max_source_rows", data.get("max_pages"))
            self._launch(state, {"action": "extend", "request": run["request"], "target_rows": run["target_rows"],
                                 "max_source_rows": pages, "base_run_id": rid}, dict(self.config), confirmed=True)
            # Old version remains readable, but the same prompt cannot be replayed twice.
            run["status"] = "continued"
            self._message(state, "user", f"保留版本 {run['version']} 已有题目，将数据集样本上限增加到 {pages} 条，继续补齐；生成新版本。")
            self._save(state)
            return {"status": "queued"}

    def _work(self, sid, rid, config):
        root = self.session_dir(sid) / "versions" / rid
        try:
            with self.lock:
                state = self._state(sid)
                run = next(r for r in state["runs"] if r["id"] == rid)
                if run["status"] != "queued" or self.closed:
                    return
                worker_env = clean_env(config)
                # Kaggle credentials are needed only by the dataset worker and
                # must not be exposed to the conversational SDK process.
                for key in ("KAGGLE_USERNAME", "KAGGLE_KEY"):
                    if os.environ.get(key):
                        worker_env[key] = os.environ[key]
                process = subprocess.Popen([sys.executable, "-m", "dataflowwebagent.chat.worker", str(root / "job.json")],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                           env=worker_env, start_new_session=True)
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
                run["status"] = report["status"] if code == 0 and report.get("status") in {"completed", "needs_confirmation", "awaiting_source_selection"} else "failed"
                if run["status"] == "completed":
                    if report.get("stop_after") in {"collect", "merge", "clean", "raw", "corpus"}:
                        stage_name = {"collect": "找数据", "raw": "找数据", "merge": "合并数据", "clean": "清洗数据", "corpus": "清洗数据"}.get(report.get("stop_after"), "数据收集")
                        self._message(state, "system", f"版本 {run['version']} 已完成：找到 {report.get('source_rows', report.get('pages_collected', 0))} 条原始样本，保留 {report['sources_accepted']} 份正文。已完成{stage_name}，并在生成 QA 前结束；可在右侧下载阶段数据。")
                    else:
                        self._message(state, "system", f"版本 {run['version']} 已完成，导出 {report['rows']} 条来源审核通过的 QA。模型审核不等于专家认证。可以预览、下载，或继续修改。")
                elif run["status"] == "awaiting_source_selection":
                    self._message(state, "system", f"版本 {run['version']} 找到 {len(report.get('selection_candidates', []))} 个候选数据集。请在右侧选择来源，再确认合并、清洗或生成 QA。目录搜索没有下载数据，也没有调用生成模型。")
                elif run["status"] == "needs_confirmation":
                    self._message(state, "system", f"版本 {run['version']} 已生成 {report['rows']} / {report['target_rows']} 条，还差 {report['shortfall']} 条。当前补采已停止，不再消耗生成 API。可以接受当前数量、增加数据集样本上限继续补齐，或在对话中调整主题／来源后创建新版本。新版本数量统计来源审核通过并去重的 QA；模型审核不等于专家认证。")
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
            from ..qa_artifacts import STAGES, export_stage
            if name in STAGES:
                root = self.session_dir(sid) / "versions" / rid
                if not (root / "warehouse" / "catalog.db").exists():
                    raise ValueError("还没有已保存的阶段数据")
                path = root / "run" / "artifacts" / (name + ".jsonl")
                export_stage(root / "warehouse", name, path)
                return path
            paths = {"qa": "qa.jsonl", "sources": "qa.jsonl.sources.jsonl", "report": "run/report.json"}
            if name not in paths or run["status"] not in {"completed", "needs_confirmation", "accepted_partial", "continued"}:
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

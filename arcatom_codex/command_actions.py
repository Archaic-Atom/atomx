"""Real slash actions shared by the command menu and typed commands.

命令变更只在后端确认成功后更新界面；失败时保留命令草稿。
"""
from __future__ import annotations

from .i18n import tr
import asyncio
import json
import os
from pathlib import Path
import time
import subprocess
import sys

from rich.text import Text
from textual.widgets import OptionList

from .commands import BY_NAME, COMMANDS
from .pickers import Picker, Prompt
from .personal import PersonalSkill, bridge_instructions, discover_skills
from .rpc import CodexClient, RpcError
from .state import clean
from .preferences import write_preferences


class CommandActions:
    async def save_view_preferences(self):
        if not self.demo:
            await asyncio.to_thread(write_preferences, self.view_preferences)

    async def native_command(self, command, tid):
        """Temporarily hand the terminal to the official Codex workflow."""
        if self.demo:
            self.show_text(tr('原生 Codex · 演示模式'), command + tr(' 会在真实模式下打开官方交互界面。\n演示模式不会启动真实客户端或修改你的配置。'))
            return
        if any(s.active_turn or s.busy_since or s.meta.get("status", {}).get("type") == "active" for s in self.store.sessions.values()) or self.sending:
            raise RpcError(tr('有任务正在运行。请等待完成或停止任务，再打开原生命令。'))
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RpcError(tr('原生命令需要交互终端，请在系统终端中运行 arcatom。'))
        session = self.store.get(tid) if tid else None
        if session and session.meta.get("ephemeral"):
            raise RpcError(tr('临时分支无法交给另一个进程恢复；请先用 /fork 创建普通分支，再打开原生命令。'))
        cwd = session.meta.get("cwd", self.cwd) if session else self.cwd
        # Reconnect after native configuration changes. First make sure no owned
        # background terminals would be lost when restarting this app-server.
        for loaded in list(self.store.sessions.values()):
            if loaded.resumed:
                terminals = await self.client.call("thread/backgroundTerminals/list", {"threadId": loaded.id})
                if terminals.get("data"):
                    raise RpcError(tr('仍有后台终端运行，请先用 /ps 查看并在需要时 /stop，再进入原生命令。'))
        if session and session.resumed:
            await self.client.call("thread/unsubscribe", {"threadId": tid})
            session.resumed = False
        argv = [sys.executable, "-m", "arcatom_codex.native", "--binary", self.client.binary,
                "--cwd", cwd, "--command", command]
        if tid:
            argv.extend(["--thread", tid])
        self.native_active = True
        try:
            with self.suspend():
                result = subprocess.run(argv, cwd=str(Path(__file__).resolve().parents[1]),
                                        env=dict(os.environ, ARCATOM_LANGUAGE=self.language), check=False)
            if result.returncode:
                self.notify(tr('官方 Codex 已退出（{0}），已返回 Arcatom。').format(result.returncode), severity="warning")
        finally:
            self.native_active = False
        binary = self.client.binary
        await self.client.close()
        self.ready = False
        for saved in self.store.sessions.values():
            saved.resumed = False
        self.client = CodexClient(binary=binary, cwd=self.cwd)
        await self.connect()
        if tid:
            try:
                # Drop cached history so official updates are reflected on return.
                session.items.clear()
                session.hydrated = False
                await self.read_history(tid)
                await self.ensure_resumed(tid)
            except RpcError as exc:
                self.notify(str(exc), severity="warning")
                self.store.sessions.pop(tid, None)
                self.current = None
                self.show_home()
        self.transcript_cache.clear()
        self.transcript_signature = None
        await self.load_account()
        self.paint(force=True)

    async def choose(self, title, choices, selected=None):
        return await self.push_screen_wait(Picker(title, choices, selected))

    def command_notice(self, tid, text):
        self.store.get(tid).ingest({"id": f"local-{time.time_ns()}", "type": "notice", "text": text})
        self.store.revision += 1

    def show_text(self, title, value):
        from .ui import Detail
        self.push_screen(Detail(title, Text(clean(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)))))

    def apply_runtime(self, session, response):
        """Use effective server settings instead of guessing model defaults."""
        for key in ("model", "reasoningEffort", "serviceTier", "approvalPolicy", "approvalsReviewer",
                    "sandbox", "activePermissionProfile", "collaborationMode", "cwd"):
            if key in response:
                session.meta[key] = response[key]

    async def ensure_resumed(self, tid):
        lock = self.resume_locks.setdefault(tid, asyncio.Lock())
        async with lock:
            session = self.store.get(tid)
            if not session.resumed:
                result = await self.client.call("thread/resume", {"threadId": tid, "excludeTurns": True})
                self.store.merge(result["thread"])
                self.apply_runtime(session, result)
                session.resumed = True
                self.store.revision += 1
            return session

    async def update_settings(self, tid, **settings):
        session = await self.ensure_resumed(tid)
        await self.client.call("thread/settings/update", {"threadId": tid, **settings})
        for key, value in settings.items():
            session.meta[{"effort": "reasoningEffort", "sandboxPolicy": "sandbox"}.get(key, key)] = value
        self.store.revision += 1
        self.paint(force=True)

    async def model_catalog(self):
        models = []
        async for batch in self.client.pages("model/list", {"includeHidden": False}):
            models.extend(batch)
        return [model for model in models if not model.get("hidden")]

    async def select_model(self, tid, argument="", effort_only=False):
        session = await self.ensure_resumed(tid)
        models = await self.model_catalog()
        if not models:
            raise RpcError(tr('后端没有返回可选模型，请检查模型提供商配置。'))
        current = session.meta.get("model")
        if effort_only:
            model = next((m for m in models if m["model"] == current), None)
            if model is None:
                raise RpcError(tr('当前模型不在后端目录中，请先用 /model 选择模型。'))
        else:
            target = argument.strip() or await self.choose(
                tr('选择模型 · 当前会话'), [(m["model"], m.get("displayName", m["model"]) + "  " + m.get("description", "")) for m in models], current)
            if target is None:
                return
            model = next((m for m in models if m["model"] == target or m["id"] == target), None)
            if model is None:
                raise RpcError(tr('模型不在当前账户的可选目录中：') + target)
        efforts = model.get("supportedReasoningEfforts") or []
        effort = session.meta.get("reasoningEffort") if model["model"] == current else model.get("defaultReasoningEffort")
        if efforts:
            effort = await self.choose(tr('推理强度 · ') + model.get("displayName", model["model"]),
                [(e["reasoningEffort"], e["reasoningEffort"] + "  " + e.get("description", "")) for e in efforts],
                effort or model.get("defaultReasoningEffort"))
            if effort is None:
                return
        settings = {"model": model["model"]}
        if effort is not None:
            settings["effort"] = effort
        mode = session.meta.get("collaborationMode")
        if mode:
            settings["collaborationMode"] = {"mode": mode["mode"], "settings": {
                **mode.get("settings", {}), "model": model["model"], "reasoning_effort": effort}}
        if model["model"] != current:
            settings["serviceTier"] = model.get("defaultServiceTier")
        await self.update_settings(tid, **settings)
        self.command_notice(tid, tr('已选择 ') + model["model"] + (" · " + effort if effort else "") + tr('，下一回合生效。'))

    async def send_command_prompt(self, tid, prompt):
        # Pass a captured prompt; never borrow another session's current draft.
        await self.send_prompt(tid, prompt_override=prompt)

    async def slash(self, text):
        from .ui import Composer
        head, _, argument = text.strip().partition(" ")
        name = head[1:]
        command = BY_NAME.get(name)
        if command is None:
            self.notify(tr('没有这个命令；输入 / 可搜索全部命令。'), severity="warning")
            return
        tid = self.current
        if self.command_busy:
            self.notify(tr('上一条命令正在处理，当前输入已保留。'))
            return
        self.command_busy = True
        composer = self.query_one(Composer)
        composer.clear()
        self.hide_commands()
        try:
            if not self.ready and name not in ("help", "exit", "quit", "warnings", "theme", "settings", "palette"):
                raise RpcError(tr('尚未连接 Codex，请先 Ctrl+R 重连。'))
            await self.dispatch_command(name, argument.strip(), tid)
        except Exception:
            if self.current == tid and not composer.text:
                composer.load_text(text)
            elif tid and not self.store.get(tid).draft:
                self.store.get(tid).draft = text
            raise
        finally:
            self.command_busy = False
            self.store.revision += 1
            if self.screen is self.main_screen:
                (composer if self.current else self.query_one("#search")).focus()

    async def dispatch_command(self, name, argument, tid):
        from .ui import Composer, NewSession
        if BY_NAME[name].native:
            await self.native_command("/" + name + (" " + argument if argument else ""), tid)
            return
        session = self.store.get(tid) if tid else None
        if name in ("settings", "palette"):
            self.action_settings()
        elif name in ("model", "reasoning"):
            await self.select_model(tid, argument, name == "reasoning")
        elif name in ("quit", "exit"):
            self.action_request_quit()
        elif name in ("new", "clear"):
            cwd = await self.push_screen_wait(NewSession(self.view_preferences.get("default_cwd") or (session.meta.get("cwd", self.cwd) if session else self.cwd)))
            if cwd:
                await self.create_session(cwd)
                if argument:
                    await self.send_command_prompt(self.current, argument)
        elif name in ("resume", "agents"):
            if argument:
                await self.open_session(argument)
            else:
                self.show_home()
        elif name in ("agent", "subagents"):
            await self.refresh_activity(tid)
            target = await self.choose(tr('当前会话的子代理'), [(i, s.get("name") or self.store.get(i).title) for i, s in session.agents.items()])
            if target:
                await self.open_session(target)
        elif name == "usage":
            if argument:
                await self.native_command("/usage " + argument, tid)
            else:
                await self.load_account()
                self.action_usage()
        elif name == "status":
            await self.ensure_resumed(tid)
            self.show_text(tr('当前会话'), {"threadId": tid, **session.meta, "tokenUsage": session.usage})
        elif name == "rename":
            title = argument or await self.push_screen_wait(Prompt(tr('会话名称'), session.title))
            if title:
                await self.client.call("thread/name/set", {"threadId": tid, "name": title})
                session.meta["name"] = title
        elif name in ("pwd", "cwd", "rollout"):
            self.show_text(tr('会话路径'), session.meta.get("path" if name == "rollout" else "cwd") or tr('后端未提供路径'))
        elif name == "cd":
            value = argument or await self.push_screen_wait(Prompt(tr('工作目录'), session.meta.get("cwd", self.cwd)))
            if value:
                path = Path(value).expanduser()
                if not path.is_absolute():
                    path = Path(session.meta.get("cwd", self.cwd)) / path
                path = path.resolve()
                if not path.is_dir():
                    raise RpcError(tr('目录不存在：') + str(path))
                await self.update_settings(tid, cwd=str(path))
                self.command_notice(tid, tr('工作目录已更改：') + str(path))
        elif name == "permissions":
            profiles = []
            async for page in self.client.pages("permissionProfile/list", {"cwd": session.meta.get("cwd", self.cwd)}):
                profiles.extend(page)
            choice = await self.choose(tr('会话权限 · 仅列出后端允许的配置'), [
                (p["id"], p["id"] + "  " + (p.get("description") or "")) for p in profiles if p.get("allowed")])
            if choice:
                await self.update_settings(tid, permissions=choice)
                self.command_notice(tid, tr('权限配置已更新：') + choice)
        elif name == "plan":
            await self.ensure_resumed(tid)
            active = session.meta.get("collaborationMode") or {}
            mode = "default" if active.get("mode") == "plan" and not argument else "plan"
            await self.update_settings(tid, collaborationMode={"mode": mode, "settings": {
                "model": session.meta["model"], "reasoning_effort": session.meta.get("reasoningEffort"), "developer_instructions": None}})
            self.command_notice(tid, tr('已切换到') + (tr('计划模式') if mode == "plan" else tr('执行模式')))
            if argument:
                await self.send_command_prompt(tid, argument)
        elif name == "fast":
            await self.ensure_resumed(tid)
            model = next((m for m in await self.model_catalog() if m["model"] == session.meta.get("model")), {})
            tiers = model.get("serviceTiers") or []
            fast = next((t for t in tiers if t["id"] in ("fast", "priority") or t.get("name", "").lower() == "fast"), None)
            if not fast:
                raise RpcError(tr('当前模型未提供 Fast 档位，请在 /model 中选择提供该档位的模型。'))
            value = None if session.meta.get("serviceTier") == fast["id"] else fast["id"]
            await self.update_settings(tid, serviceTier=value)
            self.command_notice(tid, "Fast " + (tr('已开启') if value else tr('已关闭')))
        elif name in ("compact", "review"):
            await self.ensure_resumed(tid)
            if session.active_turn or tid in self.sending:
                raise RpcError(tr('请等待当前回合结束，或按 Ctrl+C 停止后再执行。'))
            target = {"type": "custom", "instructions": argument} if argument else {"type": "uncommittedChanges"}
            if name == "review" and not argument:
                kind = await self.choose(tr('审查范围'), [("uncommittedChanges", tr('当前工作区修改')), ("baseBranch", tr('与指定分支比较')), ("commit", tr('指定提交'))])
                if kind is None:
                    return
                target = {"type": kind}
                if kind != "uncommittedChanges":
                    value = await self.push_screen_wait(Prompt(tr('分支名称') if kind == "baseBranch" else tr('提交 SHA')))
                    if not value:
                        return
                    target["branch" if kind == "baseBranch" else "sha"] = value
            session.busy_since = time.monotonic()
            session.phase = tr('正在压缩上下文') if name == "compact" else tr('正在审查代码')
            try:
                await self.client.call("thread/compact/start" if name == "compact" else "review/start",
                    {"threadId": tid} if name == "compact" else {"threadId": tid, "target": target})
            except Exception:
                session.busy_since = None
                raise
        elif name in ("fork", "side", "btw"):
            if session.active_turn or tid in self.sending:
                raise RpcError(tr('当前回合结束后才能分叉会话。'))
            result = await self.client.call("thread/fork", {"threadId": tid, "ephemeral": name != "fork", "excludeTurns": True})
            child = self.store.merge(result["thread"])
            self.apply_runtime(child, result)
            child.resumed = True
            await self.open_session(child.id)
            if argument:
                await self.send_command_prompt(child.id, argument)
        elif name == "goal":
            await self.ensure_resumed(tid)
            if argument == "clear":
                result = await self.client.call("thread/goal/clear", {"threadId": tid})
            elif argument in ("pause", "resume"):
                result = await self.client.call("thread/goal/set", {"threadId": tid, "status": "paused" if argument == "pause" else "active"})
            elif argument:
                result = await self.client.call("thread/goal/set", {"threadId": tid, "objective": argument})
            else:
                result = await self.client.call("thread/goal/get", {"threadId": tid})
            self.show_text(tr('任务目标 · /goal 目标 · pause / resume / clear'), result)
        elif name in ("archive", "delete"):
            if session.active_turn or tid in self.sending:
                raise RpcError(tr('请先按 Ctrl+C 停止当前任务。'))
            await self.client.call("thread/" + name, {"threadId": tid})
            if name == "delete":
                self.store.remove_thread(tid)
            else:
                session.meta["archived"] = True
                session.resumed = False
            self.current = None
            self.show_home()
            self.notify(tr('会话已') + (tr('删除') if name == "delete" else tr('归档')))
        elif name == "ps":
            self.action_activity()
        elif name in ("stop", "clean"):
            await self.ensure_resumed(tid)
            await self.client.call("thread/backgroundTerminals/clean", {"threadId": tid})
            await self.refresh_activity(tid)
            self.command_notice(tid, tr('已停止当前会话的后台终端。Ctrl+C 可停止模型回合。'))
        elif name == "skills":
            await self.select_skill(session)
        elif name == "mcp":
            data = []
            async for page in self.client.pages("mcpServerStatus/list", {}):
                data.extend(page)
            self.show_text(tr('MCP 服务与工具'), data or tr('没有配置 MCP 服务'))
        elif name == "debug-config":
            result = await self.client.call("config/read", {"cwd": session.meta.get("cwd", self.cwd), "includeLayers": True})
            self.show_text(tr('配置来源'), redact(result))
        elif name == "warnings":
            self.show_text(tr('诊断信息'), "\n".join(self.store.account_errors + list(getattr(self.client, "stderr", []))) or tr('当前没有诊断警告。'))
        elif name in ("recap", "init"):
            await self.send_command_prompt(tid, tr('请总结当前会话的目标、已完成工作、关键决策和待办事项。') if name == "recap" else
                tr('请检查当前项目并创建适用的 AGENTS.md，记录项目结构、构建测试命令和开发约定；如果文件已存在，先阅读并保留已有有效规则。'))
        elif name == "copy":
            text = next((i.get("text", "") for i in reversed(list(session.items.values())) if i.get("type") in ("agentMessage", "plan") and i.get("id") not in session.streaming_items), "")
            if not text:
                raise RpcError(tr('当前会话还没有完整回复可复制。'))
            self.copy_to_clipboard(text)
            self.notify(tr('已发送到终端剪贴板。'))
        elif name == "export":
            await self.export_chat(session, argument)
        elif name == "diff":
            await self.git_diff(session)
        elif name == "mention":
            await self.mention_file(session, argument)
        elif name == "raw":
            self.raw_transcript = not self.raw_transcript
            self.transcript_signature = None
            self.transcript_cache.clear()
            self.command_notice(tid, tr('纯文本显示') + (tr('已开启') if self.raw_transcript else tr('已关闭')))
        elif name == "theme":
            from pygments.styles import get_all_styles
            value = await self.choose(tr('代码语法高亮主题'), [(s, s) for s in sorted(get_all_styles())], self.code_theme)
            if value:
                self.code_theme = value
                self.view_preferences["code_theme"] = value
                self.transcript_cache.clear()
                self.transcript_signature = None
                await self.save_view_preferences()
        elif name == "statusline":
            value = await self.push_screen_wait(Prompt(tr('状态栏顺序 · time tokens context limits model · 留空字段会隐藏'), " ".join(self.status_fields)))
            if value:
                fields = value.split()
                if len(set(fields)) != len(fields) or any(f not in ("time", "tokens", "context", "limits", "model") for f in fields):
                    raise RpcError(tr('字段应为 time tokens context limits model，不可重复。'))
                self.status_fields = fields
                self.view_preferences["status_fields"] = fields
                await self.save_view_preferences()
        elif name == "title":
            value = await self.choose(tr('终端标题'), [("app", "Arcatom Codex"), ("session", tr('会话名称')), ("model", tr('模型 · 会话名称'))])
            if value:
                self.view_preferences["title"] = value
                await self.save_view_preferences()
        elif name == "help":
            self.show_text(tr('命令与快捷键'), tr('输入 / 显示提示；↑↓ 选择，Tab 补全，Enter 执行，Esc 收起。\n← 空输入返回列表；Ctrl+X 删除选中历史；Ctrl+C 停止回合。\n带‘原生’的命令会打开官方 Codex，退出后回到 Arcatom。\n\n') +
                "\n".join(f"/{c.name:24} {tr(c.description)}" + (tr('  [原生]') if c.native else "") for c in COMMANDS))

    async def select_skill(self, session):
        from .ui import Composer
        skills = [] if self.demo else discover_skills()
        result = await self.client.call("skills/list", {"cwds": [session.meta.get("cwd", self.cwd)], "forceReload": True})
        for entry in result.get("data", []):
            for skill in entry.get("skills", []):
                if skill.get("enabled", True) and not any(s.name == skill["name"] for s in skills):
                    skills.append(PersonalSkill(skill["name"], skill.get("description", ""), Path(skill["path"])))
        self.personal_skills = skills
        self.personal_instructions = "" if self.demo else bridge_instructions(skills)
        target = await self.choose(tr('Skills · 选择后输入任务'), [(s.name, f"${s.name}  {s.description}\n{s.path}") for s in skills])
        if target:
            self.query_one(Composer).load_text("$" + target + " ")
            self.query_one(Composer).focus()

    async def export_chat(self, session, argument):
        default = str(Path(session.meta.get("cwd", self.cwd)) / ("codex-" + session.id[:8] + ".md"))
        value = argument or await self.push_screen_wait(Prompt(tr('导出 Markdown 文件路径'), default))
        if not value:
            return
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(session.meta.get("cwd", self.cwd)) / path
        body = ["# " + session.title]
        for item in session.items.values():
            if item.get("type") == "userMessage":
                body.append("## User\n\n" + "\n".join(c.get("text", "") for c in item.get("content", [])))
            elif item.get("type") in ("agentMessage", "plan"):
                body.append("## Codex\n\n" + item.get("text", ""))
        def write():
            # Exclusive create: never overwrite a user file silently.
            with path.open("x") as output:
                output.write("\n\n".join(body) + "\n")
        await asyncio.to_thread(write)
        self.command_notice(session.id, tr('已导出：') + str(path))

    async def git_diff(self, session):
        cwd = session.meta.get("cwd", self.cwd)
        async def git(*args, allowed=(0,)):
            process = await asyncio.create_subprocess_exec("git", *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await process.communicate()
            if process.returncode not in allowed:
                raise RpcError(stderr.decode(errors="replace"))
            return stdout
        diff = await git("diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", allowed=(0, 128))
        if not diff:
            diff = await git("diff", "--no-ext-diff", "--no-textconv", "--")
            diff += await git("diff", "--cached", "--no-ext-diff", "--no-textconv", "--")
        untracked = await git("ls-files", "--others", "--exclude-standard", "-z")
        for filename in untracked.split(b"\0"):
            if not filename:
                continue
            diff += await git("diff", "--no-index", "--no-ext-diff", "--no-textconv", "--", "/dev/null", os.fsdecode(filename), allowed=(0, 1))
            if len(diff) > 2_000_000:
                diff += b"\n[Diff truncated at 2 MB]\n"
                break
        self.show_text(tr('Git 修改'), diff.decode(errors="replace") or tr('工作区没有修改。'))

    async def mention_file(self, session, argument):
        from .ui import Composer
        root = Path(session.meta.get("cwd", self.cwd))
        if argument:
            path = Path(argument).expanduser()
            path = path if path.is_absolute() else root / path
            if not path.exists():
                raise RpcError(tr('路径不存在：') + str(path))
            value = str(path)
        else:
            def scan():
                files = []
                for parent, dirs, names in os.walk(root):
                    dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "target")]
                    files.extend(str((Path(parent) / n).relative_to(root)) for n in names if not n.startswith("."))
                    if len(files) >= 1500:
                        break
                return sorted(files[:1500])
            paths = await asyncio.to_thread(scan)
            selected = await self.choose(tr('选择文件 · 输入筛选'), [(p, p) for p in paths])
            if not selected:
                return
            value = str(root / selected)
        self.query_one(Composer).load_text(tr('请查看文件 ') + json.dumps(value, ensure_ascii=False) + " ")


def redact(value):
    """Mask credentials in configuration diagnostics. 诊断中隐藏认证字段。"""
    if isinstance(value, dict):
        return {k: "[redacted]" if any(part in k.lower() for part in ("secret", "password", "token", "api_key", "authorization", "headers", "env")) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value

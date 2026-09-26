"""Real slash actions shared by the command menu and typed commands.

命令变更只在后端确认成功后更新界面；失败时保留命令草稿。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from rich.text import Text

from ..access import AppActions
from ..backend.rpc import CodexClient, RpcError
from ..core.commands import BY_NAME, COMMANDS
from ..core.state import Session, clean
from ..i18n import tr
from ..personal import PersonalSkill, bridge_instructions, discover_skills
from ..pickers import Picker, Prompt
from ..preferences import approval_defaults, write_preferences


class CommandActions(AppActions):
    """Execute slash commands against the workspace backend. 执行工作台斜杠命令。"""

    async def save_view_preferences(self) -> None:
        """Persist UI settings outside demonstration mode. 非演示模式保存界面偏好。"""
        if not self.workspace.demo:
            await asyncio.to_thread(
                write_preferences, self.workspace.view_preferences
            )

    async def native_command(self, command: str, tid: str | None) -> None:
        """Temporarily hand the terminal to the official Codex workflow.

        暂时将终端交给官方 Codex 工作流。
        """
        if self.workspace.demo:
            self.workspace.show_text(
                tr("原生 Codex · 演示模式"),
                command
                + tr(
                    " 会在真实模式下打开官方交互界面。\n演示模式不会启动真实客户端或修改你的配置。"
                ),
            )
            return
        if (
            any(
                s.active_turn
                or s.busy_since
                or s.meta.get("status", {}).get("type") == "active"
                for s in self.workspace.store.sessions.values()
            )
            or self.workspace.sending
        ):
            raise RpcError(
                tr("有任务正在运行。请等待完成或停止任务，再打开原生命令。")
            )
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RpcError(
                tr("原生命令需要交互终端，请在系统终端中运行 atomx。")
            )
        session = self.workspace.store.get(tid) if tid else None
        if session and session.meta.get("ephemeral"):
            raise RpcError(
                tr(
                    "临时分支无法交给另一个进程恢复；请先用 /fork 创建普通分支，再打开原生命令。"
                )
            )
        cwd = (
            session.meta.get("cwd", self.workspace.cwd)
            if session
            else self.workspace.cwd
        )
        # Reconnect after native configuration changes. First make sure no owned
        # background terminals would be lost when restarting this app-server.
        # 返回原生流程前确认后台终端不会因连接变化而丢失。
        for loaded in list(self.workspace.store.sessions.values()):
            if loaded.resumed:
                terminals = await self.workspace.client.call(
                    "thread/backgroundTerminals/list", {"threadId": loaded.id}
                )
                if terminals.get("data"):
                    raise RpcError(
                        tr(
                            "仍有后台终端运行，请先用 /ps 查看并在需要时 /stop，再进入原生命令。"
                        )
                    )
        if session and session.resumed:
            await self.workspace.client.call(
                "thread/unsubscribe", {"threadId": tid}
            )
            session.resumed = False
        argv = [
            sys.executable,
            "-m",
            "arcatom_codex.native",
            "--binary",
            self.workspace.client.binary,
            "--cwd",
            cwd,
            "--command",
            command,
        ]
        if tid:
            argv.extend(["--thread", tid])
        if getattr(self.workspace.client, "remote_endpoint", None):
            argv.extend(["--remote", self.workspace.client.remote_endpoint])
        self.workspace.native_active = True
        try:
            with self.workspace.suspend():
                result = subprocess.run(
                    argv,
                    cwd=str(Path(__file__).resolve().parents[1]),
                    env=dict(
                        os.environ, ARCATOM_LANGUAGE=self.workspace.language
                    ),
                    check=False,
                )
            if result.returncode:
                self.workspace.notify(
                    tr("官方 Codex 已退出（{0}），已返回 AtomX。").format(
                        result.returncode
                    ),
                    severity="warning",
                )
        finally:
            self.workspace.native_active = False
        binary = self.workspace.client.binary
        await self.workspace.client.close()
        self.workspace.ready = False
        for saved in self.workspace.store.sessions.values():
            saved.resumed = False
        self.workspace.client = CodexClient(
            binary=binary, cwd=self.workspace.cwd
        )
        await self.workspace.connect()
        if tid and session is not None:
            try:
                # Drop cached history so official updates are reflected on return.
                # 清空历史缓存，以显示原生界面产生的更新。
                session.items.clear()
                session.hydrated = False
                await self.workspace.read_history(tid)
                await self.workspace.ensure_resumed(tid)
            except RpcError as exc:
                self.workspace.notify(str(exc), severity="warning")
                self.workspace.store.sessions.pop(tid, None)
                self.workspace.current = None
                self.workspace.show_home()
        self.workspace.transcript_cache.clear()
        self.workspace.transcript_signature = None
        await self.workspace.load_account()
        self.workspace.paint(force=True)

    async def choose(
        self,
        title: str,
        choices: list[tuple[str, str]],
        selected: str | None = None,
    ) -> str | None:
        """Return the selected option identifier. 返回所选选项的标识。"""
        return await self.workspace.push_screen_wait(
            Picker(title, choices, selected)
        )

    def command_notice(self, tid: str, text: str) -> None:
        """Append a local notice to the command thread. 在命令所属会话追加提示。"""
        self.workspace.store.get(tid).ingest(
            {"id": f"local-{time.time_ns()}", "type": "notice", "text": text}
        )
        self.workspace.store.revision += 1

    def show_text(self, title: str, value: Any) -> None:
        """Open a readable details dialog for command results. 打开可读的命令结果详情。"""
        from ..ui import Detail

        self.workspace.push_screen(
            Detail(
                title,
                Text(
                    clean(
                        value
                        if isinstance(value, str)
                        else json.dumps(value, ensure_ascii=False, indent=2)
                    )
                ),
            )
        )

    def apply_runtime(self, session: Session, response: dict) -> None:
        """Use effective server settings instead of guessing model defaults.

        使用服务端返回的有效设置，不猜测默认模型。
        """
        for key in (
            "model",
            "reasoningEffort",
            "serviceTier",
            "approvalPolicy",
            "approvalsReviewer",
            "sandbox",
            "activePermissionProfile",
            "collaborationMode",
            "cwd",
        ):
            if key in response:
                session.meta[key] = response[key]

    async def ensure_resumed(self, tid: str) -> Session:
        """Serialize resume and apply the current approval preference.

        串行恢复会话并应用审批偏好。
        """
        lock = self.workspace.resume_locks.setdefault(tid, asyncio.Lock())
        async with lock:
            session = self.workspace.store.get(tid)
            desired = bool(
                self.workspace.view_preferences.get("approve_for_me", True)
            )
            approval = approval_defaults(self.workspace.view_preferences)
            if not session.resumed:
                result = await self.workspace.client.call(
                    "thread/resume",
                    {"threadId": tid, "excludeTurns": True, **approval},
                )
                self.workspace.store.merge(result["thread"])
                self.workspace.apply_runtime(session, result)
                session.resumed = True
                session.approval_default_applied = desired
                self.workspace.store.revision += 1
            elif session.approval_default_applied != desired:
                await self.workspace.client.call(
                    "thread/settings/update", {"threadId": tid, **approval}
                )
                session.meta.update(approval)
                session.approval_default_applied = desired
                self.workspace.store.revision += 1
            return session

    async def update_settings(self, tid: str, **settings: Any) -> None:
        """Publish backend settings before updating local state. 后端成功后更新本地设置。"""
        session = await self.workspace.ensure_resumed(tid)
        await self.workspace.client.call(
            "thread/settings/update", {"threadId": tid, **settings}
        )
        for key, value in settings.items():
            session.meta[
                {"effort": "reasoningEffort", "sandboxPolicy": "sandbox"}.get(
                    key, key
                )
            ] = value
        self.workspace.store.revision += 1
        self.workspace.paint(force=True)

    async def model_catalog(self) -> list[dict]:
        """Collect visible model choices from every server page. 汇总服务端所有可见模型。"""
        models = []
        async for batch in self.workspace.client.pages(
            "model/list", {"includeHidden": False}
        ):
            models.extend(batch)
        return [model for model in models if not model.get("hidden")]

    async def select_model(
        self, tid: str, argument: str = "", effort_only: bool = False
    ) -> None:
        """Choose a model or effort and confirm it with the backend.

        选择模型或推理强度并同步。
        """
        session = await self.workspace.ensure_resumed(tid)
        models = await self.workspace.model_catalog()
        if not models:
            raise RpcError(tr("后端没有返回可选模型，请检查模型提供商配置。"))
        current = session.meta.get("model")
        if effort_only:
            model = next((m for m in models if m["model"] == current), None)
            if model is None:
                raise RpcError(
                    tr("当前模型不在后端目录中，请先用 /model 选择模型。")
                )
        else:
            target = argument.strip() or await self.workspace.choose(
                tr("选择模型 · 当前会话"),
                [
                    (
                        m["model"],
                        m.get("displayName", m["model"])
                        + "  "
                        + m.get("description", ""),
                    )
                    for m in models
                ],
                current,
            )
            if target is None:
                return
            model = next(
                (
                    m
                    for m in models
                    if m["model"] == target or m["id"] == target
                ),
                None,
            )
            if model is None:
                raise RpcError(tr("模型不在当前账户的可选目录中：") + target)
        efforts = model.get("supportedReasoningEfforts") or []
        effort = (
            session.meta.get("reasoningEffort")
            if model["model"] == current
            else model.get("defaultReasoningEffort")
        )
        if efforts:
            effort = await self.workspace.choose(
                tr("推理强度 · ") + model.get("displayName", model["model"]),
                [
                    (
                        e["reasoningEffort"],
                        e["reasoningEffort"] + "  " + e.get("description", ""),
                    )
                    for e in efforts
                ],
                effort or model.get("defaultReasoningEffort"),
            )
            if effort is None:
                return
        settings = {"model": model["model"]}
        if effort is not None:
            settings["effort"] = effort
        mode = session.meta.get("collaborationMode")
        if mode:
            settings["collaborationMode"] = {
                "mode": mode["mode"],
                "settings": {
                    **mode.get("settings", {}),
                    "model": model["model"],
                    "reasoning_effort": effort,
                },
            }
        if model["model"] != current:
            settings["serviceTier"] = model.get("defaultServiceTier")
        await self.workspace.update_settings(tid, **settings)
        self.workspace.command_notice(
            tid,
            tr("已选择 ")
            + model["model"]
            + (" · " + effort if effort else "")
            + tr("，下一回合生效。"),
        )

    async def send_command_prompt(self, tid: str, prompt: str) -> None:
        # Pass a captured prompt; never borrow another session's current draft.
        # 使用已捕获的提示词，不借用另一会话的当前草稿。
        """Send a command-generated prompt through normal submission.

        通过正常流程发送命令提示。
        """
        await self.workspace.send_prompt(tid, prompt_override=prompt)

    async def slash(self, text: str) -> None:
        """Parse and dispatch a supported slash command. 解析并分发支持的斜杠命令。"""
        from ..ui import Composer

        head, _, argument = text.strip().partition(" ")
        name = head[1:]
        command = BY_NAME.get(name)
        if command is None:
            self.workspace.notify(
                tr("没有这个命令；输入 / 可搜索全部命令。"), severity="warning"
            )
            return
        tid = self.workspace.current
        if self.workspace.command_busy:
            self.workspace.notify(tr("上一条命令正在处理，当前输入已保留。"))
            return
        self.workspace.command_busy = True
        composer = self.workspace.query_one(Composer)
        composer.clear()
        self.workspace.hide_commands()
        try:
            if not self.workspace.ready and name not in (
                "help",
                "exit",
                "quit",
                "warnings",
                "theme",
                "settings",
                "palette",
                "login",
            ):
                raise RpcError(tr("尚未连接 Codex，请先 Ctrl+R 重连。"))
            await self.workspace.dispatch_command(name, argument.strip(), tid)
        except Exception:
            if self.workspace.current == tid and not composer.text:
                composer.load_text(text)
            elif tid and not self.workspace.store.get(tid).draft:
                self.workspace.store.get(tid).draft = text
            raise
        finally:
            self.workspace.command_busy = False
            self.workspace.store.revision += 1
            if self.workspace.screen is self.workspace.main_screen:
                (
                    composer
                    if self.workspace.current
                    else self.workspace.query_one("#search")
                ).focus()

    async def dispatch_command(
        self, name: str, argument: str, tid: str | None
    ) -> None:
        """Dispatch global commands before requiring a session. 先处理全局命令。"""
        if BY_NAME[name].native:
            await self.workspace.native_command(
                "/" + name + (" " + argument if argument else ""), tid
            )
            return
        if await self.dispatch_global(name, argument, tid):
            return
        if tid is None:
            raise RpcError(tr("请先打开一个会话。"))
        session = self.workspace.store.get(tid)
        value: str | None
        if name in ("model", "reasoning"):
            await self.workspace.select_model(
                tid, argument, name == "reasoning"
            )
        elif name in ("agent", "subagents"):
            await self.workspace.refresh_activity(tid)
            target = await self.workspace.choose(
                tr("当前会话的子代理"),
                [
                    (i, s.get("name") or self.workspace.store.get(i).title)
                    for i, s in session.agents.items()
                ],
            )
            if target:
                await self.workspace.open_session(target)
        elif name == "status":
            await self.workspace.ensure_resumed(tid)
            self.workspace.show_text(
                tr("当前会话"),
                {"threadId": tid, **session.meta, "tokenUsage": session.usage},
            )
        elif name == "rename":
            title: str | None = (
                argument
                or await self.workspace.push_screen_wait(
                    Prompt(tr("会话名称"), session.title)
                )
            )
            if title:
                await self.workspace.client.call(
                    "thread/name/set", {"threadId": tid, "name": title}
                )
                session.meta["name"] = title
        elif name in ("pwd", "cwd", "rollout"):
            self.workspace.show_text(
                tr("会话路径"),
                session.meta.get("path" if name == "rollout" else "cwd")
                or tr("后端未提供路径"),
            )
        elif name == "cd":
            value = argument or await self.workspace.push_screen_wait(
                Prompt(
                    tr("工作目录"), session.meta.get("cwd", self.workspace.cwd)
                )
            )
            if value:
                path = Path(value).expanduser()
                if not path.is_absolute():
                    path = (
                        Path(session.meta.get("cwd", self.workspace.cwd)) / path
                    )
                path = path.resolve()
                if not path.is_dir():
                    raise RpcError(tr("目录不存在：") + str(path))
                await self.workspace.update_settings(tid, cwd=str(path))
                self.workspace.command_notice(
                    tid, tr("工作目录已更改：") + str(path)
                )
        elif name == "permissions":
            profiles: list[dict] = []
            async for page in self.workspace.client.pages(
                "permissionProfile/list",
                {"cwd": session.meta.get("cwd", self.workspace.cwd)},
            ):
                profiles.extend(page)
            choice = await self.workspace.choose(
                tr("会话权限 · 仅列出后端允许的配置"),
                [
                    (p["id"], p["id"] + "  " + (p.get("description") or ""))
                    for p in profiles
                    if p.get("allowed")
                ],
            )
            if choice:
                await self.workspace.update_settings(tid, permissions=choice)
                self.workspace.command_notice(
                    tid, tr("权限配置已更新：") + choice
                )
        elif name == "plan":
            await self.workspace.ensure_resumed(tid)
            active = session.meta.get("collaborationMode") or {}
            mode = (
                "default"
                if active.get("mode") == "plan" and not argument
                else "plan"
            )
            await self.workspace.update_settings(
                tid,
                collaborationMode={
                    "mode": mode,
                    "settings": {
                        "model": session.meta["model"],
                        "reasoning_effort": session.meta.get("reasoningEffort"),
                        "developer_instructions": None,
                    },
                },
            )
            self.workspace.command_notice(
                tid,
                tr("已切换到")
                + (tr("计划模式") if mode == "plan" else tr("执行模式")),
            )
            if argument:
                await self.workspace.send_command_prompt(tid, argument)
        elif name == "fast":
            await self.workspace.ensure_resumed(tid)
            model = next(
                (
                    m
                    for m in await self.workspace.model_catalog()
                    if m["model"] == session.meta.get("model")
                ),
                {},
            )
            tiers = model.get("serviceTiers") or []
            fast = next(
                (
                    t
                    for t in tiers
                    if t["id"] in ("fast", "priority")
                    or t.get("name", "").lower() == "fast"
                ),
                None,
            )
            if not fast:
                raise RpcError(
                    tr(
                        "当前模型未提供 Fast 档位，请在 /model 中选择提供该档位的模型。"
                    )
                )
            value = (
                None
                if session.meta.get("serviceTier") == fast["id"]
                else fast["id"]
            )
            await self.workspace.update_settings(tid, serviceTier=value)
            self.workspace.command_notice(
                tid, "Fast " + (tr("已开启") if value else tr("已关闭"))
            )
        elif name in ("compact", "review"):
            await self.workspace.ensure_resumed(tid)
            if session.active_turn or tid in self.workspace.sending:
                raise RpcError(
                    tr("请等待当前回合结束，或按 Esc 停止后再执行。")
                )
            review_target = (
                {"type": "custom", "instructions": argument}
                if argument
                else {"type": "uncommittedChanges"}
            )
            if name == "review" and not argument:
                kind = await self.workspace.choose(
                    tr("审查范围"),
                    [
                        ("uncommittedChanges", tr("当前工作区修改")),
                        ("baseBranch", tr("与指定分支比较")),
                        ("commit", tr("指定提交")),
                    ],
                )
                if kind is None:
                    return
                review_target = {"type": kind}
                if kind != "uncommittedChanges":
                    value = await self.workspace.push_screen_wait(
                        Prompt(
                            tr("分支名称")
                            if kind == "baseBranch"
                            else tr("提交 SHA")
                        )
                    )
                    if not value:
                        return
                    review_target[
                        "branch" if kind == "baseBranch" else "sha"
                    ] = value
            session.busy_since = time.monotonic()
            session.phase = (
                tr("正在压缩上下文")
                if name == "compact"
                else tr("正在审查代码")
            )
            try:
                await self.workspace.client.call(
                    "thread/compact/start"
                    if name == "compact"
                    else "review/start",
                    {"threadId": tid}
                    if name == "compact"
                    else {"threadId": tid, "review_target": review_target},
                )
            except Exception:
                session.busy_since = None
                raise
        elif name in ("fork", "side", "btw"):
            if session.active_turn or tid in self.workspace.sending:
                raise RpcError(tr("当前回合结束后才能分叉会话。"))
            result = await self.workspace.client.call(
                "thread/fork",
                {
                    "threadId": tid,
                    "ephemeral": name != "fork",
                    "excludeTurns": True,
                },
            )
            child = self.workspace.store.merge(result["thread"])
            self.workspace.apply_runtime(child, result)
            child.resumed = True
            await self.workspace.open_session(child.id)
            if argument:
                await self.workspace.send_command_prompt(child.id, argument)
        elif name == "goal":
            await self.workspace.ensure_resumed(tid)
            if argument == "clear":
                result = await self.workspace.client.call(
                    "thread/goal/clear", {"threadId": tid}
                )
            elif argument in ("pause", "resume"):
                result = await self.workspace.client.call(
                    "thread/goal/set",
                    {
                        "threadId": tid,
                        "status": "paused" if argument == "pause" else "active",
                    },
                )
            elif argument:
                result = await self.workspace.client.call(
                    "thread/goal/set", {"threadId": tid, "objective": argument}
                )
            else:
                result = await self.workspace.client.call(
                    "thread/goal/get", {"threadId": tid}
                )
            self.workspace.show_text(
                tr("任务目标 · /goal 目标 · pause / resume / clear"), result
            )
        elif name in ("archive", "delete"):
            if session.active_turn or tid in self.workspace.sending:
                raise RpcError(tr("请先按 Esc 停止当前任务。"))
            await self.workspace.client.call(
                "thread/" + name, {"threadId": tid}
            )
            if name == "delete":
                self.workspace.store.remove_thread(tid)
            else:
                session.meta["archived"] = True
                session.resumed = False
            self.workspace.current = None
            self.workspace.show_home()
            self.workspace.notify(
                tr("会话已") + (tr("删除") if name == "delete" else tr("归档"))
            )
        elif name == "ps":
            self.workspace.action_activity()
        elif name in ("stop", "clean"):
            await self.workspace.ensure_resumed(tid)
            await self.workspace.client.call(
                "thread/backgroundTerminals/clean", {"threadId": tid}
            )
            await self.workspace.refresh_activity(tid)
            self.workspace.command_notice(
                tid, tr("已停止当前会话的后台终端。Esc 可停止模型回合。")
            )
        elif name == "skills":
            await self.workspace.select_skill(session)
        elif name == "debug-config":
            result = await self.workspace.client.call(
                "config/read",
                {
                    "cwd": session.meta.get("cwd", self.workspace.cwd),
                    "includeLayers": True,
                },
            )
            self.workspace.show_text(tr("配置来源"), redact(result))
        elif name in ("recap", "init"):
            await self.workspace.send_command_prompt(
                tid,
                tr("请总结当前会话的目标、已完成工作、关键决策和待办事项。")
                if name == "recap"
                else tr(
                    (
                        "请检查当前项目并创建适用的 AGENTS.md，记录项目结构、构建测试命"
                        "令和开发约定；如果文件已存在，先阅读并保留已有有效规则。"
                    )
                ),
            )
        elif name == "copy":
            text = next(
                (
                    i.get("text", "")
                    for i in reversed(list(session.items.values()))
                    if i.get("type") in ("agentMessage", "plan")
                    and i.get("id") not in session.streaming_items
                ),
                "",
            )
            if not text:
                raise RpcError(tr("当前会话还没有完整回复可复制。"))
            self.workspace.copy_to_clipboard(text)
        elif name == "export":
            await self.workspace.export_chat(session, argument)
        elif name == "diff":
            await self.workspace.git_diff(session)
        elif name == "mention":
            await self.workspace.mention_file(session, argument)
        elif name == "raw":
            self.workspace.raw_transcript = not self.workspace.raw_transcript
            self.workspace.transcript_signature = None
            self.workspace.transcript_cache.clear()
            self.workspace.command_notice(
                tid,
                tr("纯文本显示")
                + (
                    tr("已开启")
                    if self.workspace.raw_transcript
                    else tr("已关闭")
                ),
            )

    async def dispatch_global(
        self, name: str, argument: str, tid: str | None
    ) -> bool:
        """Run commands usable without an open thread. 执行无需会话的命令。"""
        from ..dialogs import NewSession

        session = self.workspace.store.get(tid) if tid else None
        value: str | None
        if name in ("settings", "palette"):
            self.workspace.action_settings()
        elif name == "login":
            await self.workspace.check_auth(prompt=False)
            if self.workspace.auth_needed:
                self.workspace.action_login()
            else:
                self.workspace.notify(tr("Codex 已登录。"))
        elif name in ("quit", "exit"):
            self.workspace.action_request_quit()
        elif name in ("new", "clear"):
            cwd = await self.workspace.push_screen_wait(
                NewSession(
                    self.workspace.view_preferences.get("default_cwd")
                    or (
                        session.meta.get("cwd", self.workspace.cwd)
                        if session
                        else self.workspace.cwd
                    )
                )
            )
            if cwd:
                await self.workspace.create_session(cwd)
                if argument and self.workspace.current:
                    await self.workspace.send_command_prompt(
                        self.workspace.current, argument
                    )
        elif name in ("resume", "agents"):
            if argument:
                await self.workspace.open_session(argument)
            else:
                self.workspace.show_home()
        elif name == "usage":
            if argument:
                await self.workspace.native_command("/usage " + argument, tid)
            else:
                await self.workspace.load_account()
                self.workspace.action_usage()
        elif name == "mcp":
            data = []
            async for page in self.workspace.client.pages(
                "mcpServerStatus/list", {}
            ):
                data.extend(page)
            self.workspace.show_text(
                tr("MCP 服务与工具"), data or tr("没有配置 MCP 服务")
            )
        elif name == "warnings":
            self.workspace.show_text(
                tr("诊断信息"),
                "\n".join(
                    self.workspace.store.account_errors
                    + list(getattr(self.workspace.client, "stderr", []))
                )
                or tr("当前没有诊断警告。"),
            )
        elif name == "theme":
            from pygments.styles import get_all_styles

            value = await self.workspace.choose(
                tr("代码语法高亮主题"),
                [(s, s) for s in sorted(get_all_styles())],
                self.workspace.code_theme,
            )
            if value:
                self.workspace.code_theme = value
                self.workspace.view_preferences["code_theme"] = value
                self.workspace.transcript_cache.clear()
                self.workspace.transcript_signature = None
                await self.workspace.save_view_preferences()
        elif name == "statusline":
            value = await self.workspace.push_screen_wait(
                Prompt(
                    tr(
                        "状态栏顺序 · time tokens context limits model · 留空字段会隐藏"
                    ),
                    " ".join(self.workspace.status_fields),
                )
            )
            if value:
                fields = value.split()
                if len(set(fields)) != len(fields) or any(
                    f not in ("time", "tokens", "context", "limits", "model")
                    for f in fields
                ):
                    raise RpcError(
                        tr(
                            "字段应为 time tokens context limits model，不可重复。"
                        )
                    )
                self.workspace.status_fields = fields
                self.workspace.view_preferences["status_fields"] = fields
                await self.workspace.save_view_preferences()
        elif name == "title":
            value = await self.workspace.choose(
                tr("终端标题"),
                [
                    ("app", "AtomX"),
                    ("session", tr("会话名称")),
                    ("model", tr("模型 · 会话名称")),
                ],
            )
            if value:
                self.workspace.view_preferences["title"] = value
                await self.workspace.save_view_preferences()
        elif name == "help":
            self.workspace.show_text(
                tr("命令与快捷键"),
                tr(
                    (
                        "输入 / 显示提示；↑↓ 选择，Tab 补全，Enter 执行，Esc "
                        "收起。\n← 空输入返回列表；连按两次 Ctrl+X 删除选中历史；Esc"
                        " 停止回合；Ctrl+C 复制。\n带‘原生’的命令会打开官方 Codex"
                        "，退出后回到 AtomX。\n\n"
                    )
                )
                + "\n".join(
                    f"/{c.name:24} {tr(c.description)}"
                    + (tr("  [原生]") if c.native else "")
                    for c in COMMANDS
                ),
            )
        else:
            return False
        return True

    async def select_skill(self, session: Session) -> None:
        """Refresh available skills and attach the chosen instructions.

        更新技能列表并应用所选技能。
        """
        from ..ui import Composer

        skills = [] if self.workspace.demo else discover_skills()
        result = await self.workspace.client.call(
            "skills/list",
            {
                "cwds": [session.meta.get("cwd", self.workspace.cwd)],
                "forceReload": True,
            },
        )
        for entry in result.get("data", []):
            for skill in entry.get("skills", []):
                if skill.get("enabled", True) and not any(
                    s.name == skill["name"] for s in skills
                ):
                    skills.append(
                        PersonalSkill(
                            skill["name"],
                            skill.get("description", ""),
                            Path(skill["path"]),
                        )
                    )
        self.workspace.personal_skills = skills
        self.workspace.personal_instructions = (
            "" if self.workspace.demo else bridge_instructions(skills)
        )
        target = await self.workspace.choose(
            tr("Skills · 选择后输入任务"),
            [(s.name, f"${s.name}  {s.description}\n{s.path}") for s in skills],
        )
        if target:
            self.workspace.query_one(Composer).load_text("$" + target + " ")
            self.workspace.query_one(Composer).focus()

    async def export_chat(self, session: Session, argument: str) -> None:
        """Write the chosen session transcript to an explicit path.

        导出指定会话记录到明确路径。
        """
        default = str(
            Path(session.meta.get("cwd", self.workspace.cwd))
            / ("codex-" + session.id[:8] + ".md")
        )
        value: str | None = argument or await self.workspace.push_screen_wait(
            Prompt(tr("导出 Markdown 文件路径"), default)
        )
        if not value:
            return
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(session.meta.get("cwd", self.workspace.cwd)) / path
        body = ["# " + session.title]
        for item in session.items.values():
            if item.get("type") == "userMessage":
                body.append(
                    "## User\n\n"
                    + "\n".join(
                        c.get("text", "") for c in item.get("content", [])
                    )
                )
            elif item.get("type") in ("agentMessage", "plan"):
                body.append("## Codex\n\n" + item.get("text", ""))

        def write() -> None:
            # Exclusive create: never overwrite a user file silently.
            # 仅创建新文件，已有文件不静默覆盖。
            """Perform the requested filesystem or clipboard write.

            执行已请求的文件或剪贴板写入。
            """
            with path.open("x") as output:
                output.write("\n\n".join(body) + "\n")

        await asyncio.to_thread(write)
        self.workspace.command_notice(session.id, tr("已导出：") + str(path))

    async def git_diff(self, session: Session) -> None:
        """Show working-tree differences without mutating the repository.

        只读显示仓库差异。
        """
        cwd = session.meta.get("cwd", self.workspace.cwd)

        async def git(*args: str, allowed: tuple[int, ...] = (0,)) -> bytes:
            """Run a read-only Git command with accepted exit codes.

            执行只读 Git 命令并检查退出码。
            """
            process = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            if process.returncode not in allowed:
                raise RpcError(stderr.decode(errors="replace"))
            return stdout

        diff = await git(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
            allowed=(0, 128),
        )
        if not diff:
            diff = await git("diff", "--no-ext-diff", "--no-textconv", "--")
            diff += await git(
                "diff", "--cached", "--no-ext-diff", "--no-textconv", "--"
            )
        untracked = await git(
            "ls-files", "--others", "--exclude-standard", "-z"
        )
        for filename in untracked.split(b"\0"):
            if not filename:
                continue
            diff += await git(
                "diff",
                "--no-index",
                "--no-ext-diff",
                "--no-textconv",
                "--",
                "/dev/null",
                os.fsdecode(filename),
                allowed=(0, 1),
            )
            if len(diff) > 2_000_000:
                diff += b"\n[Diff truncated at 2 MB]\n"
                break
        self.workspace.show_text(
            tr("Git 修改"),
            diff.decode(errors="replace") or tr("工作区没有修改。"),
        )

    async def mention_file(self, session: Session, argument: str) -> None:
        """Choose a project file and append its path to the draft.

        选择项目文件并追加到草稿。
        """
        from ..ui import Composer

        root = Path(session.meta.get("cwd", self.workspace.cwd))
        if argument:
            path = Path(argument).expanduser()
            path = path if path.is_absolute() else root / path
            if not path.exists():
                raise RpcError(tr("路径不存在：") + str(path))
            value = str(path)
        else:

            def scan() -> list[str]:
                """List mentionable files within the current project.

                列出项目中可引用的文件。
                """
                files: list[str] = []
                for parent, dirs, names in os.walk(root):
                    dirs[:] = [
                        d
                        for d in dirs
                        if not d.startswith(".")
                        and d not in ("node_modules", "__pycache__", "target")
                    ]
                    files.extend(
                        str((Path(parent) / n).relative_to(root))
                        for n in names
                        if not n.startswith(".")
                    )
                    if len(files) >= 1500:
                        break
                return sorted(files[:1500])

            paths = await asyncio.to_thread(scan)
            selected = await self.workspace.choose(
                tr("选择文件 · 输入筛选"), [(p, p) for p in paths]
            )
            if not selected:
                return
            value = str(root / selected)
        self.workspace.query_one(Composer).load_text(
            tr("请查看文件 ") + json.dumps(value, ensure_ascii=False) + " "
        )


def redact(value: Any) -> Any:
    """Mask credentials in configuration diagnostics. 诊断中隐藏认证字段。"""
    if isinstance(value, dict):
        return {
            k: "[redacted]"
            if any(
                part in k.lower()
                for part in (
                    "secret",
                    "password",
                    "token",
                    "api_key",
                    "authorization",
                    "headers",
                    "env",
                )
            )
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value

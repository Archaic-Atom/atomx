import asyncio
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from textual.widgets import ContentSwitcher, Input, OptionList
from textual.selection import Selection
from rich.console import Console
from rich.cells import cell_len
from arcatom_codex.demo import DemoClient
from arcatom_codex.state import (
    Store, rollout_usage, clean, note_agent_timing, set_agent_name,
)
from arcatom_codex.dialogs import ActivityDetail
from arcatom_codex.ui import ArcatomApp, Composer, Approval, Detail


class StateTests(unittest.TestCase):
    def test_agent_elapsed_freezes_and_resets_for_new_turn(self):
        """Use one measured interval per root turn. 每个主回合独立计时。"""
        agent = {"turnId": "first", "status": "running"}
        note_agent_timing(agent, 100.0)
        self.assertEqual(agent["startedAt"], 100.0)
        agent["status"] = "completed"
        with patch("arcatom_codex.state.time.time", return_value=160.0):
            note_agent_timing(agent)
        self.assertEqual(agent["finishedAt"], 160.0)
        agent.update({"turnId": "second", "status": "running"})
        note_agent_timing(agent, 200.0)
        self.assertEqual(agent["startedAt"], 200.0)
        self.assertNotIn("finishedAt", agent)

    def test_agent_name_does_not_cycle_between_events_and_polling(self):
        """Preserve a task path over metadata names. 路径名称不被轮询覆盖。"""
        store = Store()
        store.event("turn/started", {
            "threadId": "root", "turn": {"id": "turn"},
        })
        store.event("item/started", {"threadId": "root", "item": {
            "id": "start", "type": "subAgentActivity", "kind": "started",
            "agentThreadId": "child", "agentPath": "audit/agent_tree",
        }})
        agent = store.get("root").agents["child"]
        self.assertEqual(agent["name"], "audit/agent_tree")
        for _ in range(3):
            store.merge({
                "id": "child", "name": "Temporary title",
                "agentNickname": "Autumn", "agentRole": "worker",
                "parentThreadId": "root", "status": {"type": "active"},
            })
            set_agent_name(agent, "Autumn", 3)
            self.assertEqual(agent["name"], "audit/agent_tree")

    def test_agent_name_upgrades_once_when_specific_name_arrives(self):
        """Use a later explicit name without returning to a role.

        明确名称到达后升级一次，不回退到通用角色。
        """
        store = Store()
        store.merge({
            "id": "child", "name": "Temporary title",
            "agentRole": "worker", "parentThreadId": "root",
        })
        agent = store.get("root").agents["child"]
        self.assertEqual(agent["name"], "worker")
        store.merge({
            "id": "child", "name": "Temporary title",
            "agentNickname": "Autumn", "parentThreadId": "root",
        })
        self.assertEqual(agent["name"], "Autumn")
        store.merge({
            "id": "child", "name": "Temporary title",
            "agentRole": "worker", "parentThreadId": "root",
        })
        self.assertEqual(agent["name"], "Autumn")

    def test_stream_final_replaces_delta_and_is_thread_scoped(self):
        store = Store()
        for tid, text in [("a", "hello"), ("b", "world")]:
            store.event("item/agentMessage/delta", {"threadId": tid, "itemId": "same", "delta": text})
        store.event("item/completed", {"threadId": "a", "item": {"id": "same", "type": "agentMessage", "text": "hello!"}})
        self.assertEqual(store.get("a").items["same"]["text"], "hello!")
        self.assertEqual(store.get("b").items["same"]["text"], "world")

    def test_usage_is_latest_snapshot_not_sum_and_rejects_outside_home(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            log = home / "rollout.jsonl"
            lines = [json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"total_tokens": n, "input_tokens": n-10, "output_tokens": 10, "cached_input_tokens": 5}
            }}}) for n in [20, 30, 30]]
            log.write_text("\n".join(lines) + '\n{"type":')
            result = rollout_usage(str(log), home)
            self.assertEqual(result["total"]["totalTokens"], 30)
            self.assertEqual(result["total"]["cachedInputTokens"], 5)
            self.assertEqual(rollout_usage(str(log), home / "elsewhere"), {})

    def test_children_not_in_root_list(self):
        store = Store()
        store.merge({"id": "parent", "name": "Main", "updatedAt": 1})
        store.merge({"id": "child", "name": "Worker", "parentThreadId": "parent"})
        self.assertEqual([s.id for s in store.roots()], ["parent"])
        self.assertIn("child", store.get("parent").agents)

    def test_agents_are_scoped_to_the_latest_turn(self):
        """Keep older children in history, but show only the newest group.

        旧代理保留记录，活动面板只显示最近一轮。
        """
        store = Store()
        turns = []
        for turn_id, count in (("first", 3), ("second", 4)):
            turns.append({"id": turn_id, "status": "completed", "items": [
                {
                    "id": f"spawn-{turn_id}-{index}",
                    "type": "collabAgentToolCall", "tool": "spawnAgent",
                    "receiverThreadIds": [f"{turn_id}-{index}"],
                    "agentsStates": {
                        f"{turn_id}-{index}": {"status": "completed"}
                    },
                }
                for index in range(count)
            ]})
        session = store.merge({"id": "root", "turns": turns})
        self.assertEqual(len(session.agents), 7)
        self.assertEqual(len(session.visible_agents()), 4)
        self.assertTrue(all(
            agent["turnId"] == "second"
            for agent in session.visible_agents().values()
        ))

    def test_unknown_old_agent_is_not_assigned_to_new_turn(self):
        """Require an item-to-turn link before showing a resumed child.

        恢复时缺少回合证据的旧代理不混入新回合。
        """
        store = Store()
        root = store.merge({"id": "root"})
        root.agents["old"] = {"id": "old", "status": "running"}
        self.assertFalse(root.visible_agents())
        store.event("turn/started", {
            "threadId": "root", "turn": {"id": "new-turn"},
        })
        store.merge({
            "id": "old", "parentThreadId": "root",
            "status": {"type": "active"},
        })
        self.assertFalse(root.visible_agents())
        store.event("item/started", {"threadId": "root", "item": {
            "id": "new-spawn", "type": "collabAgentToolCall",
            "tool": "spawnAgent", "receiverThreadIds": ["new"],
            "agentsStates": {"new": {"status": "running"}},
        }})
        self.assertEqual(list(root.visible_agents()), ["new"])

    def test_reused_agent_belongs_to_second_turn(self):
        """A later sendInput makes an existing child part of the new turn.

        后续回合复用子代理时更新其显示归属。
        """
        store = Store()
        store.event("turn/started", {
            "threadId": "root", "turn": {"id": "first"},
        })
        store.event("item/started", {"threadId": "root", "item": {
            "id": "spawn", "type": "collabAgentToolCall",
            "tool": "spawnAgent", "receiverThreadIds": ["child"],
        }})
        store.event("turn/completed", {"threadId": "root", "turn": {
            "id": "first", "status": "completed",
        }})
        store.event("turn/started", {
            "threadId": "root", "turn": {"id": "second"},
        })
        store.event("item/started", {"threadId": "root", "item": {
            "id": "reuse", "type": "collabAgentToolCall",
            "tool": "sendInput", "receiverThreadIds": ["child"],
            "agentsStates": {"child": {"status": "running"}},
        }})
        self.assertEqual(store.get("root").agents["child"]["turnId"], "second")
        self.assertEqual(list(store.get("root").visible_agents()), ["child"])

    def test_nested_agent_turn_uses_root_even_when_spawn_arrives_late(self):
        """Do not copy a child's own turn ID into the root's tree.

        父代理创建事件迟到时，孙代理仍归属主回合。
        """
        store = Store()
        store.event("turn/started", {
            "threadId": "root", "turn": {"id": "root-turn"},
        })
        store.merge({"id": "child", "parentThreadId": "root"})
        store.event("turn/started", {
            "threadId": "child", "turn": {"id": "child-turn"},
        })
        store.event("item/started", {"threadId": "child", "item": {
            "id": "grand-spawn", "type": "collabAgentToolCall",
            "tool": "spawnAgent", "receiverThreadIds": ["grand"],
        }})
        store.event("item/started", {"threadId": "root", "item": {
            "id": "child-spawn", "type": "collabAgentToolCall",
            "tool": "spawnAgent", "receiverThreadIds": ["child"],
        }})
        self.assertEqual(
            store.get("root").agents["grand"]["turnId"], "root-turn"
        )
        self.assertEqual(
            set(store.get("root").visible_agents()), {"grand", "child"}
        )

    def test_delayed_active_status_does_not_revive_completed_child(self):
        """Only a new turn may reactivate a completed agent.

        迟到的 active 状态不能复活已完成代理。
        """
        store = Store()
        store.merge({"id": "root"})
        store.merge({"id": "child", "parentThreadId": "root"})
        store.event("turn/completed", {"threadId": "child", "turn": {
            "id": "child-turn", "status": "completed",
        }})
        store.event("thread/status/changed", {
            "threadId": "child", "status": {"type": "active"},
        })
        self.assertEqual(store.get("root").agents["child"]["status"], "completed")
        store.event("turn/started", {
            "threadId": "child", "turn": {"id": "new-child-turn"},
        })
        self.assertEqual(store.get("root").agents["child"]["status"], "running")

    def test_nested_collaboration_is_visible_from_root(self):
        """Propagate descendant activity to the root. 根会话显示孙级代理。"""
        store = Store()
        store.merge({"id": "root", "name": "Main"})
        store.merge({"id": "child", "parentThreadId": "root"})
        store.event("item/started", {"threadId": "child", "item": {
            "id": "spawn-grandchild", "type": "collabAgentToolCall",
            "tool": "spawnAgent", "receiverThreadIds": ["grandchild"],
            "agentsStates": {"grandchild": {"status": "running"}},
        }})
        self.assertEqual(store.get("root").agents["grandchild"]["status"], "running")
        self.assertEqual(store.get("root").agents["grandchild"]["parentId"], "child")
        store.merge({"id": "grandchild", "parentThreadId": "child"})
        store.event("item/started", {"threadId": "grandchild", "item": {
            "id": "job", "type": "commandExecution",
            "command": "python train.py\n--epochs 5", "status": "inProgress",
        }})
        self.assertEqual(
            store.get("root").agents["grandchild"]["activity"], "python train.py"
        )
        store.event("item/completed", {"threadId": "grandchild", "item": {
            "id": "job", "type": "commandExecution",
            "command": "python train.py", "status": "completed",
        }})
        self.assertNotIn("activity", store.get("root").agents["grandchild"])

    def test_terminal_sequences_removed(self):
        self.assertEqual(clean("\x1b[31mtext\x1b[0m\x07"), "text")


class UiTests(unittest.IsolatedAsyncioTestCase):
    def app(self):
        client = DemoClient()
        return ArcatomApp(cwd=tempfile.gettempdir(), client=client, demo=True), client

    async def test_agent_columns_align_across_nested_levels(self):
        """Align name, state, usage and time through deep nesting.

        深至四级代理时，各列仍按终端字符宽度对齐。
        """
        app, _client = self.app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-login")
            await pilot.pause(.2)
            app.store.event("turn/started", {
                "threadId": "demo-login", "turn": {"id": "aligned-turn"},
            })
            app.store.event("item/started", {"threadId": "demo-login", "item": {
                "id": "aligned-spawn", "type": "collabAgentToolCall",
                "tool": "spawnAgent", "receiverThreadIds": ["short", "long"],
                "agentsStates": {
                    "short": {"status": "running"},
                    "long": {"status": "completed"},
                },
            }})
            root = app.store.get("demo-login")
            set_agent_name(root.agents["short"], "短", 4)
            set_agent_name(root.agents["long"], "long_agent_name", 4)
            parent = "short"
            for depth in range(2, 5):
                child_id = f"depth-{depth}"
                root.agents[child_id] = {
                    "id": child_id, "parentId": parent,
                    "turnId": "aligned-turn", "status": "running",
                    "name": f"nested-level-{depth}",
                }
                parent = child_id
            now = time.time()
            root.agents["short"]["startedAt"] = now - 75
            root.agents["long"]["startedAt"] = now - 85
            root.agents["long"]["finishedAt"] = now - 25
            app.store.get("short").usage = {"total": {"totalTokens": 2300}}
            app.store.get("long").usage = {"total": {"totalTokens": 456}}
            app.paint(force=True)
            panel = app.query_one("#activities", OptionList)
            rows = [panel.get_option(f"a-{tid}").prompt.plain
                    for tid in ("short", "long", "depth-2", "depth-3", "depth-4")]
            separators = [
                [cell_len(row[:index]) for index, char in enumerate(row)
                 if char == "│"]
                for row in rows
            ]
            self.assertTrue(all(points == separators[0] for points in separators))
            self.assertEqual(len(separators[0]), 3)
            self.assertIn("nested-level-4", rows[-1])
            self.assertIn("2.3k tokens", rows[0])
            self.assertIn("01:00", rows[1])
            panel.highlighted = next(
                index for index in range(panel.option_count)
                if panel.get_option_at_index(index).id == "a-depth-4"
            )
            selected_id = panel.get_option_at_index(panel.highlighted).id
            await pilot.resize_terminal(80, 34)
            await pilot.pause(.4)
            narrow_rows = [panel.get_option(f"a-{tid}").prompt.plain
                           for tid in ("short", "long", "depth-2", "depth-3", "depth-4")]
            narrow_separators = [
                [cell_len(row[:index]) for index, char in enumerate(row)
                 if char == "│"]
                for row in narrow_rows
            ]
            self.assertTrue(all(
                points == narrow_separators[0] for points in narrow_separators
            ))
            self.assertIn("nested-level-4", narrow_rows[-1])
            self.assertNotEqual(separators[0], narrow_separators[0])
            self.assertEqual(panel.get_option_at_index(panel.highlighted).id, selected_id)
            await pilot.resize_terminal(110, 34)
            await pilot.pause(.4)
            restored = panel.get_option("a-depth-4").prompt.plain
            restored_separators = [
                cell_len(restored[:index]) for index, char in enumerate(restored)
                if char == "│"
            ]
            self.assertEqual(restored_separators, separators[0])
            self.assertEqual(panel.get_option_at_index(panel.highlighted).id, selected_id)

    async def test_empty_left_and_draft_preservation(self):
        app, client = self.app()
        async with app.run_test(size=(110, 36)) as pilot:
            await pilot.pause(0.3)
            app.query_one("#sessions").focus()
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertEqual(app.current, "demo-login")
            composer = app.query_one(Composer)
            await pilot.press("h", "i", "left")
            self.assertEqual(app.current, "demo-login")
            self.assertEqual(composer.text, "hi")
            await pilot.press("escape")
            self.assertIsNotNone(app.current)
            await pilot.press("escape")
            self.assertIsNone(app.current)
            app.query_one("#sessions").focus()
            await pilot.press("enter")
            await pilot.pause(0.2)
            self.assertEqual(composer.text, "hi")
            composer.clear()
            await pilot.press("left")
            self.assertIsNone(app.current)
            self.assertEqual(app.query_one(ContentSwitcher).current, "home")

    async def test_search_arrow_and_multiline_stream(self):
        app, client = self.app()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("up", "up", "enter")
            await pilot.pause(0.2)
            self.assertEqual(app.current, "demo-dashboard")
            await pilot.press("a", "ctrl+j", "b", "enter")
            await pilot.pause(1)
            sent = [p for m, p in client.calls if m == "turn/start"]
            self.assertEqual(sent[0]["input"][0]["text"], "a\nb")
            self.assertEqual(app.query_one(Composer).text, "")
            self.assertEqual(app.store.get("demo-dashboard").total, 28100)
            self.assertIsNone(app.store.get("demo-dashboard").active_turn)

    async def test_activity_modal_and_usage_with_background_events(self):
        app, client = self.app()
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause(0.2)
            app.query_one("#sessions").focus()
            await pilot.press("enter")
            await pilot.pause(0.3)
            await pilot.press("ctrl+t")
            self.assertTrue(app.query_one("#activities").display)
            self.assertGreaterEqual(app.query_one("#activities", OptionList).option_count, 3)
            await pilot.press("enter")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, ActivityDetail)
            await client.events.put({"method": "thread/tokenUsage/updated", "params": {
                "threadId": "demo-login", "tokenUsage": {"total": {"totalTokens": 99}}}})
            await pilot.pause(0.2)
            await pilot.press("escape", "ctrl+u")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, Detail)
            self.assertEqual(app.store.get("demo-login").total, 99)

    async def test_live_agents_and_tools_open_activity_panel(self):
        """Show live collaboration without a hidden shortcut. 实时代理自动显示。"""
        app, _client = self.app()
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-login")
            await pilot.pause(.2)
            for agent in app.store.get("demo-login").agents.values():
                agent["status"] = "completed"
            app.store.event("turn/started", {"threadId": "demo-login", "turn": {
                "id": "live-turn"
            }})
            app.store.event("item/started", {"threadId": "demo-login", "item": {
                "id": "live-spawn", "type": "collabAgentToolCall",
                "tool": "spawnAgent", "status": "inProgress",
                "receiverThreadIds": ["live-worker"],
                "agentsStates": {"live-worker": {"status": "running"}},
            }})
            app.store.event("item/started", {"threadId": "demo-login", "item": {
                "id": "live-tool", "type": "dynamicToolCall",
                "namespace": "functions", "tool": "exec", "status": "inProgress",
            }})
            app.paint(force=True)
            await pilot.pause(.2)
            panel = app.query_one("#activities", OptionList)
            self.assertTrue(panel.display)
            labels = [
                panel.get_option_at_index(index).prompt.plain
                for index in range(panel.option_count)
            ]
            self.assertIn("Main", labels[0])
            self.assertTrue(any("live-worker" in label for label in labels))
            transcript = app.query_one("#transcript").render().plain
            self.assertIn("spawnAgent", transcript)
            self.assertIn("functions/exec", transcript)
            panel.highlighted = 0
            panel.focus()
            await pilot.press("enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, ActivityDetail)
            entries = app.screen.query_one("#activity-entries", OptionList)
            self.assertTrue(any(
                "functions/exec" in str(entries.get_option_at_index(index).prompt)
                for index in range(entries.option_count)
            ))
            await pilot.press("escape")
            await pilot.press("ctrl+t")
            self.assertFalse(panel.display)
            await pilot.press("ctrl+t")
            self.assertTrue(panel.display)
            for agent_id, agent in app.store.get("demo-login").agents.items():
                if agent_id != "live-worker":
                    agent["status"] = "completed"
            app.store.event("item/completed", {"threadId": "demo-login", "item": {
                "id": "live-spawn", "type": "collabAgentToolCall",
                "tool": "spawnAgent", "status": "completed",
                "receiverThreadIds": ["live-worker"],
                "agentsStates": {"live-worker": {"status": "completed"}},
            }})
            app.paint(force=True)
            self.assertFalse(panel.display)
            await pilot.press("ctrl+t")
            self.assertTrue(panel.display)
            await pilot.press("ctrl+t")
            self.assertFalse(panel.display)
            app.paint(force=True)
            self.assertFalse(panel.display)
            app.store.event("turn/started", {"threadId": "demo-login", "turn": {
                "id": "next-turn"
            }})
            app.store.event("item/started", {"threadId": "demo-login", "item": {
                "id": "next-spawn", "type": "collabAgentToolCall",
                "tool": "spawnAgent", "status": "inProgress",
                "receiverThreadIds": ["next-worker"],
                "agentsStates": {"next-worker": {"status": "running"}},
            }})
            app.paint(force=True)
            self.assertTrue(panel.display)

    async def test_activity_tree_excludes_previous_turn_agents(self):
        """Show four current agents after a prior turn spawned three.

        首轮三个、次轮四个时，当前树仅显示四个。
        """
        app, client = self.app()
        client.threads["sequence"] = {
            "id": "sequence", "name": "Sequence", "cwd": tempfile.gettempdir(),
            "status": {"type": "idle"}, "turns": [],
        }
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause(0.2)
            await app.open_session("sequence")
            await pilot.pause(0.2)
            for turn_id, count in (("first", 3), ("second", 4)):
                app.store.event("turn/started", {
                    "threadId": "sequence", "turn": {"id": turn_id},
                })
                for index in range(count):
                    agent_id = f"{turn_id}-{index}"
                    item = {
                        "id": f"spawn-{agent_id}",
                        "type": "collabAgentToolCall", "tool": "spawnAgent",
                        "receiverThreadIds": [agent_id],
                        "agentsStates": {
                            agent_id: {"status": "running"}
                        },
                    }
                    app.store.event("item/started", {
                        "threadId": "sequence", "item": item,
                    })
                    if turn_id == "first":
                        item["agentsStates"][agent_id]["status"] = "completed"
                        app.store.event("item/completed", {
                            "threadId": "sequence", "item": item,
                        })
                if turn_id == "first":
                    app.store.event("turn/completed", {
                        "threadId": "sequence", "turn": {
                            "id": turn_id, "status": "completed",
                        },
                    })
            app.paint(force=True)
            panel = app.query_one("#activities", OptionList)
            self.assertTrue(panel.display)
            self.assertEqual(panel.option_count, 5)
            self.assertEqual(len(app.store.get("sequence").agents), 7)
            self.assertIn("Agents 4", app.query_one("#activity-summary").render().plain)
            self.assertTrue(all(
                "first-" not in panel.get_option_at_index(index).prompt.plain
                for index in range(panel.option_count)
            ))

    async def test_nested_agent_and_process_open_under_own_thread(self):
        """Keep descendants and their processes under the correct thread.

        孙级代理和它的进程归属于自身线程。
        """
        client = DemoClient()
        nested = {
            "id": "nested-worker", "name": "Nested worker",
            "parentThreadId": "demo-backend", "status": {"type": "active"},
            "turns": [{"id": "nested-turn", "status": "inProgress", "items": [
                {"id": "nested-answer", "type": "agentMessage",
                 "text": "Nested agent result"},
            ]}],
        }
        client.threads["nested-worker"] = nested
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-login")
            await pilot.pause(.2)
            app.store.event("item/started", {
                "threadId": "demo-backend", "item": {
                    "id": "nested-start", "type": "subAgentActivity",
                    "kind": "started", "agentThreadId": "nested-worker",
                    "agentPath": "backend/nested-worker",
                },
            })
            app.store.get("demo-login").agents["nested-worker"]["status"] = (
                "pendingInit"
            )
            original_call = client.call

            async def nested_call(method, params=None, timeout=30):
                if method == "thread/list" and params.get("ancestorThreadId"):
                    result = await original_call(method, params, timeout)
                    result["data"].append({
                        key: value for key, value in nested.items()
                        if key != "turns"
                    })
                    return result
                if (method == "thread/backgroundTerminals/list"
                        and params["threadId"] == "nested-worker"):
                    return {"data": [{
                        "itemId": "nested-command", "processId": "child-pid",
                        "command": "python child.py", "osPid": 5432,
                    }]}
                return await original_call(method, params, timeout)

            with patch.object(client, "call", side_effect=nested_call):
                await app.refresh_activity("demo-login")
            root = app.store.get("demo-login")
            self.assertEqual(root.agents["nested-worker"]["status"], "running")
            self.assertEqual(root.terminals[-1]["agentThreadId"], "nested-worker")
            app.paint(force=True)
            await pilot.press("ctrl+t")
            panel = app.query_one("#activities", OptionList)
            labels = [
                panel.get_option_at_index(index).prompt.plain
                for index in range(panel.option_count)
            ]
            self.assertIn("Main", labels[0])
            backend = next(i for i, label in enumerate(labels) if "后端检查" in label)
            child = next(i for i, label in enumerate(labels) if "nested-worker" in label)
            self.assertEqual(child, backend + 1)
            panel.highlighted = child
            panel.focus()
            await pilot.press("enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, ActivityDetail)
            output = io.StringIO()
            Console(file=output, width=120, color_system=None).print(
                app.screen.query_one("#detail-body").content
            )
            self.assertIn("Nested agent result", output.getvalue())
            body = app.screen.query_one("#detail-body")
            app.screen.selections = {body: Selection(None, None)}
            with patch("arcatom_codex.ui.copy_text", return_value=True) as copied:
                await pilot.press("ctrl+c")
                await pilot.pause(.1)
                self.assertIn("Nested agent result", copied.call_args.args[0])
            entries = app.screen.query_one("#activity-entries", OptionList)
            self.assertTrue(any(
                "python child.py" in str(entries.get_option_at_index(index).prompt)
                for index in range(entries.option_count)
            ))
            await pilot.press("escape")
            app.store.event("item/completed", {
                "threadId": "demo-backend", "item": {
                    "id": "nested-finish", "type": "subAgentActivity",
                    "kind": "completed", "agentThreadId": "nested-worker",
                    "agentPath": "backend/nested-worker",
                },
            })
            app.paint(force=True)
            self.assertIn("Completed", panel.get_option("a-nested-worker").prompt.plain)
            with patch.object(client, "call", side_effect=nested_call):
                await app.refresh_activity("demo-login")
            app.paint(force=True)
            self.assertIn("Completed", panel.get_option("a-nested-worker").prompt.plain)

    async def test_approval_never_automatic_and_escape_declines(self):
        app, client = self.app()
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause(0.2)
            await client.events.put({"id": 55, "method": "item/commandExecution/requestApproval", "params": {
                "threadId": "demo-login", "command": "echo test", "reason": "test"}})
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, Approval)
            self.assertEqual(client.replies, [])
            await pilot.press("escape")
            await pilot.pause(0.2)
            self.assertEqual(client.replies, [(55, {"decision": "decline"})])

    async def test_small_terminal_and_new_session(self):
        app, client = self.app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("ctrl+n", "enter")
            await pilot.pause(0.3)
            self.assertIsNotNone(app.current)
            self.assertEqual(app.store.get(app.current).meta["cwd"], str(Path(tempfile.gettempdir()).resolve()))
            self.assertGreater(app.query_one("#transcript-scroll").size.height, 0)


if __name__ == "__main__":
    unittest.main()

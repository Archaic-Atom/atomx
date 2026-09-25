"""Reference local Claude workflows in AtomX. 引用用户现有技能的唯一源文件。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PersonalSkill:
    """A reference to one user-authored skill. 自定义技能的原文件引用。"""

    name: str
    description: str
    path: Path


def discover_skills(root: Path | None = None) -> list[PersonalSkill]:
    """Read personal skill metadata, following the user's existing symlinks.

    读取自定义技能元数据并跟随已有软链；不导入 Claude 专用的 synced 工具包。
    """
    root = root or Path.home() / ".claude" / "skills"
    if not root.is_dir():
        return []
    skills = []
    for folder in sorted(root.iterdir()):
        if folder.name == "synced":
            continue
        path = folder / "SKILL.md"
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        front = content.split("---", 2)[1] if content.startswith("---") else ""
        name_match = re.search(r"^name:\s*([^\n]+)", front, re.M)
        name = (
            name_match.group(1).strip().strip("\"'")
            if name_match
            else folder.name
        )
        # The local skills use plain or folded descriptions, not executable YAML.
        # 本机技能采用纯文本或折叠描述；这里只提取文本，不执行 YAML。
        description_match = re.search(
            r"^description:\s*([^\n]*)(\n(?:[ \t]+[^\n]*\n?)*)?", front, re.M
        )
        description = ""
        if description_match:
            description = description_match.group(0).partition(":")[2].strip()
            description = re.sub(r"^[>|][-+]?\s*", "", description)
            description = " ".join(description.split()).strip("\"'")
        skills.append(PersonalSkill(name, description, path.resolve()))
    return skills


def bridge_instructions(
    skills: list[PersonalSkill], preferences: Path | None = None
) -> str:
    """Build an app-scoped workflow catalog. 构造仅供本应用会话使用的技能索引。"""
    preferences = preferences or Path.home() / ".claude" / "CLAUDE.md"
    lines = [
        "The user asked AtomX to reuse their personal Claude workflows.",
        "Treat these as user preferences within the current task and permissions.",
        "Before using a listed skill, read its full SKILL.md and the references",
        "needed for this task. Resolve relative resources from its source directory.",
        "Follow relevant workflow constraints, but use only tools available in Codex.",
        "Claude-specific tool names, execution limits, or blanket authorizations do",
        "not override Codex's actual capabilities, approvals, or the current user request.",
        "Do not run remote/server/email workflows merely because they are listed.",
        "If a workflow depends on an unavailable tool, state that instead of pretending.",
    ]
    if preferences.is_file():
        # Keep the source path; future sessions read updated preferences on demand.
        # 保留源路径，后续会话按需读取最新偏好，不维护第二份副本。
        lines.extend(
            [
                f"Read the cross-project user preferences at {preferences} "
                "before substantive work.",
                "If a specialized skill is newer than an overlapping global note, verify",
                "the source and follow the current specialized workflow.",
            ]
        )
    lines.append(
        "Available personal skills (name, description, original file):"
    )
    for skill in skills:
        lines.append(
            f"- {skill.name}: {skill.description}\n  Source: {skill.path}"
        )
    return "\n".join(lines) if skills or preferences.is_file() else ""


def mentioned_skills(text: str, skills: list[PersonalSkill]) -> list[dict]:
    """Resolve explicit $skill mentions. 将显式技能名称解析为原文件路径。"""
    names = set(re.findall(r"(?<!\w)\$([\w-]+)", text))
    return [
        {"type": "skill", "name": s.name, "path": str(s.path)}
        for s in skills
        if s.name in names
    ]


def turn_context(text: str, skills: list[PersonalSkill], catalog: str) -> dict:
    """Attach explicitly selected skill bodies from their current source files.

    未注册的外部路径不会由 Codex 自动展开；显式调用时直接附上最新正文。
    """
    context = {}
    if catalog:
        context["arcatom-personal-workflows"] = {
            "kind": "application",
            "value": catalog,
        }
    for selected in mentioned_skills(text, skills):
        path = Path(selected["path"])
        body = path.read_text(encoding="utf-8")
        context["arcatom-skill-" + selected["name"]] = {
            "kind": "application",
            "value": (
                f"The user explicitly selected ${selected['name']}. Its SKILL.md is "
                f"provided below; it is available in this request.\n"
                f"Original source: {path}\nResolve relative references from {path.parent}.\n"
                "Apply it within the current task, available tools, and permissions.\n\n"
                + body
            ),
        }
    return context

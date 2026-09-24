"""Shared terminal palettes and a portable logo. 统一界面配色与字符 Logo。"""
from dataclasses import dataclass, replace
from .i18n import tr

from rich.text import Text
from textual.theme import Theme


@dataclass(frozen=True)
class Palette:
    """Semantic colors shared by CSS and Rich. CSS 与消息共用语义颜色。"""

    label: str
    background: str
    surface: str
    foreground: str
    muted: str
    accent: str
    selection: str
    user: str
    border: str
    success: str
    warning: str
    error: str
    dark: bool = True

    def theme(self) -> Theme:
        """Build a Textual theme with exact semantic variables. 构造主题变量。"""
        variables = {"arc-" + name: getattr(self, name) for name in (
            "background", "surface", "foreground", "muted", "accent",
            "selection", "user", "border", "success", "warning", "error")}
        return Theme("arcatom-custom", primary=self.accent, background=self.background,
                     foreground=self.foreground, surface=self.surface, dark=self.dark,
                     variables=variables)


PALETTES = {
    "warm": Palette("暖沙", "#191918", "#222220", "#eee9df", "#a5a198",
                    "#d99a76", "#39322d", "#343432", "#62584e",
                    "#9dc39a", "#e6b776", "#e58282"),
    "midnight": Palette("午夜蓝", "#131923", "#1d2633", "#e4edf5", "#a2afbf",
                        "#82becb", "#2b4055", "#323c49", "#4a5c72",
                        "#91caa0", "#e8c17b", "#f09191"),
    "forest": Palette("松林", "#151d19", "#202c25", "#e4eee7", "#a3b3a8",
                      "#9dc39a", "#34483c", "#343e37", "#526859",
                      "#9dc39a", "#e6bd82", "#ef9999"),
    "gray": Palette("石墨灰", "#202020", "#2a2a2a", "#e6e6e6", "#a4a4a4",
                    "#c0c0c0", "#404040", "#373737", "#686868",
                    "#9dc39a", "#e6b776", "#e58282"),
    "paper": Palette("纸白", "#f4f1eb", "#e9e5dd", "#292d30", "#666b6e",
                     "#905031", "#d6dce0", "#dcdcd9", "#979b9c",
                     "#34653b", "#815612", "#a42e37", False),
}
ACCENTS = {"default": "随主题", "copper": "铜橙", "blue": "湖蓝",
           "green": "草绿", "violet": "紫藤", "rose": "玫瑰"}
ACCENT_COLORS = {
    "copper": ("#e5a277", "#905031"), "blue": ("#82becb", "#256579"),
    "green": ("#9dc39a", "#3b713d"), "violet": ("#bdacf0", "#6d48a0"),
    "rose": ("#e8a0b1", "#993f58"),
}


def palette_for(preferences: dict) -> Palette:
    """Resolve saved values, including older preferences. 兼容旧偏好。"""
    palette = PALETTES.get(preferences.get("ui_theme"), PALETTES["warm"])
    colors = ACCENT_COLORS.get(preferences.get("accent"))
    return replace(palette, accent=colors[0 if palette.dark else 1]) if colors else palette


def brand(palette: Palette, compact: bool, home: bool) -> Text:
    """Ink sampled from assets/logo-dark.png; no image protocol required.

    Logo 原图采样为盲文字符，普通跨平台终端即可显示。
    """
    if not home:
        return Text.assemble(("ARCATOM", "bold " + palette.accent),
                             (" / CODEX", palette.muted))
    rows = [" ⢹⣉⣵⣮⣉⡇ ", "⢏⣉⢮⠰⠆⡵⣉⣽", " ⢸⣉⡹⢏⣉⡇ "]
    result = Text()
    for index, line in enumerate(rows):
        result.append(line, palette.accent)
        if index == 1:
            result.append("  ARCATOM", "bold " + palette.foreground)
            result.append(" / CODEX", palette.muted)
            if home:
                result.append(tr("  ·  继续你的工作"), palette.foreground)
        if index < len(rows) - 1:
            result.append("\n")
    return result

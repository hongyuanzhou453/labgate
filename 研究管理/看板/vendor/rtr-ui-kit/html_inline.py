#!/usr/bin/env python3
"""Standard-library Python helper to emit offline single-file HTML pages using @zhouyang/rtr-ui-kit."""

from __future__ import annotations

import argparse
import html
import json
import logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

KIT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSS = KIT_ROOT / "dist" / "rtr-ui.css"
DEFAULT_JS = KIT_ROOT / "dist" / "rtr-ui.iife.js"

FOUC_GUARD = (
    '(function(){try{var v=localStorage.getItem("rtr-color-scheme");'
    'var m=document.querySelector(\'meta[name="color-scheme"]\');var r=document.documentElement;'
    'if(v==="light"||v==="dark"){if(m)m.content=v;r.setAttribute("data-theme",v);}'
    'else{if(m)m.content="light dark";r.removeAttribute("data-theme");}}catch(e){}})();'
)


def _safe_json_script(data: Any) -> str:
    return json.dumps(data if data is not None else {}, ensure_ascii=False).replace("<", "\\u003c")


def _safe_inline_code(code: str) -> str:
    return code.replace("</script>", "<\\/script>").replace("</SCRIPT>", "<\\/SCRIPT>")


def build_standalone_html(
    *,
    title: str = "Read the Research · Report",
    lang: str = "en",
    family: str = "macro",
    theme: str = "system",
    body_html: str = '<main class="rtr-wrap" id="app"></main>',
    data: Any = None,
    boot_js: str = "",
    footer: str = "Generated offline with @zhouyang/rtr-ui-kit.",
    css_path: Path = DEFAULT_CSS,
    js_path: Path = DEFAULT_JS,
) -> str:
    """Assemble a self-contained offline HTML document with inlined RTR CSS and IIFE runtime."""
    if not css_path.is_file():
        raise FileNotFoundError(f"Missing CSS bundle: {css_path}")
    if not js_path.is_file():
        raise FileNotFoundError(f"Missing IIFE JS bundle: {js_path}")

    resolved_family = "company" if family == "company" else "macro"
    resolved_theme = theme if theme in {"light", "dark"} else "system"
    meta_content = "light dark" if resolved_theme == "system" else resolved_theme
    html_theme_attr = "" if resolved_theme == "system" else f' data-theme="{resolved_theme}"'

    css_text = css_path.read_text(encoding="utf-8")
    js_text = _safe_inline_code(js_path.read_text(encoding="utf-8"))

    parts: list[str] = [
        "<!doctype html>",
        f'<html lang="{html.escape(lang, quote=True)}"{html_theme_attr}>',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">',
        f'<meta name="color-scheme" content="{meta_content}">',
        f"<title>{html.escape(title)}</title>",
    ]
    if resolved_theme == "system":
        parts.append(f"<script>{FOUC_GUARD}</script>")
    parts.extend(
        [
            f"<style>\n{css_text}\n</style>",
            "</head>",
            f'<body class="rtr-page rtr-root" data-family="{resolved_family}">',
            body_html,
        ]
    )
    if footer:
        parts.append(
            f'<footer class="rtr-wrap" style="padding-top:0"><p class="rtr-code rtr-muted">{html.escape(footer)}</p></footer>'
        )
    if data is not None:
        parts.append(f'<script type="application/json" id="rtr-data">{_safe_json_script(data)}</script>')
    parts.append(f"<script>\n{js_text}\n</script>")
    if boot_js:
        parts.append(f"<script>\n{_safe_inline_code(boot_js)}\n</script>")
    parts.extend(["</body>", "</html>", ""])
    return "\n".join(parts)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Build offline standalone HTML using @zhouyang/rtr-ui-kit.")
    parser.add_argument("--title", default="RTR Offline Report", help="Document <title>")
    parser.add_argument("--family", choices=["macro", "company"], default="macro", help="Visual family")
    parser.add_argument("--theme", choices=["system", "light", "dark"], default="system", help="Initial theme")
    parser.add_argument("--data", type=Path, default=None, help="Optional JSON data path")
    parser.add_argument("--out", type=Path, default=None, help="Output HTML file path")
    parser.add_argument("--dry-run", action="store_true", help="Build in memory without writing files")
    args = parser.parse_args()

    payload: Any = {"note": "empty"}
    if args.data is not None:
        payload = json.loads(args.data.read_text(encoding="utf-8"))

    output_html = build_standalone_html(
        title=args.title,
        family=args.family,
        theme=args.theme,
        data=payload,
        boot_js="RTR.initMotion(document);",
    )
    byte_len = len(output_html.encode("utf-8"))
    if args.dry_run or args.out is None:
        LOGGER.info("dry-run: built %d bytes (family=%s, theme=%s)", byte_len, args.family, args.theme)
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(output_html, encoding="utf-8")
    LOGGER.info("wrote %s (%d bytes)", args.out, byte_len)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

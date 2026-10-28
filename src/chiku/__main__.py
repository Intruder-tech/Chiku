"""Entry point: `python -m chiku` launches the desktop app; flags run diagnostics."""

from __future__ import annotations

import argparse
import sys

from chiku import diagnostics
from chiku.config import load_settings
from chiku.logging_setup import setup_logging
from chiku.paths import default_paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="chiku", description="Chiku desktop assistant")
    ap.add_argument("--check", action="store_true", help="check hardware, models, Ollama, voice")
    ap.add_argument("--download-models", action="store_true", help="download missing models")
    ap.add_argument("--yes", action="store_true", help="don't ask before downloading")
    ap.add_argument("--say", metavar="TEXT", help="speak TEXT through the selected speaker")
    ap.add_argument("--mic-test", type=float, metavar="SECONDS", help="record and transcribe")
    ap.add_argument("--chat", metavar="TEXT", help="run one text turn through the assistant")
    ap.add_argument("--mute", action="store_true", help="with --chat: don't play audio")
    ap.add_argument("--list-apps", nargs="?", const="", metavar="QUERY", help="list/search apps")
    ap.add_argument("--open-app", metavar="NAME", help="open an app through the tool system")
    ap.add_argument("--set-search-key", action="store_true",
                    help="store (or remove) the Brave Search API key in Credential Manager")
    ap.add_argument("--search", metavar="QUERY", help="run one web search (sends QUERY to Brave)")
    ap.add_argument("--read-page", metavar="URL", help="fetch a page and show extracted text")
    ap.add_argument("--find-files", metavar="QUERY", help="search your user folders by name")
    ap.add_argument("--windows", action="store_true", help="list open app windows")
    ap.add_argument("--memory", action="store_true", help="list what Chiku remembers")
    ap.add_argument("--models", action="store_true", help="list installed Ollama models")
    ap.add_argument("--benchmark", nargs="?", type=int, const=3, metavar="RUNS",
                    help="measure latency, accuracy and resource use (silent; local only)")
    ap.add_argument("--version-info", action="store_true",
                    help="print the version and check that all native components load")
    args = ap.parse_args(argv)
    if args.version_info:
        return diagnostics.version_info()

    paths = default_paths()
    paths.ensure()
    settings = load_settings(paths.settings_file)
    cli = any([args.check, args.download_models, args.say, args.mic_test, args.chat,
               args.list_apps is not None, args.open_app, args.set_search_key, args.search,
               args.read_page, args.find_files, args.windows, args.memory, args.models,
               args.benchmark])
    setup_logging(paths.logs_dir, console=cli and diagnostics.stdout_available())

    if args.download_models:
        return diagnostics.download_models(settings, paths, args.yes)
    if args.check:
        return diagnostics.check(settings, paths)
    if args.say:
        return diagnostics.say(settings, paths, args.say)
    if args.mic_test:
        return diagnostics.mic_test(settings, paths, args.mic_test)
    if args.chat:
        return diagnostics.chat(settings, paths, args.chat, args.mute)
    if args.list_apps is not None:
        return diagnostics.list_apps(args.list_apps)
    if args.open_app:
        return diagnostics.open_app(settings, paths, args.open_app)
    if args.set_search_key:
        return diagnostics.set_search_key()
    if args.search:
        return diagnostics.run_tool(settings, paths, "web_search", {"query": args.search})
    if args.read_page:
        # Explicit CLI request by the user: confirmation prompt appears for unknown URLs.
        return diagnostics.run_tool(settings, paths, "read_web_page", {"url": args.read_page})
    if args.find_files:
        return diagnostics.run_tool(settings, paths, "find_files", {"query": args.find_files})
    if args.windows:
        return diagnostics.run_tool(settings, paths, "list_open_windows", {})
    if args.memory:
        return diagnostics.show_memory(paths)
    if args.models:
        return diagnostics.show_models(settings)
    if args.benchmark:
        return diagnostics.benchmark(settings, paths, args.benchmark)

    from chiku.ui.app import run_gui

    return run_gui(settings, paths)


if __name__ == "__main__":
    sys.exit(main())

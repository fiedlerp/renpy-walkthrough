#!/usr/bin/env python3
"""parse_rpy.py – Parse Ren'Py script files and build a game-flow graph.

This script uses the Ren'Py engine to parse ``.rpy`` files and extracts
the game's structure as a directed graph whose **nodes** are named labels
and whose **edges** represent control-flow transfers (jumps, calls, and
menu choices).

Requirements
------------
The Ren'Py SDK must be accessible. Provide its path via one of:

* The ``RENPY_SDK`` environment variable, or
* The ``--renpy-sdk`` command-line argument.

Download the Ren'Py SDK from: https://www.renpy.org/latest.html

Usage
-----
::

    python parse_rpy.py <game_dir>
    python parse_rpy.py <game_dir> --renpy-sdk /path/to/renpy-sdk
    python parse_rpy.py <game_dir> --output graph.json

Output
------
A JSON document written to *stdout* (or ``--output FILE``) containing:

``nodes``
    A mapping of label name → ``{filename, linenumber}`` metadata.

``edges``
    A list of directed edges, each with the keys ``from``, ``to``,
    ``type`` (``"jump"`` | ``"call"`` | ``"menu"``), and optional
    ``choice``, ``filename``, and ``linenumber`` fields.

Extending this template
-----------------------
* Add ``networkx`` graph construction by passing the returned dict to
  :func:`build_networkx_graph` (see the commented example at the bottom).
* Visualise the graph with ``networkx`` + ``matplotlib`` or export it
  to GraphML / GEXF for tools like Gephi or yEd.
* Filter out internal/auto-generated labels (those starting with ``_``)
  by examining the node name before adding it to the graph.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Ren'Py SDK path helpers
# ---------------------------------------------------------------------------


def _find_sdk_from_env() -> str | None:
    """Return the Ren'Py SDK root from the ``RENPY_SDK`` environment variable."""
    return os.environ.get("RENPY_SDK")


def _setup_renpy_path(sdk_path: str) -> None:
    """Add *sdk_path* (and its ``lib/`` sub-directory) to ``sys.path``.

    The SDK root directory – the one that contains the ``renpy/`` Python
    package – must be on ``sys.path`` so that ``import renpy`` succeeds.
    """
    candidates = [sdk_path, os.path.join(sdk_path, "lib")]
    for path in candidates:
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


# ---------------------------------------------------------------------------
# Minimal Ren'Py initialisation
# ---------------------------------------------------------------------------


def _init_renpy_for_parsing(game_dir: str) -> None:
    """Perform the smallest Ren'Py initialisation that the parser needs.

    The full Ren'Py engine initialises a game loop, display, audio, etc.
    For script parsing we only need:

    * ``renpy.config.basedir`` / ``renpy.config.gamedir`` – basic paths.
    * ``renpy.game.script`` – a lightweight stub whose only requirement is
      ``record_pycode = False``, which prevents :class:`renpy.ast.PyCode`
      from trying to register itself with a non-existent real
      :class:`renpy.script.Script` object.
    """
    import renpy  # noqa: F401 – side-effect import
    import renpy.config as config
    import renpy.game as game

    config.basedir = os.path.abspath(game_dir)
    config.gamedir = os.path.join(config.basedir, "game")
    config.renpy_base = os.path.dirname(os.path.abspath(renpy.__file__))

    class _StubScript:
        record_pycode: bool = False
        all_pycode: list = []
        all_pyexpr: list = []

    game.script = _StubScript()
    game.exception_info = ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_file(filepath: str) -> list[Any] | None:
    """Parse a single ``.rpy`` file and return a list of top-level AST nodes.

    Returns ``None`` when the file cannot be parsed; parse errors are
    printed to *stderr*.
    """
    import renpy.parser as parser

    parser.parse_errors.clear()
    nodes = parser.parse(filepath)

    for err in parser.get_parse_errors():
        print(f"  [parse error] {err}", file=sys.stderr)

    return nodes


def parse_game(game_dir: str) -> list[Any]:
    """Parse every ``.rpy`` file under *game_dir* and return all AST nodes.

    Both *game_dir* itself and its ``game/`` subdirectory (the standard
    Ren'Py project layout) are searched recursively.
    """
    search_dirs: list[str] = [game_dir]
    game_subdir = os.path.join(game_dir, "game")
    if os.path.isdir(game_subdir):
        search_dirs.append(game_subdir)

    all_nodes: list[Any] = []
    seen: set[str] = set()

    for search_dir in search_dirs:
        for root, _dirs, files in os.walk(search_dir):
            for filename in sorted(files):
                if not filename.endswith(".rpy"):
                    continue
                filepath = os.path.join(root, filename)
                # Avoid double-parsing when game_dir == parent of game/
                if filepath in seen:
                    continue
                seen.add(filepath)

                print(f"Parsing: {filepath}", file=sys.stderr)
                nodes = parse_file(filepath)
                if nodes:
                    all_nodes.extend(nodes)

    return all_nodes


# ---------------------------------------------------------------------------
# Graph building
# ---------------------------------------------------------------------------


def _collect_edges(
    nodes: list[Any],
    current_label: str | None,
    graph: dict[str, Any],
) -> None:
    """Recursively walk *nodes* and record control-flow edges in *graph*.

    This function mutates *graph* in place.
    """
    import renpy.ast as ast

    for node in nodes:
        if isinstance(node, ast.Label):
            label_name: str = node.name
            if label_name not in graph["nodes"]:
                graph["nodes"][label_name] = {
                    "filename": node.filename,
                    "linenumber": node.linenumber,
                }
            # Recurse into the label's body under the new label context.
            _collect_edges(node.block, label_name, graph)

        elif isinstance(node, ast.Jump):
            # Skip expression-based jumps (target not statically known).
            if current_label is not None and not node.expression:
                graph["edges"].append(
                    {
                        "from": current_label,
                        "to": node.target,
                        "type": "jump",
                        "filename": node.filename,
                        "linenumber": node.linenumber,
                    }
                )

        elif isinstance(node, ast.Call):
            # Skip expression-based calls (target not statically known).
            if current_label is not None and not node.expression:
                graph["edges"].append(
                    {
                        "from": current_label,
                        "to": node.label,
                        "type": "call",
                        "filename": node.filename,
                        "linenumber": node.linenumber,
                    }
                )

        elif isinstance(node, ast.Menu):
            # Each menu item is a (choice_text, condition, block) tuple.
            # Items whose block is None are narration lines before the choices.
            for item_text, _condition, item_block in node.items:
                if item_block is None or item_text is None:
                    continue
                # Recurse so that jumps/calls inside a menu branch are captured.
                _collect_edges(item_block, current_label, graph)
                # Also record a dedicated "menu" edge for the first
                # unconditional jump/call inside this choice block so that
                # graph visualisers can label the edge with the choice text.
                if item_block and current_label is not None:
                    first = item_block[0]
                    if isinstance(first, ast.Jump) and not first.expression:
                        graph["edges"].append(
                            {
                                "from": current_label,
                                "to": first.target,
                                "type": "menu",
                                "choice": item_text,
                                "filename": node.filename,
                                "linenumber": node.linenumber,
                            }
                        )
                    elif isinstance(first, ast.Call) and not first.expression:
                        graph["edges"].append(
                            {
                                "from": current_label,
                                "to": first.label,
                                "type": "menu",
                                "choice": item_text,
                                "filename": node.filename,
                                "linenumber": node.linenumber,
                            }
                        )

        elif isinstance(node, ast.If):
            for _condition, block in node.entries:
                _collect_edges(block, current_label, graph)

        elif isinstance(node, ast.While):
            _collect_edges(node.block, current_label, graph)


def build_graph(ast_nodes: list[Any]) -> dict[str, Any]:
    """Build a game-flow graph from a flat list of top-level AST nodes.

    Returns a dict with two keys:

    ``nodes``
        A mapping of label name → ``{filename, linenumber}`` metadata.

    ``edges``
        A list of dicts, each describing one directed control-flow edge
        with ``from``, ``to``, ``type``, and optional ``choice``,
        ``filename``, and ``linenumber`` keys.
    """
    import renpy.ast as ast

    graph: dict[str, Any] = {"nodes": {}, "edges": []}

    # First pass: register every top-level Label as a node so that labels
    # that have no incoming edges still appear in the graph.
    for node in ast_nodes:
        if isinstance(node, ast.Label):
            graph["nodes"][node.name] = {
                "filename": node.filename,
                "linenumber": node.linenumber,
            }

    # Second pass: collect all control-flow edges.
    _collect_edges(ast_nodes, None, graph)

    return graph


# ---------------------------------------------------------------------------
# Optional: networkx integration (uncomment to use)
# ---------------------------------------------------------------------------

# def build_networkx_graph(graph: dict) -> "networkx.DiGraph":
#     """Convert the JSON graph dict to a networkx DiGraph.
#
#     Requires: ``pip install networkx``
#     """
#     import networkx as nx
#
#     dg = nx.DiGraph()
#     for label, meta in graph["nodes"].items():
#         dg.add_node(label, **meta)
#     for edge in graph["edges"]:
#         dg.add_edge(
#             edge["from"], edge["to"],
#             type=edge["type"],
#             choice=edge.get("choice", ""),
#         )
#     return dg


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Parse Ren'Py .rpy files and output a game-flow graph.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("game_dir", help="Path to the Ren'Py game directory.")
    p.add_argument(
        "--renpy-sdk",
        metavar="PATH",
        default=None,
        help=(
            "Path to the Ren'Py SDK (the directory that contains the renpy/ "
            "package). Defaults to the value of the RENPY_SDK environment "
            "variable."
        ),
    )
    p.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="Write the JSON graph to FILE instead of stdout.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    # ------------------------------------------------------------------
    # 1. Locate and import Ren'Py.
    # ------------------------------------------------------------------
    sdk_path = args.renpy_sdk or _find_sdk_from_env()
    if sdk_path:
        _setup_renpy_path(sdk_path)

    try:
        import renpy  # noqa: F401
    except ImportError:
        print(
            "Error: Could not import the 'renpy' package.\n\n"
            "Please provide the path to the Ren'Py SDK via --renpy-sdk or the\n"
            "RENPY_SDK environment variable, or run this script with the\n"
            "Ren'Py SDK's bundled Python interpreter.\n\n"
            "Download the SDK from: https://www.renpy.org/latest.html",
            file=sys.stderr,
        )
        return 1

    # ------------------------------------------------------------------
    # 2. Validate game directory.
    # ------------------------------------------------------------------
    game_dir = os.path.abspath(args.game_dir)
    if not os.path.isdir(game_dir):
        print(f"Error: Game directory not found: {game_dir}", file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # 3. Initialise Ren'Py for parsing only (no display / audio).
    # ------------------------------------------------------------------
    _init_renpy_for_parsing(game_dir)

    # ------------------------------------------------------------------
    # 4. Parse all .rpy files.
    # ------------------------------------------------------------------
    ast_nodes = parse_game(game_dir)
    if not ast_nodes:
        print(
            "Warning: No AST nodes were produced. "
            "Ensure that .rpy files exist under the given directory.",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------
    # 5. Build the graph and serialise to JSON.
    # ------------------------------------------------------------------
    graph = build_graph(ast_nodes)
    graph_json = json.dumps(graph, indent=2, default=str)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(graph_json)
        print(f"Graph written to: {args.output}", file=sys.stderr)
    else:
        print(graph_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())

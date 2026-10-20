"""Table cells print server text literally, never as Rich markup.

Root cause: ``print_table`` passed cells straight to Rich, so a proposal's
"Filed by" text (which includes the agent name a caller declares in
``X-Beakr-Agent``), a page title or a summary containing ``[...]`` was read as
markup and could restyle or hide what a reviewer sees.
"""

from __future__ import annotations

from rich.console import Console

from beakr_cli import output


def test_table_cells_with_markup_print_literally(monkeypatch):
    recorder = Console(record=True, width=200, color_system=None)
    monkeypatch.setattr(output, "console", recorder)

    output.print_table(
        ["Page", "Filed by"],
        [
            [
                "[bold red]Roadmap[/bold red]",
                "[hidden]Dana[/hidden] Max Strome via [link=x]agent[/link]",
            ]
        ],
    )

    text = recorder.export_text()
    assert "[bold red]Roadmap[/bold red]" in text
    assert "[hidden]Dana[/hidden] Max Strome via [link=x]agent[/link]" in text

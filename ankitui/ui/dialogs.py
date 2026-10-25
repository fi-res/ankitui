"""Reusable modal dialogs."""

from __future__ import annotations

import os

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Label, OptionList, Static
from textual.widgets.option_list import Option


class MessageDialog(ModalScreen[None]):
    BINDINGS = [Binding("escape,enter", "close", "Close")]

    def __init__(self, title: str, message: str):
        super().__init__()
        self.title_text = title
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            with VerticalScroll(classes="dialog-body"):
                yield Static(self.message)
            with Horizontal(classes="dialog-buttons"):
                yield Button("OK", variant="primary", id="ok")

    def on_mount(self) -> None:
        self.query_one("#ok", Button).focus()

    @on(Button.Pressed, "#ok")
    def action_close(self) -> None:
        self.dismiss(None)


class ConfirmDialog(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("y", "confirm", "Yes", show=False)]

    def __init__(self, title: str, message: str, yes: str = "Yes", no: str = "No", danger: bool = False):
        super().__init__()
        self.title_text = title
        self.message = message
        self.yes = yes
        self.no = no
        self.danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            yield Static(self.message, classes="dialog-body")
            with Horizontal(classes="dialog-buttons"):
                yield Button(self.yes, variant="error" if self.danger else "primary", id="yes")
                yield Button(self.no, id="no")

    def on_mount(self) -> None:
        self.query_one("#no" if self.danger else "#yes", Button).focus()

    @on(Button.Pressed, "#yes")
    def action_confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def action_cancel(self) -> None:
        self.dismiss(False)


class PromptDialog(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, label: str = "", value: str = "", placeholder: str = "", restrict: str | None = None):
        super().__init__()
        self.title_text = title
        self.label = label
        self.value = value
        self.placeholder = placeholder
        self.restrict = restrict

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            if self.label:
                yield Label(self.label)
            yield Input(self.value, placeholder=self.placeholder, id="prompt-input", restrict=self.restrict)
            with Horizontal(classes="dialog-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def submit(self) -> None:
        self.dismiss(self.query_one(Input).value)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class ChoiceDialog(ModalScreen[str | None]):
    """Pick one entry from a list of (key, label) choices."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, choices: list[tuple[str, str | Text]], message: str = ""):
        super().__init__()
        self.title_text = title
        self.choices = choices
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title")
            if self.message:
                yield Static(self.message)
            yield OptionList(*[Option(label, id=key) for key, label in self.choices], id="choices")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class FilePickerDialog(ModalScreen[str | None]):
    """Browse for a file (or type a path). For saving, a file name can be typed."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, start: str | None = None, save: bool = False, default_name: str = "", extensions: tuple[str, ...] = ()):
        super().__init__()
        self.title_text = title
        self.start = start or os.getcwd()
        self.save = save
        self.default_name = default_name
        self.extensions = extensions

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog dialog-wide"):
            yield Label(self.title_text, classes="dialog-title")
            yield Label("Select a file below or type a path:")
            initial = os.path.join(self.start, self.default_name) if self.save else ""
            yield Input(initial, placeholder="Path", id="path")
            tree = DirectoryTree(self.start, id="tree")
            yield tree
            with Horizontal(classes="dialog-buttons"):
                yield Button("Save" if self.save else "Open", variant="primary", id="ok")
                yield Button("Up", id="up")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#path" if self.save else "#tree").focus()

    @on(DirectoryTree.FileSelected)
    def file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.query_one("#path", Input).value = str(event.path)
        if not self.save:
            self.dismiss(str(event.path))

    @on(DirectoryTree.DirectorySelected)
    def dir_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        if self.save:
            name = os.path.basename(self.query_one("#path", Input).value) or self.default_name
            self.query_one("#path", Input).value = os.path.join(str(event.path), name)

    @on(Button.Pressed, "#up")
    def go_up(self) -> None:
        tree = self.query_one(DirectoryTree)
        parent = os.path.dirname(os.path.abspath(str(tree.path)))
        tree.path = parent

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def submit(self) -> None:
        path = os.path.expanduser(self.query_one("#path", Input).value.strip())
        if not path:
            return
        if os.path.isdir(path):
            self.query_one(DirectoryTree).path = path
            return
        if not self.save and not os.path.exists(path):
            self.notify("File not found", severity="error")
            return
        self.dismiss(path)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

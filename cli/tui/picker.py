"""Inline interactive picker — the dsh selection grammar on prompt_toolkit.

dsh answers ``/resume`` and ``/model`` with a selector, not an id prompt:
type to filter, ↑/↓ to move, Enter to choose, Esc clears a non-empty
filter first and cancels on the second press, Tab flips between scopes,
Shift+Tab cycles the highlighted row's variant (its reasoning effort),
and the row's *title* is the identity — ids live on the dim detail line.
This is the DeepCode rendition: an inline (no alternate screen)
prompt_toolkit application that erases itself when done, leaving the
transcript clean.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl

from cli.tui import theme

# dsh shows at most 8 rows and pages the rest under the filter.
_VISIBLE_ROWS = 8


@dataclass(frozen=True, slots=True)
class PickerVariant:
    """One value of an item's cycled dimension (dsh: reasoning effort)."""

    value: object
    label: str


@dataclass(frozen=True, slots=True)
class PickerItem:
    """One selectable row: a human title first, machine detail second."""

    value: object
    title: str
    detail: str = ""
    disabled_reason: str | None = None
    # Shift+Tab cycles these on the highlighted row (dsh's model/effort
    # pairing); empty means the item has no cycled dimension.
    variants: tuple[PickerVariant, ...] = ()
    initial_variant: int = 0

    def matches(self, query: str) -> bool:
        needle = query.casefold()
        return needle in self.title.casefold() or needle in self.detail.casefold()


@dataclass(frozen=True, slots=True)
class PickerChoice:
    """What Enter commits: the row's value plus its cycled variant, if any."""

    value: object
    variant: object | None = None


@dataclass(frozen=True, slots=True)
class PickerScope:
    """A labeled set of items; Tab cycles between scopes (dsh's ⇥)."""

    label: str
    items: Sequence[PickerItem]


class Picker:
    """Filterable inline selector; ``run()`` returns the chosen value or None."""

    def __init__(
        self,
        scopes: Sequence[PickerScope],
        *,
        title: str,
        initial_scope: int = 0,
        variant_hint: str = "variant",
    ) -> None:
        if not scopes:
            raise ValueError("picker needs at least one scope")
        self._scopes = list(scopes)
        self._title = title
        self._variant_hint = variant_hint
        self._scope_index = initial_scope % len(self._scopes)
        self._selected = 0
        self._window_start = 0
        # Chosen variant per item (keyed by identity: items outlive the run).
        self._variant_index: dict[int, int] = {}
        self._buffer = Buffer(multiline=False)
        self._buffer.on_text_changed += self._on_filter_changed

    # -- state ---------------------------------------------------------------

    def _filtered(self) -> list[PickerItem]:
        query = self._buffer.text.strip()
        items = self._scopes[self._scope_index].items
        if not query:
            return list(items)
        return [item for item in items if item.matches(query)]

    def _on_filter_changed(self, _buffer) -> None:
        self._selected = 0
        self._window_start = 0

    def _move(self, step: int) -> None:
        count = len(self._filtered())
        if count == 0:
            return
        self._selected = (self._selected + step) % count
        if self._selected < self._window_start:
            self._window_start = self._selected
        elif self._selected >= self._window_start + _VISIBLE_ROWS:
            self._window_start = self._selected - _VISIBLE_ROWS + 1

    def _variant_of(self, item: PickerItem) -> PickerVariant | None:
        if not item.variants:
            return None
        index = self._variant_index.get(id(item), item.initial_variant)
        return item.variants[index % len(item.variants)]

    def _cycle_variant(self, item: PickerItem) -> None:
        if not item.variants:
            return
        index = self._variant_index.get(id(item), item.initial_variant)
        self._variant_index[id(item)] = (index + 1) % len(item.variants)

    # -- rendering -----------------------------------------------------------

    def _header_fragments(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = [
            (theme.PICKER_TITLE_STYLE_PTK, f" {self._title}")
        ]
        if len(self._scopes) > 1:
            current = self._scopes[self._scope_index]
            others = [
                f"⇥ {scope.label} ({len(scope.items)})"
                for index, scope in enumerate(self._scopes)
                if index != self._scope_index
            ]
            fragments.append(("", f" · {current.label}   {'   '.join(others)}"))
        return fragments

    def _row_fragments(self) -> StyleAndTextTuples:
        filtered = self._filtered()
        if not filtered:
            return [("", "   no matches")]
        fragments: StyleAndTextTuples = []
        visible = filtered[self._window_start : self._window_start + _VISIBLE_ROWS]
        for offset, item in enumerate(visible):
            index = self._window_start + offset
            selected = index == self._selected
            pointer = f" {theme.PICKER_POINTER} " if selected else "   "
            title_style = (
                theme.PICKER_SELECTED_STYLE_PTK
                if selected
                else theme.PICKER_TITLE_STYLE_PTK
            )
            if offset:
                fragments.append(("", "\n"))
            fragments.append((title_style, f"{pointer}{item.title}"))
            variant = self._variant_of(item)
            if variant is not None:
                fragments.append(("", f" · {self._variant_hint} {variant.label}"))
            detail = item.detail
            if item.disabled_reason:
                detail = f"{detail} · unavailable: {item.disabled_reason}".strip(" ·")
            if detail:
                fragments.append(("", f"\n     {detail}"))
        above = self._window_start
        below = len(filtered) - (self._window_start + len(visible))
        if above > 0 or below > 0:
            fragments.append(("", f"\n   … {above} above · {below} below"))
        return fragments

    def _rows_height(self) -> int:
        filtered = self._filtered()
        if not filtered:
            return 1
        visible = min(len(filtered), _VISIBLE_ROWS)
        details = sum(
            1
            for item in filtered[self._window_start : self._window_start + visible]
            if item.detail or item.disabled_reason
        )
        pager = 1 if len(filtered) > visible else 0
        return visible + details + pager

    def _hint_fragments(self) -> StyleAndTextTuples:
        parts = ["type to filter", "↑/↓ move", "enter select"]
        if len(self._scopes) > 1:
            parts.append("tab scope")
        if any(item.variants for scope in self._scopes for item in scope.items):
            parts.append(f"shift+tab {self._variant_hint}")
        parts.append("esc cancel")
        return [("", " " + " · ".join(parts))]

    # -- keys ----------------------------------------------------------------

    def _key_bindings(self) -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add("up")
        def _up(event) -> None:
            self._move(-1)

        @bindings.add("down")
        def _down(event) -> None:
            self._move(1)

        @bindings.add("tab")
        def _scope(event) -> None:
            if len(self._scopes) > 1:
                self._scope_index = (self._scope_index + 1) % len(self._scopes)
                self._selected = 0
                self._window_start = 0

        @bindings.add("s-tab")
        def _variant(event) -> None:
            filtered = self._filtered()
            if filtered:
                self._cycle_variant(filtered[self._selected])

        @bindings.add("enter")
        def _accept(event) -> None:
            filtered = self._filtered()
            if not filtered:
                return
            item = filtered[self._selected]
            if item.disabled_reason:
                return
            variant = self._variant_of(item)
            event.app.exit(
                result=PickerChoice(
                    value=item.value,
                    variant=variant.value if variant is not None else None,
                )
            )

        @bindings.add("escape", eager=True)
        def _cancel(event) -> None:
            # dsh grammar: first Esc clears a non-empty filter, second closes.
            if self._buffer.text:
                self._buffer.reset()
            else:
                event.app.exit(result=None)

        @bindings.add("c-c")
        def _abort(event) -> None:
            event.app.exit(result=None)

        return bindings

    # -- entry ---------------------------------------------------------------

    async def run(self) -> PickerChoice | None:
        layout = Layout(
            HSplit(
                [
                    Window(FormattedTextControl(self._header_fragments), height=1),
                    Window(
                        BufferControl(self._buffer),
                        height=1,
                        get_line_prefix=lambda line, wrap: [("", " ⌕ ")],
                    ),
                    Window(
                        FormattedTextControl(self._row_fragments),
                        height=self._rows_height,
                    ),
                    Window(FormattedTextControl(self._hint_fragments), height=1),
                ]
            )
        )
        application: Application = Application(
            layout=layout,
            key_bindings=self._key_bindings(),
            full_screen=False,
            erase_when_done=True,
            mouse_support=False,
        )
        return await application.run_async()

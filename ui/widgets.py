# This module implements widgets.

from textension.utils import _check_type, _forwarder, _system, _context, \
    test_and_update, safe_redraw, close_cells, inline, set_name, \
    LinearStack, Adapter, soft_property, _named_index
from textension.ui.utils import set_focus, get_focus
from textension.ui.gl import Rect, Texture
from textension.core import find_word_boundary

from functools import partial
from itertools import islice
from operator import methodcaller, itemgetter
from collections import defaultdict
from typing import Optional, Union

from textension.overrides.default import (
    ED_OT_redo,
    ED_OT_undo,
    TEXT_OT_copy,
    TEXT_OT_cut,
    TEXT_OT_delete,
    TEXT_OT_insert,
    TEXT_OT_line_break,
    TEXT_OT_move,
    TEXT_OT_move_select,
    TEXT_OT_paste,
    TEXT_OT_select_all,
)

from bl_math import clamp
import bpy
import blf

# All possible cursors to pass Window.set_cursor()
cursor_types = set(bpy.types.Operator.bl_rna.properties["bl_cursor_pending"].enum_items.keys())
_get_margins = itemgetter("left", "top", "right", "bottom")

__all__ = [
    "BoxResizer",
    "EdgeResizer",
    "Input",
    "ListBox",
    "ListEntry",
    "OverlayRect",
    "Scrollbar",
    "TextDraw",
    "TextLine",
    "TextView",
    "Thumb",
    "Widget",
]

def wrap_string(string:    str,
                max_width: int,
                font_size: int,
                font_id:   int) -> list[str]:

    from collections import deque
    string = string.expandtabs(4)
    lines = deque(string.split("\n"))

    wrapped = []
    

    def _break_word(word) -> tuple[str, str]:
        for i in range(len(word)):
            substring = word[:i + 1]
            substring_width = blf.dimensions(font_id, substring + " ")[0]
            if substring_width > max_width:
                return word[:i], word[i:]
        return word[:-1], word[-1]

    blf.size(font_id, font_size, int(_system.dpi * _system.pixel_size))
    while lines:
        line = lines.popleft()
        width = blf.dimensions(font_id, line)[0]

        if width > max_width:
            span = 0
            words = deque(line.split(" "))

            tmp = []

            while words:
                word = words.popleft()
                word_length = blf.dimensions(font_id, word + " ")[0]
                curr_width = word_length + span

                if curr_width < max_width:
                    tmp += word,
                    span += word_length

                elif tmp:
                    words.appendleft(word)
                    wrapped.append(" ".join(tmp))
                    del tmp[:]
                    span = 0

                else:
                    word, tail = _break_word(word)
                    words.appendleft(tail)
                    wrapped.append(word)
            if tmp:
                line = " ".join(tmp)

        wrapped.append(line)
    return wrapped


class Widget:
    """Base for rectangular shaped widgets.
    Supports rounded corners, borders, hit testing.
    """
    parent:   Optional["Widget"]
    children:     list["Widget"]

    background_color = 1.0, 1.0, 1.0, 1.0
    border_color     = 0.0, 0.0, 0.0, 1.0

    border_width     = 1.0
    corner_radius    = 0.0

    # The cursor to set when the Widget is hit tested.
    cursor           = 'DEFAULT'

    # For convenience. Allows using forwarders.
    context          = _context

    def __init__(self, parent: Optional["Widget"] = None) -> None:
        _check_type(parent, Widget, type(None))
        assert self.cursor in cursor_types, self.cursor

        self.children = []
        if parent is not None:
            parent.children += self,

        self.parent = parent
        self.rect   = Rect()

        self.update_uniforms(
            background_color=self.background_color,
            border_color    =self.border_color,
            border_width    =self.border_width,
            corner_radius   =self.corner_radius)

    @inline
    def hit_test(self, x: float, y: float) -> bool:
        """Hit test and return the most refined result, which
        can be this Widget, any of its children, or None.
        """
        from textension.utils import filtertrue
        from operator import methodcaller
        from builtins import map

        @set_name("hit_test (Widget)")
        def hit_test(self: "Widget", x: float, y: float) -> bool:
            rect = self.rect
            if 0.0 <= x - rect.x <= rect.width:
                if 0.0 <= y - rect.y <= rect.height:
                    call = methodcaller("hit_test", x, y)
                    for widget in filtertrue(map(call, self.children)):
                        return widget
                    return self
            return None
        return hit_test

    @inline
    def update_uniforms(self, **kw) -> None:
        return _forwarder("rect.update_uniforms")

    def on_enter(self) -> None:
        """Called when the mouse enters the Widget."""

    def on_leave(self) -> None:
        """Called when the mouse leaves the Widget."""

    def on_activate(self) -> None:
        """Called when the Widget is activated by pressing it."""

    x      = _forwarder("rect.x", rtype=float)
    y      = _forwarder("rect.y", rtype=float)

    width        = _forwarder("rect.width",       rtype=float)
    width_inner  = _forwarder("rect.width_inner", rtype=float)

    height       = _forwarder("rect.height",       rtype=float)
    height_inner = _forwarder("rect.height_inner", rtype=float)

    position_inner = _forwarder("rect.position_inner", rtype=tuple[float, float])
    size_inner     = _forwarder("rect.size_inner", rtype=tuple[float, float])

    def __repr__(self):
        return f"<{self._repr_path()} at {id(self):016X}>"

    def _repr_path(self):
        if self.parent:
            return f"{self.parent._repr_path()}.{self.__class__.__name__}"
        return self.__class__.__name__


# Used as a no-hit result when we just want to block.
HIT_BLOCK = Widget()


class Thumb(Widget):
    """Scrollbar thumb."""

    parent: "Scrollbar"

    background_color = 0.24, 0.24, 0.24, 1.0
    border_color     = 0.32, 0.32, 0.32, 1.0

    def __init__(self, scroll: "Scrollbar") -> None:
        _check_type(scroll, Scrollbar)
        super().__init__(parent=scroll)

    def set_highlight(self, state: bool) -> None:
        mul = 0.05 * float(state)
        self.rect.border_color = [clamp(v + mul) for v in self.border_color]
        self.rect.background_color = [clamp(v + mul) for v in self.background_color]
        safe_redraw()

    def on_enter(self) -> None:
        self.set_highlight(True)

    def on_leave(self):
        self.set_highlight(False)

    def on_activate(self) -> None:
        bpy.ops.textension.ui_scrollbar('INVOKE_DEFAULT', axis=self.parent.axis)


class Scrollbar(Widget):
    """Scrollbar for TextDraw objects."""

    parent: "TextDraw"
    thumb:   Thumb
    axis:    str

    # These are for the scrollbar gutter (invisible).
    background_color = 0.0, 0.0, 0.0, 0.0
    border_color     = 0.0, 0.0, 0.0, 0.0

    thumb_offsets    = (0, 0)

    # For overriding textension.ui_scroll_lines behavior.
    @property
    def is_passthrough(self) -> bool:
        return False

    def __init__(self, parent: "TextDraw", axis: str = "VERTICAL") -> None:
        _check_type(parent, TextDraw)

        super().__init__(parent=parent)
        self.thumb = Thumb(scroll=self)

        assert axis in {"VERTICAL", "HORIZONTAL"}, "Bad axis"
        self.axis = axis

        self.thumb.update_uniforms(
            background_color=parent.scrollbar_thumb_background_color,
            corner_radius   =parent.corner_radius,
            border_color    =parent.scrollbar_thumb_border_color,
            border_width    =parent.scrollbar_thumb_border_width)

        self.update_uniforms(
            background_color=parent.scrollbar_background_color,
            corner_radius   =parent.corner_radius,
            border_color    =parent.scrollbar_border_color)

        self.set_view = partial(parent.set_view, axis=axis)

    def on_activate(self) -> None:
        bpy.ops.textension.ui_scroll_lines('INVOKE_DEFAULT')

    @inline
    def set_view(self, ratio: float) -> None:
        """Transform the parent's view, aka scroll. Between 0-1."""
        # This function is bound when the Scrollbar is initialized.

    def draw(self) -> None:
        """Draw the scrollbar."""
        parent = self.parent
        offset, span_px = self._compute_offsets()
        x, y = parent.position_inner

        if self.axis == "VERTICAL":
            # Width is defined on parent and multiplied by ui scale.
            width = parent.scrollbar_width * (_system.wu * 0.05)
            # Position the scrollbar to the parent's right edge.
            x += parent.width_inner - width
            self.rect.draw(x, y, width, parent.height)

            if span_px > 0:
                self.thumb.rect.draw(x, y + offset, width, span_px)
        else:
            width = parent.width
            height = parent.scrollbar_width * (_system.wu * 0.05)

            self.rect.draw(x, y, width, height)

            if span_px > 0:
                self.thumb.rect.draw(x + offset, y, span_px, height)

    def _compute_offsets(self) -> tuple[int, int]:
        """Calculate the thumb's offset and length.
        
        The offset becomes the amount of pixels into the parent.
        The span becomes the length of the thumb.
        """

        parent = self.parent
        view_ratio = parent.get_view_ratio(self.axis)

        if view_ratio >= 1.0:
            self.thumb_offsets = (0, 0)
            return self.thumb_offsets

        pos_ratio = parent.get_view_position(self.axis)

        if self.axis == "VERTICAL":
            span_px = parent.height_inner
            pos_ratio = 1.0 - pos_ratio

        else:
            span_px = parent.width_inner
            if parent.max_top:
                span_px -= parent.scrollbar_width

        length_px = round(max(view_ratio * span_px, min(span_px, 30)))
        offset_px = round((span_px - length_px) * pos_ratio)

        self.thumb_offsets = (offset_px, length_px)
        return self.thumb_offsets

    def get_difference(self, axis: str) -> int:
        if axis == "VERTICAL":
            parent_span = self.parent.height_inner
        else:
            parent_span = self.parent.width_inner
        return parent_span - self._compute_offsets()[1]

    @property
    def thumb_y(self) -> int:
        """The y coordinate of the thumb."""
        return self._compute_offsets()[0]

    @set_name("hit_test (Scrollbar)")
    def hit_test(self, x, y) -> None:
        if self.thumb_offsets != (0, 0):
            return super().hit_test(x, y)
        return None


class TextLine:
    __slots__ = ("string",)

    # The string this line holds.
    string: str

    def __init__(self, string) -> None:
        self.string = string


class ListEntry(TextLine):
    """Entry for a ListBox. Does nothing unique from TextLine currently."""


class EdgeResizer(Widget):
    """A resize handle. Currently only for bottom and right edges of a Widget."""
    background_color = 0.4, 0.4, 0.4, 0.0
    border_color     = 0.4, 0.4, 0.4, 0.0
    corner_radius    = 4.0

    show_resize_handles = True

    # The axis the resizer works in - 'VERTICAL' or 'HORIZONTAL'.
    axis: str = ""

    # The Widget this resizer controls. Doesn't have to be the parent.
    subject: Widget

    def __init__(self, parent: Widget, subject=None) -> None:
        if self.axis not in ('VERTICAL', 'HORIZONTAL'):
            raise ValueError(f"axis in 'VERTICAL' or 'HORIZONTAL'")
        super().__init__(parent=parent)
        self.subject = subject

    def set_alpha(self, value) -> None:
        if test_and_update(self.rect.background_color, "w", value):
            safe_redraw()

    def on_enter(self) -> None:
        if self.show_resize_handles:
            self.set_alpha(1.0)

    def on_leave(self) -> None:
        self.set_alpha(0.0)

    def on_activate(self) -> None:
        bpy.ops.textension.ui_resize('INVOKE_DEFAULT', axis=self.axis)

    def draw(self, x, y, w, h) -> None:
        if self.rect.background_color.w > 0.0:
            self.rect.draw(x, y, w, h)
        else:
            # Just update the rectangle.
            self.rect[:] = x, y, w, h

    def get_subject(self) -> Widget:
        return self.subject or self.parent


class VerticalResizer(EdgeResizer):
    cursor  = 'MOVE_Y'
    axis    = 'VERTICAL'


class HorizontalResizer(EdgeResizer):
    cursor  = 'MOVE_X'
    axis    = 'HORIZONTAL'


class BoxResizer(Widget):
    """Allows resizing the lower right corner of a box."""

    # The corner resizer isn't actually drawn.
    background_color = 0.0, 0.0, 0.0, 0.0
    border_color     = 0.0, 0.0, 0.0, 0.0

    sizers: tuple[HorizontalResizer, VerticalResizer]

    cursor = 'SCROLL_XY'

    def __init__(self, parent: Widget) -> None:
        super().__init__(parent)
        self.sizers = (HorizontalResizer(self, parent),
                       VerticalResizer(self, parent))

    @set_name("hit_test (BoxResizer)")
    def hit_test(self, x, y):
        if super().hit_test(x, y):
            return self
        horz, vert = self.sizers
        return horz.hit_test(x, y) or vert.hit_test(x, y)

    def on_enter(self) -> None:
        # Entering the BoxResizer highlights both sizers.
        for sizer in self.sizers:
            sizer.on_enter()

    def on_leave(self) -> None:
        for sizer in self.sizers:
            sizer.on_leave()

    def draw(self) -> None:
        x, y, w, h = self.parent.rect
        horz, vert = self.sizers
        t = 4

        vert.draw(x, y, w, t)
        horz.draw(x + w - t, y, t, h)

        t *= 3
        self.rect[:] = x + w - t, y, t, t

    def on_activate(self) -> None:
        bpy.ops.textension.ui_resize('INVOKE_DEFAULT', axis='CORNER')

    def get_subject(self) -> Widget:
        return self.parent


class TextDraw(Widget):
    """Implements functionality for drawing non-rich text."""

    font_size: int = 16
    font_id:   int = 0

    top:     float = 0.0
    left:      int = 0

    width:     int = 1
    height:    int = 1

    lines: list[TextLine]
    foreground_color = 0.4,  0.7, 1.0,  1.0
    line_padding: float = 1.25

    scrollbar_width = 12

    scrollbar_background_color = 0.0, 0.0, 0.0, 0.0
    scrollbar_border_color     = 0.0, 0.0, 0.0, 0.0
    scrollbar_border_width     = 1

    scrollbar_thumb_background_color = 0.24, 0.24, 0.24, 1.0
    scrollbar_thumb_border_color     = 0.32, 0.32, 0.32, 1.0
    scrollbar_thumb_border_width     = 1

    show_horizontal_scrollbar: bool  = True

    @inline
    def count_lines(self) -> int:
        return _forwarder("lines.__len__")

    num_lines: int = property(methodcaller("count_lines"))

    def __init__(self, parent: Optional[Widget] = None) -> None:
        super().__init__(parent=parent)
        self.update_uniforms(rect=(0, 0, 300, 200))

        self.lines = []

        _check_type(self.width, int)
        _check_type(self.height, int)
        assert self.width > 0 < self.height

        self.resizer = BoxResizer(self)
        self.surface = Texture((self.width, self.height))

        self.scrollbar = Scrollbar(parent=self, axis="VERTICAL")
        self.scrollbar_h = Scrollbar(parent=self, axis="HORIZONTAL")
        self.reset_cache()

    @property
    def width_inner(self) -> int:
        """The width excluding border."""
        return round(self.rect.width_inner)

    @property
    def height_inner(self) -> int:
        """The height excluding border."""
        return round(self.rect.height_inner)

    @property
    def visible_lines(self) -> float:
        """Amount of visible lines."""
        return self.height_inner / self.line_height

    @property
    def bottom(self) -> float:
        """The bottom of the current view in lines."""
        return self.top + self.visible_lines

    @property
    def max_top(self) -> float:
        """The maximum allowed top in lines."""
        return max(0.0, self.num_lines - self.visible_lines)

    @property
    def max_left(self) -> int:
        if self.show_horizontal_scrollbar:
            blf.size(self.font_id, self.font_size,
                     int(_system.dpi * _system.pixel_size))
            if self.lines and isinstance(self.lines[0], TextLine):
                from itertools import repeat
                strings = [l.string for l in self.lines]
                size = max(map(blf.dimensions, repeat(self.font_id), strings))
                max_left = round(size[0] - self.width)
                if self.max_top != 0.0:
                    max_left += self.scrollbar_width
                return max_left
        return 0

    @property
    def line_offset_px(self) -> int:
        """The pixel offset into the current line from top."""
        return round(self.top % 1.0 * self.line_height)

    @property
    def line_height(self) -> int:
        """The line height in pixels."""
        font_id = self.font_id
        # At 1.77 scale, dpi is halved and pixel_size is doubled. Go figure.
        blf.size(font_id, self.font_size, int(_system.dpi * _system.pixel_size))
        # The actual height. Ascender + descender + x-height.
        return int(blf.dimensions(font_id, "Ag")[1] * self.line_padding)

    def get_view_ratio(self, axis: str) -> float:
        """The ratio between displayable lines and total lines."""
        if axis == "VERTICAL":
            return self.height_inner / max(1.0, self.line_height * self.num_lines)
        else:
            return self.width_inner / max(1.0, self.max_left + self.width_inner)

    def get_view_position(self, axis: str) -> float:
        """The current view ratio of a given axis. 0 at top, 1 at bottom."""
        if axis == "VERTICAL":
            return self.top / max(self.max_top, 1.0)
        return self.left / max(self.max_left, 1.0) 

    def reset_cache(self) -> None:
        """Causes the list to redraw its surface next time."""
        self.cache_key = ()
        self.surface.resize(self.surface_size)

    def lines_to_view(self, lines: Union[float, int]) -> float:
        """Transform logical lines to view. Used for scrolling."""
        _check_type(lines, float, int)
        ratio = self.get_view_ratio("VERTICAL")
        if ratio >= 1.0:
            return 0.0
        span = self.height_inner / ratio
        return ((self.line_height / span) / (1.0 - ratio)) * lines

    def set_view(self, value: float, axis: str) -> None:
        """Set view by a value between 0 and 1 (top/bottom respectively).
        Axis must be widgets.HORIZONTAL and/or widgets.VERTICAL.
        """
        _check_type(value, float, int)
        assert axis in {"VERTICAL", "HORIZONTAL", "BOTH"}, "Bad axis"

        if axis in {"VERTICAL", "BOTH"} and self.top != value:
            max_top = self.max_top
            self.top = max(0.0, min(value * max_top, max_top))

        if axis in {"HORIZONTAL", "BOTH"} and self.left != value:
            max_left = self.max_left
            self.left = round(max(0, min(value * max_left, max_left)))
        safe_redraw()
        self.reset_cache()

    def reset_view(self) -> None:
        self.set_view(0, "BOTH")

    def scroll(self, lines: Union[float, int], axis: str) -> None:
        """Scroll the view by logical lines."""

        _check_type(lines, float, int)
        assert axis in {"VERTICAL", "HORIZONTAL"}, "Bad axis"

        if axis == "VERTICAL":
            value = self.get_view_position(axis) + self.lines_to_view(lines)
        else:
            # XXX: Fix
            value = 0
        self.set_view(value, axis)

    def draw(self) -> None:
        # TODO: self.position/self.surface size isn't the right way.
        self.surface.x = int(self.rect.inner_x)
        self.surface.y = int(self.rect.inner_y)
        self.surface.draw()
        self.scrollbar.draw()
        self.scrollbar_h.draw()
        self.resizer.draw()

    @property
    def surface_size(self) -> tuple[int, int]:
        rect = self.rect
        bw = (rect.uniforms.border_width * 2) - 1
        return round(rect[2] - bw), round(rect[3] - bw)

    @property
    def size(self) -> tuple[float, float]:
        return self.rect[2], self.rect[3]

    def get_drawable_lines(self):
        start = int(self.top)
        end = int(start + (self.rect[3] // self.line_height) + 2)
        return islice(self.lines, start, end)
    
    def get_surface(self) -> Texture:
        # There's no scissor/clip as of 3.2, so we draw to an off-screen
        # surface instead. The surface is cached for performance reasons.
        surface = self.surface
        if self.surface_size != surface.size:
            surface.resize(self.surface_size)
        return surface

    def get_text_y(self) -> int:
        line_height = self.line_height
        blf.size(self.font_id, self.font_size, int(_system.dpi * _system.pixel_size))
        # Pad the glyph baseline so the text is centered on the line.
        y_pad = (line_height - blf.dimensions(self.font_id, "x")[1]) // 2

        return int(self.rect.height_inner - line_height + self.line_offset_px + y_pad + self.rect.border_width)

    def _clamp_view(self):
        # Clamp left
        if self.left > 0.0:
            shift = max(0, self.left - self.max_left)
            if shift > 0:
                self.left = max(0, self.left - shift)

        # Clamp top
        if self.top > 0.0:
            shift = max(0.0, self.top - self.max_top)
            if shift > 0.0:
                self.top = max(0, self.top - shift)

    def resize(self, size: tuple[int, int]):
        self.rect.size = size
        self._clamp_view()


class InputAdapter(Adapter):
    def __init__(self, input: "Input"):
        self.input = input

    def get_string(self) -> str:
        return self.input.string

    def get_cursor(self) -> tuple[int]:
        return self.input.anchor, self.input.focus

    def set_cursor(self, cursor):
        self.input.set_cursor(*cursor)

    def set_string(self, data):
        self.input.set_string(data)

    def poll_undo(self):
        return self.input is get_focus()

    def poll_redo(self):
        return self.input is get_focus()


def input_undo(meth):

    @close_cells(meth)
    @set_name(f"{meth.__name__} (undomethod)")
    def wrapper(self: "Input", *args, undo=True, **kw):

        self.state.update_cursor()
        ret = meth(self, *args, **kw)

        if undo:
            self.state.push_undo(tag=meth.__name__)
        return undo
    return wrapper


class Input(TextDraw):
    """A text input widget."""
    width = 300
    height = 200

    font_id = 0
    background_color = 0.15, 0.15, 0.15, 1.0
    border_color     = 0.3, 0.3, 0.3, 1.0

    cursor = 'TEXT'

    anchor = 0
    focus  = 0

    string:     str
    hint:       str

    _hooks_set: bool
    state:      LinearStack

    def __init__(self, parent):
        super().__init__(parent=parent)
        self.string = ""
        self.hint   = ""
        self.caret  = Rect()

        self.caret.update_uniforms(
            background_color=(0.0, 0.85, 0.0, 1.0),
            border_color=(0.0, 1.0, 0.0, 1.0)
        )

        self.selection = Rect()
        color = tuple(_context.preferences.themes[0].user_interface.wcol_text.item)
        self.selection.update_uniforms(background_color=color,
                                       border_color=color)
        self.font_size = 16
        self._hooks_set = False
        from textension.operators import clicks_counter
        self.clicks = clicks_counter()
        self.state = LinearStack(InputAdapter(self))

    def remove_hooks(self):
        if self._hooks_set:
            ED_OT_undo.remove_poll(self.state.poll_undo)
            ED_OT_undo.remove_pre(self.state.pop_undo)

            ED_OT_redo.remove_poll(self.state.poll_redo)
            ED_OT_redo.remove_pre(self.state.pop_redo)

            TEXT_OT_copy.remove_pre(self.copy)
            TEXT_OT_cut.remove_pre(self.cut)
            TEXT_OT_delete.remove_pre(self.delete)
            TEXT_OT_insert.remove_pre(self.write)
            TEXT_OT_move.remove_pre(self.navigate)
            TEXT_OT_move_select.remove_pre(self.navigate)
            TEXT_OT_paste.remove_pre(self.paste)
            TEXT_OT_select_all.remove_pre(self.select_all)
            TEXT_OT_line_break.remove_pre(self.line_break)
            self._hooks_set = False

    def line_break(self):
        # Does nothing for now. Just eat the line break event.
        pass

    def set_hooks(self):
        if not self._hooks_set:
            ED_OT_undo.add_poll(self.state.poll_undo)
            ED_OT_undo.add_pre(self.state.pop_undo)

            ED_OT_redo.add_poll(self.state.poll_redo)
            ED_OT_redo.add_pre(self.state.pop_redo)

            TEXT_OT_copy.add_pre(self.copy)
            TEXT_OT_cut.add_pre(self.cut)
            TEXT_OT_delete.add_pre(self.delete)
            TEXT_OT_insert.add_pre(self.write)
            TEXT_OT_move.add_pre(self.navigate)
            TEXT_OT_move_select.add_pre(self.navigate)
            TEXT_OT_paste.add_pre(self.paste)
            TEXT_OT_select_all.add_pre(self.select_all)
            TEXT_OT_line_break.add_pre(self.line_break)
            self._hooks_set = True

    def set_hint(self, string: str):
        _check_type(string, str)
        self.hint = string

    def set_string(self, string: str, select: bool = False):
        _check_type(string, str)
        self.string = string
        if select:
            self.set_cursor(0, len(string))

    def hit_test_column(self, x: int):
        blf.size(self.font_id, self.font_size)
        string = self.string

        pos = len(string)
        span = blf.dimensions(self.font_id, string)[0]
        if x >= span:
            return pos
        
        from itertools import repeat
        from operator import itemgetter
        a = map(itemgetter(0), map(blf.dimensions, repeat(self.font_id), string))

        span = 0
        for index, width in enumerate(a):
            if span + (width / 2) >= x:
                return index
            span += width
        return len(string)

    def draw(self):
        region = _context.region
        h = self.parent.rect.height
        y = (region.height - h) // 2
        w = self.parent.rect.width
        pad = 2
        x = self.parent.x + pad  # X start of input
        left_pad = 50

        self.rect.draw(x + left_pad,
                       y + pad,
                       w - (pad * 2) - left_pad,
                       h - (pad * 2))

        blf.size(self.font_id, self.font_size)

        cx = x + self.focus_x
        cy = y + pad
        cw = 1  # Caret width
        ch = h - (pad * 2)

        y_pad = (h - blf.dimensions(self.font_id, "x")[1]) // 2
        x_pad = y_pad * 0.2

        if self.is_focused:
            caret_x = int(cx + left_pad + x_pad)

            # Draw the selection.
            if self.anchor != self.focus:
                sx, sw = self.get_selection_offsets()
                color = tuple(_context.preferences.themes[0].user_interface.wcol_text.item)
                self.selection.update_uniforms(background_color=color,
                                               border_color=color)
                self.selection.draw(x + left_pad + sx + 1, cy + 1, sw + x_pad - 2, ch - 2)

            # Draw the caret.
            self.caret.draw(caret_x, cy + 2, cw, ch - 4)

        # Draw the string.
        if string := self.string:
            blf.color(self.font_id, 1, 1, 1, 1)
        else:
            string = self.hint
            left_pad += 3
            blf.color(self.font_id, 0.5, 0.5, 0.5, 1.0)
        blf.position(self.font_id, x + left_pad + x_pad, y + y_pad, 0)
        blf.draw(self.font_id, string)

    def on_parent_focus(self, select=True):
        if select:
            self.select_all()

    def get_selection_offsets(self):
        start, end = self.range
        blf.size(self.font_id, self.font_size)
        x = blf.dimensions(self.font_id, self.string[:start])[0]
        span = blf.dimensions(self.font_id, self.string[start:end])[0]
        return x, span

    # The local x-coordinate of the cursor focus.
    @property
    def focus_x(self):
        blf.size(self.font_id, self.font_size)
        return blf.dimensions(self.font_id, self.string[:self.focus])[0]

    # The sorted selection range.
    @property
    def range(self):
        return sorted((self.anchor, self.focus))

    @property
    def is_focused(self) -> bool:
        return self is get_focus()

    def on_activate(self):
        set_focus(self)
        if _context.window.event.type_string == 'LEFTMOUSE':
            bpy.ops.textension.ui_input_set_cursor('INVOKE_DEFAULT')

    def on_focus(self):
        self.set_hooks()
        self.update_uniforms(border_color=(0.5, 0.5, 0.5, 1.0))
        safe_redraw()

    def on_defocus(self):
        self.remove_hooks()
        self.update_uniforms(border_color=(0.3, 0.3, 0.3, 1.0))
        # When de-focused, selection is removed.
        if self.selected_text == self.string:
            self.set_cursor(len(self.string))
        safe_redraw()

    def set_cursor(self, start: int, end: int = None):
        if end is None:
            end = start

        self.set_anchor(start)
        self.set_focus(end)
        safe_redraw()

    def set_focus(self, focus):
        self.focus = max(0, min(len(self.string), focus))

    def set_anchor(self, anchor):
        self.anchor = max(0, min(len(self.string), anchor))

    @input_undo
    def write(self, s: str):
        start, end = self.range
        tmp = list(self.string)
        tmp[start:end] = s
        self.string = "".join(tmp)
        self.set_cursor(start + len(s))
        safe_redraw()

    @input_undo
    def delete(self, mode: str):
        start, end = self.range
        self._delete(mode)
        self.set_cursor(start)
        safe_redraw()

    def _delete(self, mode: str):
        start, end = self.range
        string = self.string

        if start == end:
            if mode == 'PREVIOUS_CHARACTER':
                start = max(0, start - 1)
            elif mode == 'PREVIOUS_WORD':
                start -= find_word_boundary(string[:start][::-1])
            elif mode == 'NEXT_CHARACTER':
                end += 1
            elif mode == 'NEXT_WORD':
                end += find_word_boundary(string[end:])
        tmp = list(string)
        del tmp[start:end]
        self.string = "".join(tmp)


    def navigate(self, mode: str, select=False):
        _check_type(mode, str)
        _check_type(select, bool)

        start, end = self.range
        if mode == 'LINE_BEGIN':
            rel_index = -self.focus
            abs_index = 0
        elif mode == 'LINE_END':
            rel_index = len(self.string) - self.focus
            abs_index = len(self.string)
        elif mode == 'PREVIOUS_CHARACTER':
            rel_index = -1
            abs_index = start + rel_index
        elif mode == 'NEXT_CHARACTER':
            rel_index = 1
            abs_index = end + rel_index
        elif mode == 'PREVIOUS_WORD':
            rel_index = -find_word_boundary(self.string[:self.focus][::-1])
            abs_index = start + rel_index
        elif mode == 'NEXT_WORD':
            rel_index = find_word_boundary(self.string[self.focus:])
            abs_index = end + rel_index
        else:
            # Other modes unhandled for now (top, bottom, page up, page down).
            return

        if select:
            self.set_focus(self.focus + rel_index)
        else:
            self.set_cursor(abs_index)
        safe_redraw()

    def select_all(self):
        self.set_cursor(0, len(self.string))
        safe_redraw()

    @property
    def selected_text(self):
        start, end = self.range
        return self.string[start:end]

    def copy(self):
        if string := self.selected_text:
            _context.window_manager.clipboard  = string

    @input_undo
    def paste(self):
        try:
            string = str(_context.window_manager.clipboard)
        except:
            raise
        self.write(string, undo=False)

    def get_cursor(self):
        return self.anchor, self.focus

    @input_undo
    def cut(self):
        self.copy()
        if self.selected_text:
            self._delete('PREVIOUS_CHARACTER')
            safe_redraw()


class OverlayRect(Rect):
    index = -1

    def set_index(self, index, redraw_on_modify=True):
        if self.index != index:
            self.index = index
            if redraw_on_modify:
                safe_redraw()


class ListBox(TextDraw):
    """ListBox draws a list of ListEntry widgets."""
    parent:      Widget
    lines:       tuple[ListEntry]

    view_key:    int = 0
    cache_key:   tuple

    active: OverlayRect
    hover:  OverlayRect

    active_background_color = 0.16, 0.22, 0.33, 1.0
    active_border_color     = 0.16, 0.29, 0.5,  1.0
    active_border_width     = 1.0

    hover_background_color  = 1.0, 1.0, 1.0, 0.1
    hover_border_color      = 1.0, 1.0, 1.0, 0.4
    hover_border_width      = 1.0

    text_padding            = 5

    width  = 300
    height = 200

    font_id = 1

    def __init__(self, parent: Widget = None):
        super().__init__(parent=parent)

        # The selection rectangle.
        self.active = OverlayRect()
        self.active.update_uniforms(
            background_color=self.active_background_color,
            border_color=self.active_border_color,
            border_width=self.active_border_width,
            corner_radius=self.corner_radius)

        # The hover rectangle.
        self.hover = OverlayRect()
        self.hover.update_uniforms(
            background_color=self.hover_background_color,
            border_color=self.hover_border_color,
            border_width=self.hover_border_width,
            corner_radius=self.corner_radius)

    @property
    def content_height(self):
        return (len(self.lines) * self.line_height)

    @property
    def x(self) -> int:
        return round(self.rect.inner_x)

    @property
    def y(self) -> int:
        return round(self.rect.inner_y)

    # TODO: These are wrong. Should be inner versions.
    @property
    def width(self) -> int:
        return round(self.rect.width_inner)
    @property
    def height(self) -> int:
        return round(self.rect.height_inner)

    @property
    def start_x(self):
        return self.text_padding * _system.wu * 0.05

    def on_leave(self) -> None:
        self.hover.set_index(-1)

    def on_activate(self) -> None:
        print("ListBox clicked")
        # self.active.index = self.hover.index

    def draw(self) -> None:
        if self._validate_view():
            self.active.set_index(0, redraw_on_modify=False)
            # self.hover.set_index(-1, redraw_on_modify=False)

        surface = self.get_surface()

        text_x = self.start_x
        line_height = self.line_height
        cache_key = (self.line_offset_px, self.view_key, line_height, surface.size, text_x)

        # The surface must be redrawn.
        if cache_key != self.cache_key:
            self.cache_key = cache_key

            text_y = self.get_text_y()
            with self.surface.bind():
                for entry in self.get_drawable_lines():
                    self.draw_entry(entry, text_x, text_y)
                    text_y -= line_height

        self.draw_overlays()
        super().draw()  # TextDraw.draw()

    def draw_overlays(self):
        """Draw hover and selection rectangle overlays."""

        line_height = self.line_height
        x, y_start  = self.position_inner

        width, height_start = self.size_inner

        y_top = y_start + height_start

        # An overlay's origin y starts from the top, minus one line.
        origin = y_top - line_height + self.line_offset_px

        top = int(self.top)
        bottom = top + int(self.visible_lines) + 1

        for overlay in (self.active, self.hover):
            if top <= overlay.index <= bottom:
                y = origin - line_height * (overlay.index - top)
                height = min(line_height, y_top - y, y - y_start + line_height)
                overlay.draw(x, max(y_start, y), width, height)

    # This exists so subclasses can override for custom entry drawing.
    def draw_entry(self, entry: ListEntry, x: int, y: int):
        self.draw_string(entry.string, x, y)

    def draw_string(self, string: str, x: int, y: int):
        blf.position(self.font_id, x, y, 0)
        blf.color(self.font_id, *self.foreground_color)
        blf.draw(self.font_id, string)

    def _validate_view(self) -> bool:
        # If ``self.lines`` changes, reset top, hover and selection.
        # TODO: This isn't a good check.
        # TODO: ``lines`` should be mutable while still allowing view invalidation.
        if id(self.lines) != self.view_key:
            self.view_key = id(self.lines)
            self.top = 0.0
            return True
        return False

    @set_name("hit_test (ListBox)")
    def hit_test(self, x, y):
        result = super().hit_test(x, y)
        # The ListBox is hit.
        if result is self:
            if 0 <= (x - self.surface.x) <= self.width:
                # The view-space hit index
                hit = self.top + (self.y + self.height_inner - y) / self.line_height
                # Mouse is inside vertically
                if self.top <= hit < min(self.num_lines, self.bottom):
                    self.hover.set_index(int(hit))
                    return self
            return HIT_BLOCK
        return result


class _Margins(tuple):
    left   = _named_index(0)
    top    = _named_index(1)
    right  = _named_index(2)
    bottom = _named_index(3)

    @soft_property
    def vertical(self, obj, objclass=None):
        value = obj.top + obj.bottom
        obj.vertical = value
        return value

    @soft_property
    def horizontal(self, obj, objclass=None):
        value = self[0] + self[2]
        obj.horizontal = value
        return value


class TextView(TextDraw):
    parent: Widget

    width  = 350
    height = 450

    background_color = 0.2, 0.2, 0.2, 1.0
    border_color     = 0.3, 0.3, 0.3, 1.0

    lines: list[TextLine]

    margins: _Margins = _Margins((0, 0, 0, 0))  # Left, right, top, bottom.

    cached_string: str = ""
    cache_key:   tuple
    font_id = 0

    use_word_wrap: bool = True

    def _update_lines(self) -> list[str]:
        if self.use_word_wrap:
            max_width = self.width - self.scrollbar_width
            lines = wrap_string(self.cached_string, max_width, self.font_size, self.font_id)
        else:
            lines = self.cached_string.splitlines()
        self.lines[:] = map(TextLine, lines)

    def set_margins(self, **kw) -> None:
        """Set the margins of the TextView object.

        Possible keywords: left, top, right and bottom.

        Example: set_margins(left=10)
        """
        new_margins = dict(zip(("left", "top", "right", "bottom"), self.margins)) | kw
        try:
            self.margins = _Margins(map(int, _get_margins(new_margins)))
        except:
            raise TypeError("Keyword arguments not convertible to int")

    def set_from_string(self, string: str):
        self.cached_string = string
        self._update_lines()
        self.reset_view()

    def __init__(self, parent: Widget):
        super().__init__(parent=parent)
        self.rect.size = (250, 100)
        self.set_margins(left=8, top=8, right=8, bottom=8)

    @property
    def width(self):
        return round(self.rect.width)
    @property
    def height(self):
        return round(self.rect.height)

    def draw(self):
        rect = self.rect
        p_rect = self.parent.rect
        w, h = rect.size

        x, y = p_rect.position + p_rect.size

        rect.draw(x, y - h, w, h)  # Draw the background.
        self.draw_text()           # Draw the text.
        super().draw()             # Draw the surface.

    def get_text_y(self):
        y = super().get_text_y() - self.margins[1]

        # ``get_drawable_lines`` accounts for margins which means the top can
        # be negative. If so, text_y must be offset so the drawable lines fit
        # within the margin. Otherwise the lines get clipped in view.
        a = int(max(0, self.top - self.margin_line_offset))
        return y + int(int(self.top) - a) * self.line_height

    def get_text_x(self):
        return -self.left + self.margins[0]

    @property
    def margin_line_offset(self):
        return self.margins.vertical / self.line_height

    @property
    def num_lines(self):
        return super().num_lines + self.margin_line_offset

    @property
    def max_top(self) -> float:
        # If we have a vertical margin, we need to add it to max top.
        if offset := self.margin_line_offset:
            return max(0.0, self.count_lines() - self.visible_lines + offset)
        return super().max_top

    def get_drawable_lines(self):
        # print(self.top - self.margin_line_offset)
        start = int(max(0, self.top - self.margin_line_offset))
        end = int(start + (self.rect[3] // self.line_height) + 2)
        return islice(self.lines, start, end)

    def draw_text(self):
        surface = self.get_surface()

        line_height = self.line_height
        text_y = self.get_text_y()
        text_x = self.get_text_x()
        with surface.bind():
            blf.color(self.font_id, 1, 1, 1, 1)
            for line in self.get_drawable_lines():
                blf.position(self.font_id, text_x, text_y, 0)
                blf.draw(self.font_id, line.string)
                text_y -= line_height

    def resize(self, size: tuple[int, int]):
        self.rect.size = size
        self._update_lines()
        self._clamp_view()

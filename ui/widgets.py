# This module implements widgets.

from textension.utils import bl_cursor_types, _check_type, _forwarder, \
    safe_redraw, test_and_update, _system, _context, _call, LinearStack, Adapter, Step, close_cells, set_name
from .gl import Rect, Texture
from textension.core import find_word_boundary
from typing import Optional, Union
from itertools import islice
from textension.ui.utils import set_focus, get_focus

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

    def __init__(self, parent: Optional["Widget"] = None):
        _check_type(parent, Widget, type(None))

        if self.cursor not in bl_cursor_types:
            raise ValueError(f"Cursor '{self.cursor}' not in {bl_cursor_types}")

        self.children = []
        if parent is not None:
            parent.children += self,

        self.parent = parent
        self.rect   = Rect()
        self.rect.widget = self

        self.update_uniforms(
            background_color=self.background_color,
            border_color    =self.border_color,
            border_width    =self.border_width,
            corner_radius   =self.corner_radius)
        
    def hit_test(self, x: float, y: float) -> bool:
        """Hit test and return the most refined result, which
        can be this Widget, any of its children, or None.
        """
        r = self.rect
        if 0.0 <= x - r.x <= r.width and 0.0 <= y - r.y <= r.height:
            for c in self.children:
                if ret := c.hit_test(x, y):
                    return ret
            return self
        return None

    def update_uniforms(self, **kw):
        self.rect.update_uniforms(**kw)

    def on_enter(self):
        """Called when the mouse enters the Widget."""

    def on_leave(self):
        """Called when the mouse leaves the Widget."""

    def on_activate(self):
        """Called when the Widget is activated by pressing it."""

    x = _forwarder("rect.x")
    y = _forwarder("rect.y")

    width  = _forwarder("rect.width")
    height = _forwarder("rect.height")


# Used as a no-hit result when we just want to block.
HIT_BLOCK = Widget()


class Thumb(Widget):
    """Scrollbar thumb."""

    parent: "Scrollbar"

    background_color = 0.24, 0.24, 0.24, 1.0
    border_color     = 0.32, 0.32, 0.32, 1.0

    def __init__(self, scroll: "Scrollbar"):
        _check_type(scroll, Scrollbar)
        super().__init__(parent=scroll)

    def set_highlight(self, state: bool):
        mul = 0.05 * float(state)
        self.rect.border_color = [clamp(v + mul) for v in self.border_color]
        self.rect.background_color = [clamp(v + mul) for v in self.background_color]
        safe_redraw()

    def on_enter(self):
        self.set_highlight(True)

    def on_leave(self):
        self.set_highlight(False)

    def on_activate(self):
        bpy.ops.textension.ui_scrollbar('INVOKE_DEFAULT')


class Scrollbar(Widget):
    """Scrollbar for TextDraw objects."""

    parent: "TextDraw"
    thumb:   Thumb

    # These are for the scrollbar gutter (invisible).
    background_color = 0.0, 0.0, 0.0, 0.0
    border_color     = 0.0, 0.0, 0.0, 0.0

    thumb_offsets    = (0, 0)

    # For overriding textension.ui_scroll_lines behavior.
    @property
    def is_passthrough(self):
        return False

    def __init__(self, parent: "TextDraw"):
        _check_type(parent, TextDraw)

        super().__init__(parent=parent)
        self.thumb = Thumb(scroll=self)

        self.thumb.update_uniforms(
            background_color=parent.scrollbar_thumb_background_color,
            corner_radius   =parent.corner_radius,
            border_color    =parent.scrollbar_thumb_border_color,
            border_width    =parent.scrollbar_thumb_border_width)

        self.update_uniforms(
            background_color=parent.scrollbar_background_color,
            corner_radius   =parent.corner_radius,
            border_color    =parent.scrollbar_border_color)

    def on_activate(self):
        bpy.ops.textension.ui_scroll_lines('INVOKE_DEFAULT')

    def set_view(self, ratio: float):
        """Transform the parent's view, aka scroll. Between 0-1."""
        self.parent.set_view(ratio)

    def draw(self):
        """Draw the scrollbar."""
        parent = self.parent
        offset_y, height = self._compute_offsets()

        # Width is defined on parent and multiplied by ui scale.
        width = parent.scrollbar_width * (_system.wu * 0.05)

        # Position the scrollbar to the parent's right edge.
        x, y = parent.get_position()
        x += parent.width - width

        self.rect.draw(x, y, width, parent.height)

        if height > 0:
            self.thumb.rect.draw(x, y + offset_y, width, height)

    def _compute_offsets(self) -> tuple[int, int]:
        """Calculates the thumb's vertical offset and height."""
        parent = self.parent
        view_ratio = parent.get_view_ratio()
        parent_height = parent.height

        if view_ratio < 1.0:
            height = max(view_ratio * parent_height, min(parent_height, 30))
            pos = (parent_height - height) * (1.0 - self.parent.get_view_top())
            self.thumb_offsets = round(pos), round(height)
        else:
            self.thumb_offsets = (0, 0)
        return self.thumb_offsets

    @property
    def difference_height(self):
        """The difference in height between the parent and the thumb."""
        return self.parent.height - self._compute_offsets()[1]

    @property
    def thumb_y(self) -> int:
        """The y coordinate of the thumb."""
        return self._compute_offsets()[0]

    def hit_test(self, x, y):
        if self.thumb_offsets != (0, 0):
            return super().hit_test(x, y)
        return None


class TextLine:
    __slots__ = ("string",)

    # The string this line holds.
    string: str

    def __init__(self, string):
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

    def __init__(self, parent: Widget, subject=None):
        if self.axis not in ('VERTICAL', 'HORIZONTAL'):
            raise ValueError(f"axis in 'VERTICAL' or 'HORIZONTAL'")
        super().__init__(parent=parent)
        self.subject = subject

    def set_alpha(self, value):
        if test_and_update(self.rect.background_color, "w", value):
            safe_redraw()

    def on_enter(self):
        if self.show_resize_handles:
            self.set_alpha(1.0)

    def on_leave(self):
        self.set_alpha(0.0)

    def on_activate(self):
        bpy.ops.textension.ui_resize('INVOKE_DEFAULT', axis=self.axis)

    def draw(self, x, y, w, h):
        if self.rect.background_color.w > 0.0:
            self.rect.draw(x, y, w, h)
        else:
            # Just update the rectangle.
            self.rect[:] = x, y, w, h

    def get_subject(self):
        return self.subject or self.parent


class VerticalResizer(EdgeResizer):
    cursor  = 'MOVE_Y'
    axis    = 'VERTICAL'

    def __init__(self, parent: Widget, subject=None):
        super().__init__(parent=parent, subject=subject)


class HorizontalResizer(EdgeResizer):
    cursor  = 'MOVE_X'
    axis    = 'HORIZONTAL'

    def __init__(self, parent: Widget, subject=None):
        super().__init__(parent=parent, subject=subject)


class BoxResizer(Widget):
    """Allows resizing the lower right corner of a box."""

    # The corner resizer isn't actually drawn.
    background_color = 0.0, 0.0, 0.0, 0.0
    border_color     = 0.0, 0.0, 0.0, 0.0

    sizers: tuple[HorizontalResizer, VerticalResizer]

    cursor = 'SCROLL_XY'

    def __init__(self, parent: Widget):
        super().__init__(parent=parent)
        self.sizers = (HorizontalResizer(parent=self, subject=parent),
                       VerticalResizer(parent=self, subject=parent))

    def hit_test(self, x, y):
        if super().hit_test(x, y):
            return self
        horz, vert = self.sizers
        return horz.hit_test(x, y) or vert.hit_test(x, y)

    def on_enter(self):
        # Entering the BoxResizer highlights both sizers.
        for sizer in self.sizers:
            sizer.on_enter()

    def on_leave(self):
        for sizer in self.sizers:
            sizer.on_leave()

    def draw(self):
        x, y, w, h = self.parent.rect
        horz, vert = self.sizers
        t = 4

        vert.draw(x, y, w, t)
        horz.draw(x + w - t, y, t, h)

        t *= 3
        self.rect[:] = x + w - t, y, t, t

    def on_activate(self):
        bpy.ops.textension.ui_resize('INVOKE_DEFAULT', axis='CORNER')

    def get_subject(self):
        return self.parent


class TextDraw(Widget):
    """Implements functionality for drawing non-rich text."""

    font_size: int = 16
    font_id:   int = 0

    top:       float = 0.0

    width:     int = 1
    height:    int = 1

    lines: list[TextLine]
    foreground_color = 0.4,  0.7, 1.0,  1.0
    line_padding     = 1.25

    scrollbar_width = 16

    scrollbar_background_color = 0.0, 0.0, 0.0, 0.0
    scrollbar_border_color     = 0.0, 0.0, 0.0, 0.0
    scrollbar_border_width     = 1

    scrollbar_thumb_background_color = 0.24, 0.24, 0.24, 1.0
    scrollbar_thumb_border_color     = 0.32, 0.32, 0.32, 1.0
    scrollbar_thumb_border_width     = 1

    def __init__(self, parent: Optional[Widget] = None):
        super().__init__(parent=parent)
        self.update_uniforms(rect=(0, 0, 300, 200))

        self.lines = []

        _check_type(self.width, int)
        _check_type(self.height, int)
        assert self.width > 0 < self.height

        self.resizer = BoxResizer(self)
        self.surface = Texture((self.width, self.height))

        self.scrollbar = Scrollbar(parent=self)
        self.reset_cache()

    @property
    def visible_lines(self) -> float:
        """The amount of lines visible in view."""
        return self.height / self.line_height

    @property
    def bottom(self) -> float:
        """The bottom of the current view in lines."""
        return self.top + self.visible_lines

    @property
    def max_top(self) -> int:
        """The maximum allowed top in lines."""
        return max(0, self.num_lines - self.visible_lines)

    @property
    def line_offset_px(self):
        """The pixel offset into the current line from top."""
        return int(self.top % 1.0 * self.line_height)

    @property
    def line_height(self):
        """The line height in pixels."""
        font_id = self.font_id
        # At 1.77 scale, dpi is halved and pixel_size is doubled. Go figure.
        blf.size(font_id, self.font_size, int(_system.dpi * _system.pixel_size))
        # The actual height. Ascender + descender + x-height.
        # NOTE: This returns a float, but when computing 
        return int(blf.dimensions(font_id, "Ag")[1] * self.line_padding)

    @property
    def num_lines(self):
        return len(self.lines)

    def get_view_ratio(self) -> float:
        """The ratio between displayable lines and total lines."""
        return self.height / max(1.0, self.line_height * self.num_lines)

    def get_view_top(self) -> float | None:
        """The current view ratio of top. 0.0 at top, 1.0 at bottom."""
        return self.top / max(self.max_top, 1.0) 

    # So it can be overridden.
    def get_position(self):
        rect = self.rect
        bw = rect.uniforms.border_width
        return rect[0] + bw, rect[1] + bw

    def reset_cache(self):
        """Causes the list to redraw its surface next time."""
        self.cache_key = ()
        self.surface.resize(self.surface_size)

    def lines_to_view(self, lines: Union[float, int]) -> float:
        """Transform logical lines to view. Used for scrolling."""
        _check_type(lines, float, int)
        ratio = self.get_view_ratio()
        if ratio >= 1.0:
            return 0.0
        span = self.height / ratio
        return ((self.line_height / span) / (1.0 - ratio)) * lines

    def set_top(self, top: float) -> None:
        """Set view by line number (zero based)."""
        _check_type(top, float, int)
        if test_and_update(self, "top", max(0, min(top, self.max_top))):
            safe_redraw()

    def set_view(self, value: float) -> None:
        """Set view by a value between 0 and 1 (top/bottom respectively)."""
        _check_type(value, float, int)
        max_top = self.max_top
        self.set_top(max(0, min(value * max_top, max_top)))
        self.reset_cache()

    def scroll(self, lines: Union[float, int]):
        """Scroll the view by logical lines."""
        _check_type(lines, float, int)
        self.set_view(self.get_view_top() + self.lines_to_view(lines))

    def draw(self):
        # TODO: self.position/self.surface size isn't the right way.
        self.surface.x = int(self.rect.inner_x)
        self.surface.y = int(self.rect.inner_y)
        self.surface.draw()
        self.scrollbar.draw()
        self.resizer.draw()

    @property
    def surface_size(self) -> tuple[int, int]:
        rect = self.rect
        bw = (rect.uniforms.border_width * 2) - 1
        return round(rect[2] - bw), round(rect[3] - bw)

    @property
    def size(self):
        return self.rect[2], self.rect[3]

    def get_drawable_lines(self):
        start = int(self.top)
        end = int(start + (self.rect[3] // self.line_height) + 2)
        return islice(self.lines, start, end)
    
    def get_surface(self):
        # There's no scissor/clip as of 3.2, so we draw to an off-screen
        # surface instead. The surface is cached for performance reasons.
        surface = self.surface
        if self.surface_size != surface.size:
            surface.resize(self.surface_size)
        return surface

    def get_text_y(self):
        line_height = self.line_height
        # Pad the glyph baseline so the text is centered on the line.
        y_pad = (line_height - blf.dimensions(self.font_id, "x")[1]) // 2
        y = self.rect.height - line_height + self.line_offset_px + y_pad
        return y


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

        self.state.push_intermediate_cursor()
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
    def x(self) -> int:
        return round(self.rect.inner_x)

    @property
    def y(self) -> int:
        return round(self.rect.inner_y)

    @property
    def width(self) -> int:
        return round(self.rect.inner_width)

    @property
    def height(self) -> int:
        return round(self.rect.inner_height)

    @property
    def start_x(self):
        return self.text_padding * _system.wu * 0.05

    def on_leave(self) -> None:
        self.hover.set_index(-1)

    def on_activate(self) -> None:
        print("ListBox clicked")
        # self.active.index = self.hover.index

    def draw(self) -> None:
        # TODO: This should be unnecessary. Stuff modifying ``top`` should
        # ensure the top is invalidated.
        self._validate_top()

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

    # Draw hover and selection rectangles.
    def draw_overlays(self):
        init_rh = self.line_height
        rx, y = self.get_position()
        rw, h = self.surface_size

        # TODO: Why is this needed?
        y -= 1
        rw -= 1

        # The overlay rect y starts at the top of the list.
        y_start = y + h + self.line_offset_px - init_rh
        view_bottom = int(self.visible_lines) + 1

        for rect in (self.active, self.hover):
            view_index = rect.index - int(self.top)
            if view_index < 0 or view_index > view_bottom:
                continue

            ry = y_start - (init_rh * view_index)
            rh = min(init_rh, y + h - ry)
            rh = min(rh, init_rh - (y - ry)) - 1
            rect.draw(rx, max(ry, y) + 1, rw, rh)

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

    def _validate_top(self):
        if self.top > self.max_top:
            self.top = self.max_top
            return True
        return False

    def hit_test(self, x, y):
        result = super().hit_test(x, y)
        # The ListBox is hit.
        if result is self:
            if 0 <= (x - self.surface.x) <= self.width:
                # The view-space hit index
                hit = self.top + (self.y + self.height - y) / self.line_height
                # Mouse is inside vertically
                if self.top <= hit < min(self.num_lines, self.bottom):
                    self.hover.set_index(int(hit))
                    return self
            return HIT_BLOCK
        return result





class TextView(TextDraw):
    parent: Widget

    width  = 350
    height = 450

    background_color = 0.2, 0.2, 0.2, 1.0
    border_color     = 0.3, 0.3, 0.3, 1.0

    lines: list[TextLine]

    cache_key:   tuple
    font_id = 0

    def set_from_string(self, string: str = ()):
        if string.__class__ is str:
            string = map(TextLine, string.splitlines())
        self.lines[:] = string
        self.set_view(0.0)

    def __init__(self, parent: Widget):
        super().__init__(parent=parent)
        self.rect.height = 100
        self.rect.width = 250

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

        x = p_rect.x + p_rect.width
        y = p_rect.y + p_rect.height - h

        rect.draw(x, y, w, h)   # Draw the background.
        self.draw_text()        # Draw the text.
        super().draw()          # Draw the surface.

    def draw_text(self):
        surface = self.get_surface()

        line_height = self.line_height
        text_y = self.get_text_y()

        with surface.bind():
            blf.color(self.font_id, 1, 1, 1, 1)
            for line in self.get_drawable_lines():
                blf.position(self.font_id, 0, text_y, 0)
                blf.draw(self.font_id, line.string)
                text_y -= line_height

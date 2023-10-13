"""This module implements widgets."""

from textension.utils import _check_type, _forwarder, _system, _context, \
    safe_redraw, close_cells, inline, set_name, UndoStack, Adapter, \
    soft_property, _named_index, defaultdict_list, Variadic, _variadic_index, \
    filtertrue, consume, map_not, classproperty, lazy_overwrite, blf_size
from textension.ui.utils import set_widget_focus, get_widget_focus, runtime
from textension.ui.gl import Rect, Texture
from textension.core import find_word_boundary

from functools import partial
from itertools import islice, compress, repeat
from operator import methodcaller, itemgetter
from typing import Optional, Union, TypeVar, Iterable, Type
import weakref

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

_T = TypeVar("_T")


def noop(method):
    return None.__init__


@inline
def wrap_string(string:    str,
                max_width: int,
                font_size: int,
                font_id:   int) -> list[str]:

    from itertools import repeat
    from functools import partial
    from builtins import zip, map, enumerate
    from ..utils import instanced_default_cache

    @inline
    def join_space(strings):
        return " ".join

    @instanced_default_cache
    def dimensions_func_cache(self: dict, font_id):
        return self.setdefault(font_id, partial(map, blf.dimensions, repeat(font_id)))

    split = str.split
    expandtabs = str.expandtabs

    def wrap_string(string, max_width, font_size, font_id):
        wrapped = []

        if "\t" in string:
            string = expandtabs(string, 4)

        lines = split(string, "\n")
        blf_size(font_id, font_size)
        map_dimensions = dimensions_func_cache[font_id]
        (space, _), = map_dimensions(" ")

        for line, (width, _) in zip(lines, map_dimensions(lines)):
            if width > max_width:
                tmp = []
                words = split(line, " ")
                remaining = max_width

                for word, (width, _) in zip(words, map_dimensions(words)):
                    if width <= remaining:
                        remaining -= width + space  # Word + " ".
                        tmp += word,

                    else:
                        if tmp:
                            wrapped += join_space(tmp),

                        if width <= max_width:
                            remaining = max_width - width - space
                            tmp = [word]

                        else:
                            i = 0
                            start = 0
                            remaining = max_width
                            for i, (width, _) in enumerate(map_dimensions(word)):
                                if width <= remaining:
                                    remaining -= width
                                else:
                                    remaining = max_width - width
                                    wrapped += word[start:i],
                                    start = i

                            if start != i:  # type: ignore
                                remaining -= width + space
                                tmp = [word[start:]]
                            else:
                                remaining = max_width
                                tmp = [word[i:]]

                if tmp:
                    wrapped += join_space(tmp),
                continue

            wrapped += line,
        return wrapped
    return wrap_string


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

    shadow           = 0.0, 0.0, 0.0, 0.0

    # The cursor to set when the Widget is hit tested.
    cursor           = 'DEFAULT'

    # For convenience. Allows using forwarders.
    context          = _context

    _instance_refs: dict["Widget", list["Widget"]] = defaultdict_list()

    @classproperty
    def instances(cls: Type["Widget"]) -> Iterable["Widget"]:
        """Return a generator of active instances for this Widget."""
        refs = cls._instance_refs[cls]
        valid = list(map(weakref.ref.__call__, refs))
        # Remove dead references.
        consume(map(refs.remove, compress(refs, map_not(valid))))
        yield from filtertrue(valid)

    def __init__(self, parent: Optional["Widget"] = None) -> None:
        _check_type(parent, Widget, type(None))
        assert self.cursor in cursor_types, self.cursor

        self._instance_refs[self.__class__] += weakref.ref(self),

        self.children = []
        if parent is not None:
            parent.children += self,

        self.parent = parent
        self.rect   = Rect()
        self.update_from_defaults()

    def update_from_defaults(self):
        self.update_uniforms(
            background_color=self.background_color,
            border_color    =self.border_color,
            border_width    =self.border_width,
            corner_radius   =self.corner_radius,
            shadow          =self.shadow)

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

    position       = _forwarder("rect.position", rtype=tuple[float, float])
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
        self.rect.border_color = tuple(clamp(v + mul) for v in self.border_color)
        self.rect.background_color = tuple(clamp(v + mul) for v in self.background_color)
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
        width = parent.scrollbar_width * runtime.wu_norm

        if self.axis == "VERTICAL":
            # Width is defined on parent and multiplied by ui scale.
            # Position the scrollbar to the parent's right edge.
            x += parent.width_inner - width
            self.rect.draw(x, y, width, parent.height)

            if span_px > 0:
                self.thumb.rect.draw(x, y + offset, width, span_px)
        else:
            height = width
            width = parent.width
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


class TextLine(Variadic):

    # The string this line holds.
    string: str = _variadic_index(0)


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

    def set_alpha(self, value: float) -> None:
        color = self.rect.background_color
        if color.w != value:
            color.w = value
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

    cache_key = ()

    top:     float = 0.0
    left:      int = 0

    line_height: int

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

    show_scrollbar: bool             = True
    show_horizontal_scrollbar: bool  = True

    @inline
    def count_lines(self) -> int:
        return _forwarder("lines.__len__")

    num_lines: int = property(methodcaller("count_lines"))

    default_width  = 260
    default_height = 158

    def __init__(self, parent: Optional[Widget] = None) -> None:
        super().__init__(parent=parent)
        self.update_uniforms(rect=(0, 0, self.default_width, self.default_height))

        self.lines   = []
        self.resizer = BoxResizer(self)
        self.surface = Texture(self.rect.size_inner)

        self.scrollbar   = Scrollbar(parent=self, axis="VERTICAL")
        self.scrollbar_h = Scrollbar(parent=self, axis="HORIZONTAL")

        self.line_height = 1
        self.height_inner = 1
        self.width_inner = 1
        self.line_offset_px = 0
        self.map_delattrs = partial(map, self.__delattr__, ("line_height", "height_inner", "width_inner", "line_offset_px"))

    def set_corner_radius(self, new_radius):
        for widget in (self, self.scrollbar, self.scrollbar.thumb):
            widget.update_uniforms(corner_radius=new_radius)

    @lazy_overwrite
    def width_inner(self) -> int:
        """The width excluding border."""
        return round(self.rect.width_inner)

    @lazy_overwrite
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
            blf_size(self.font_id, self.font_size)
            if self.lines and isinstance(self.lines[0], TextLine):
                from itertools import repeat
                strings = [l.string for l in self.lines]
                size = max(map(blf.dimensions, repeat(self.font_id), strings))
                max_left = round(size[0] - self.width)
                if self.max_top != 0.0:
                    max_left += self.scrollbar_width
                return max_left
        return 0

    @lazy_overwrite
    def line_offset_px(self) -> int:
        """The pixel offset into the current line from top."""
        return round(self.top % 1.0 * self.line_height)

    @lazy_overwrite
    def line_height(self) -> int:
        """The line height in pixels."""
        font_id = self.font_id
        # At 1.77 scale, dpi is halved and pixel_size is doubled. Go figure.
        blf_size(font_id, self.font_size)
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

    @noop
    def draw_contents(self):
        """Called by TextDraw when the surface contents must be redrawn."""

    @noop
    def draw_overlays(self):
        """Called by TextDraw when overlays must be drawn."""

    @noop
    def on_cache_key_changed(self):
        """Called by TextDraw when the cache key changed.
        The cache key is an object that must be equal to a previous key.
        If the key is different, the surface (cache) is redrawn.
        """

    @set_name("TextDraw.draw")
    def draw(self) -> None:
        # Regenerate line height
        regen_attrs = self.map_delattrs()
        while True:
            try:
                consume(regen_attrs)
                break
            except:
                pass
        rect = self.rect
        width, height = rect.size

        # Draw the Widget background.
        rect.draw(*self.position, width, height)

        border = rect.border_width * 2.0
        surface_size = width - border, height - border
        if surface_size != self.surface.size:
            self.surface.__init__(surface_size)

        cache_key = self.get_cache_key()
        if cache_key != self.cache_key:
            self.cache_key = cache_key
            self.on_cache_key_changed()

            with self.surface.bind():
                self.draw_contents()

        self.draw_overlays()

        self.surface.x = int(rect.x + rect.border_width)
        self.surface.y = int(rect.y + rect.border_width)
        self.surface.draw()

        if self.show_scrollbar:
            self.scrollbar.draw()

        if self.show_horizontal_scrollbar:
            self.scrollbar_h.draw()

        self.resizer.draw()

    size = _forwarder("rect.size")

    def get_drawable_lines(self):
        start = int(self.top)
        end = int(start + (self.rect[3] // self.line_height) + 2)
        return islice(self.lines, start, end)

    def get_text_y(self) -> int:
        line_height = self.line_height
        blf_size(self.font_id, self.font_size)
        # The glyph rests on the baseline. Add the (average) descender.
        y_pad = (line_height - blf.dimensions(self.font_id, "x")[1]) // 2

        return int(self.height_inner - line_height + self.line_offset_px + y_pad)

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
        self.cache_key = object()
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
        return self.input is get_widget_focus()

    def poll_redo(self):
        return self.input is get_widget_focus()


def input_undo(input_method):

    @close_cells(input_method)
    @set_name(input_method.__name__)
    def wrapper(self: "Input", *args, **kw):
        self.state.update_cursor()

        # Result can be None, True or False. None/True will push.
        result = input_method(self, *args, **kw)

        if result is not False:
            self.state.push(tag=input_method.__name__)

        # True because input methods block all other hooks.
        return True
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
    state:      UndoStack

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
        self.state = UndoStack(InputAdapter(self))

    def remove_hooks(self):
        if self._hooks_set:
            ED_OT_undo.remove_poll(self.state.poll_undo)
            ED_OT_undo.remove_pre(self.state.pop_undo)

            ED_OT_redo.remove_poll(self.state.poll_redo)
            ED_OT_redo.remove_pre(self.state.pop_redo)

            for override, input_method in input_override_map.items():
                override.remove_pre(getattr(self, input_method.__name__))
            self._hooks_set = False

    def set_hooks(self):
        if not self._hooks_set:
            ED_OT_undo.add_poll(self.state.poll_undo)
            ED_OT_undo.add_pre(self.state.pop_undo)

            ED_OT_redo.add_poll(self.state.poll_redo)
            ED_OT_redo.add_pre(self.state.pop_redo)

            for override, input_method in input_override_map.items():
                override.add_pre(getattr(self, input_method.__name__))
            self._hooks_set = True

    def set_hint(self, string: str):
        self.hint = string

    def set_string(self, string: str, select: bool = False, reset: bool = False):
        self.string = string
        if reset:
            self.state.reset()
        if select:
            self.set_cursor(0, len(string))

    def hit_test_column(self, x: int):
        blf_size(self.font_id, self.font_size)
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

        blf_size(self.font_id, self.font_size)

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
        blf_size(self.font_id, self.font_size)
        x = blf.dimensions(self.font_id, self.string[:start])[0]
        span = blf.dimensions(self.font_id, self.string[start:end])[0]
        return x, span

    # The local x-coordinate of the cursor focus.
    @property
    def focus_x(self):
        blf_size(self.font_id, self.font_size)
        return blf.dimensions(self.font_id, self.string[:self.focus])[0]

    # The sorted selection range.
    @property
    def range(self):
        return sorted((self.anchor, self.focus))

    @property
    def is_focused(self) -> bool:
        return self is get_widget_focus()

    def on_activate(self):
        set_widget_focus(self)
        if _context.window.event.type_string == 'LEFTMOUSE':
            bpy.ops.textension.ui_input_set_cursor('INVOKE_DEFAULT')

    def on_focus(self):
        self.update_uniforms(border_color=(0.5, 0.5, 0.5, 1.0))
        self.set_hooks()
        safe_redraw()

    def on_defocus(self):
        self.update_uniforms(border_color=(0.3, 0.3, 0.3, 1.0))
        self.remove_hooks()
        # When de-focused, selection is removed.
        if self.selected_text != self.string:
            self.set_cursor(len(self.string))
        else:
            safe_redraw()

    def set_cursor(self, start: int, end: int = None):
        if end is None:
            end = start

        self.set_cursor_anchor(start)
        self.set_cursor_focus(end)
        safe_redraw()

    def set_cursor_focus(self, focus):
        self.focus = max(0, min(len(self.string), focus))

    def set_cursor_anchor(self, anchor):
        self.anchor = max(0, min(len(self.string), anchor))

    @input_undo
    def insert(self, s: str):
        start, end = self.range
        tmp = list(self.string)
        tmp[start:end] = s
        self.string = "".join(tmp)
        self.set_cursor(start + len(s))

    @input_undo
    def delete(self, mode: str):
        start, end = self.range
        if not self._delete(mode):
            return False
        self.set_cursor(start)

    def _delete(self, mode: str):
        if not any(self.range):
            return False

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
        return True


    def move(self, mode: str, select=False):
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
            self.set_cursor_focus(self.focus + rel_index)
            safe_redraw()
        else:
            self.set_cursor(abs_index)

    def select_all(self):
        self.set_cursor(0, len(self.string))

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
        self.insert(string, undo=False)

    def get_cursor(self):
        return self.anchor, self.focus

    @input_undo
    def cut(self):
        self.copy()
        if self.selected_text and self._delete('PREVIOUS_CHARACTER'):
            safe_redraw()

    @input_undo
    def line_break(self):
        # Does nothing for now. Just eat the line break event.
        pass


input_override_map = {
    TEXT_OT_copy:        Input.copy,
    TEXT_OT_cut:         Input.cut,
    TEXT_OT_delete:      Input.delete,
    TEXT_OT_insert:      Input.insert,
    TEXT_OT_move:        Input.move,
    TEXT_OT_move_select: Input.move,
    TEXT_OT_paste:       Input.paste,
    TEXT_OT_select_all:  Input.select_all,
    TEXT_OT_line_break:  Input.line_break
}



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

    width  = 200
    height = 120

    font_id = 1

    # Horizontal scrollbar doesn't make sense on ListBoxes.
    show_horizontal_scrollbar = False

    @property
    def active_entry(self):
        index = self.active.index
        if index is not -1:
            return self.lines[index]
        return None

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
        return self.text_padding * runtime.wu_norm

    def on_leave(self) -> None:
        self.hover.set_index(-1)

    def on_activate(self) -> None:
        print("ListBox clicked")

    def get_cache_key(self):
        if id(self.lines) != self.view_key:
            self.view_key = id(self.lines)
            self.top = 0.0
            self.active.set_index(0, redraw_on_modify=False)
        text_x = self.start_x
        line_height = self.line_height
        return (self.line_offset_px, self.view_key, line_height, self.rect.size, text_x)

    def draw_contents(self):
        line_height = self.line_height
        text_x = self.start_x
        text_y = self.get_text_y()
        for entry in self.get_drawable_lines():
            self.draw_entry(entry, text_x, text_y)
            text_y -= line_height

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


class Margins(tuple):
    left   = _named_index(0)
    top    = _named_index(1)
    right  = _named_index(2)
    bottom = _named_index(3)

    @soft_property
    def vertical(self, margin: "Margins", unused=None):
        """The total vertical margin (top + bottom)."""
        margin.vertical = margin.top + margin.bottom
        return margin.vertical

    @soft_property
    def horizontal(self, margin: "Margins", unused=None):
        """The total horizontal margin (left + right)."""
        margin.horizontal = margin[0] + margin[2]
        return margin.horizontal


class TextView(TextDraw):
    parent: Widget

    background_color = 0.2, 0.2, 0.2, 1.0
    border_color     = 0.3, 0.3, 0.3, 1.0

    lines: list[TextLine]

    margins: Margins = Margins((8, 8, 8, 8))  # Left, right, top, bottom.

    # Copy of the string as it was passed to TextView.set_from_string.
    cached_string: str = ""

    font_id = 0

    use_word_wrap: bool = True

    def get_cache_key(self):
        return (self.cached_string, self.font_size, _system.ui_scale)

    def add_font_delta(self, delta: int):
        self.font_size += delta

    def set_margins(self, **kw) -> None:
        """Set the margins of the TextView object.
        Possible keywords: left, top, right and bottom.
        Example: set_margins(left=10)
        """
        edges = ("left", "top", "right", "bottom")
        new_margins = dict(zip(edges, self.margins)) | kw
        try:
            self.margins = Margins(map(int, itemgetter(*edges)(new_margins)))
        except:
            raise TypeError("Keyword arguments not convertible to int")

    def set_from_string(self, string: str):
        self.cached_string = string
        self.cache_key = ()
        self.reset_view()

    def __init__(self, parent: Widget):
        super().__init__(parent=parent)
        self.rect.size = (250, 100)

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
        start = int(max(0, self.top - self.margin_line_offset))
        end   = int(start + (self.rect[3] // self.line_height) + 2)
        return islice(self.lines, start, end)

    def on_cache_key_changed(self):
        self._update_lines()

    def draw_contents(self):
        line_height = self.line_height
        text_y = self.get_text_y()
        text_x = self.get_text_x()
        blf.color(self.font_id, *self.foreground_color)
        for line in self.get_drawable_lines():
            blf.position(self.font_id, text_x, text_y, 0)
            blf.draw(self.font_id, line.string)
            text_y -= line_height

    def resize(self, size: tuple[int, int]):
        self.rect.size = size
        self._update_lines()

    def _update_lines(self) -> list[str]:
        """Must be called when the view is resized or the text is changed."""
        if self.use_word_wrap:
            max_width = self.width - self.scrollbar_width - self.margins.horizontal
            lines = wrap_string(self.cached_string, max_width, self.font_size, self.font_id)
        else:
            lines = self.cached_string.splitlines()
        self.lines[:] = map(TextLine, lines)
        self._clamp_view()
        self.cache_key = object()


class Popup(TextView):
    shadow           = 0.0,  0.0,  0.0,  0.3
    foreground_color = 0.8,  0.8,  0.8,  1.0
    background_color = 0.2,  0.2,  0.2,  1.0
    border_color     = 0.35, 0.35, 0.35, 1.0
    border_width     = 1

    font_size      = 12
    use_word_wrap  = False
    show_scrollbar = False
    show_horizontal_scrollbar = False

    @noop
    def hit_test(self, x: float, y: float) -> bool:
        pass

    def __init__(self, parent: Widget = None):
        super().__init__(parent)
        self.update_uniforms(rect=(100, 100, 200, 100))
        self.set_from_string("This is a popup")
        self.fit()

    def fit(self):
        # Margins are based on being 6px at font size 16 and ui scale 1.0.
        margin = int(6 * (self.font_size / 16) * runtime.wu_norm)
        self.margins = Margins((margin,) * 4)

        blf_size(self.font_id, self.font_size)
        width, height = blf.dimensions(self.font_id, self.cached_string)
        width += self.margins.horizontal
        height += self.margins.vertical
        self.rect.size = map(round, (width, height))

    def get_text_y(self):
        y = (self.rect.height - (self.rect.border_width * 2))
        base = blf.dimensions(self.font_id, "x")[1]
        return int((y - base) * 0.5) - 1

    def get_text_x(self):
        blf_size(self.font_id, self.font_size)
        width = blf.dimensions(self.font_id, self.cached_string)[0]
        return ((self.rect.width - width) // 2) - 1

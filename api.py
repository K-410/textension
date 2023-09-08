# This module implements api extensions.


from ctypes import Structure, Array, c_int, c_short, c_bool, c_char, \
    c_char_p, c_float, c_void_p, sizeof, addressof

from operator import indexOf, attrgetter
from typing import Iterable
from .btypes import *
import bpy
import os


c_int2 = c_int * 2
as_p = bpy.types.bpy_struct.as_pointer


def init():
    for cls in APIBase.__subclasses__():
        cls._register_api()

    # bpy.types.Text.id = bpy.props.IntProperty(get=_ensure_text_id, set=set_id)
    bpy.types.Text.id = property(_ensure_text_id)
    bpy.types.Text.id_proxy = bpy.props.IntProperty()

def cleanup():
    CacheBase.clear_caches()
    for cls in APIBase.__subclasses__():
        cls._remove_api()

    del bpy.types.Text.id
    del bpy.types.Text.id_proxy


def factory(func):
    return func()


_safe_write_store = {}


# Writing to id_proxy isn't allowed in some contexts. In this case we need to
# store the value and write to it later.
def safe_write_id_proxy(self, value):
    try:
        self.id_proxy = value

    except AttributeError:
        _safe_write_store[self] = value

        from .utils import defer

        def deferred_write(self):
            value = _safe_write_store[self]
            if safe_write_id_proxy(self, value):
                del _safe_write_store[self]
                return None
            return 0.0

        defer(deferred_write, self, persistent=False)
        return False
    return True


@factory
def _ensure_text_id():
    from operator import attrgetter

    get_id_proxy = attrgetter("id_proxy")
    bit_length = int.bit_length

    def _ensure_text_id(self: bpy.types.Text):
        id = self.id_proxy
        try:
            assert bit_length(id) == 31
        except (TypeError, AssertionError):  # Not an int or an ideal value.

            if self in _safe_write_store:
                id = _safe_write_store[self]
            else:
                id = 0
                used_ids = set(map(get_id_proxy, bpy.data.texts))
                while bit_length(id) != 31 or id in used_ids:
                    id = int.from_bytes(os.urandom(4), byteorder="little") & 0x7fffffff

                assert id != 0
                safe_write_id_proxy(self, id)

        return id

    return _ensure_text_id


def _tmp(typ):
    defaults = {c_int: 1, c_short: 1, c_bool: True,
                c_char: b"", c_float: 1.0, c_void_p: None}.get

    fields = {member: defaults(c_type) for member, c_type in typ._fields_}
    fields["__bool__"] = False.__bool__
    return type(typ.__name__ + "Temp", (), fields)()


class CacheBase(dict):
    _caches = []
    _func = None

    @classmethod
    def clear_caches(cls):
        for cache in filter(None, cls._caches):
            cache.clear()
        cls._caches.clear()

    def __new__(cls):
        self = super().__new__(cls)
        cls._caches += self,
        return self

    def __missing__(self, key):
        """When a key doesn't exist, run the function this cache decorates."""
        assert self._func is not None
        data = self._func(key)

        # NOTE: Cases where data may be NULL (data isn't ready yet) must be
        # handled by the function itself.

        # The function may return a temporary struct (_tmp) with placeholder
        # values and a __bool__ method returning False. This prevents it from
        # being cached, and still be vaguely useful until it *can* be cached.
        if data:
            self[key] = data
        return data

class CachedStruct(CacheBase):
    """A (read-only) cached property for C struct instances.

    Usage:
    @CachedStruct
    def value(self):
        return MyStruct.from_address(self.as_pointer())
    """
    def __new__(cls, func):
        self = super().__new__(cls)
        self._func = func
        return property(self.__getitem__)


class CachedCData(CacheBase):
    """A cached property for simple C data (c_int, c_short, etc.).

    Usage:
    @CachedCData
    def value(self):
        return c_int.from_address(self.as_pointer() + value_offset)

    @CachedCData(write=True)
    def flag(self):
        return c_int.from_address(self.as_pointer() + flag_offset)
    """

    def __new__(cls, write=False):
        self = super().__new__(cls)

        # When used as a decorator, make it possible to pass the function
        # without calling with arguments. This means the "write" paramenter
        # now is the function being called in the __missing__ method.
        if isinstance(write, type(cls.__new__)):
            self._func = write
            self._setter = None

        def wrapper(func):
            self._func = func
            return property(self._getter, self._setter)

        if self._func is not None:
            wrapper = wrapper(self._func)
        return wrapper

    def _getter(self, key):
        return self[key].value

    def _setter(self, key, value):
        self[key].value = value




# XXX: Keep this
# @property
# @lru_cache(maxsize=1)
# def _synman_from_st(self):
#     from .syntax import SynMan
#     if SynMan._instance is not None:
#         return SynMan._instance[self]
#     return SynMan()[self]


# XXX: Keep this
# def _synman_from_st_cached(self, cache=ccache(_synman_from_st, validate_st)):
#     return cache[self]


class APIBase:
    """Base for all API extensions"""

    # StructBase sub-classes must be ready by now.
    initialize()
    __slots__ = ()
    bl_type = None

    @classmethod
    def _register_api(cls):
        for key, attr in cls._iter_extensions():
            setattr(cls.bl_type, key, attr)

    @classmethod
    def _remove_api(cls):
        for key, _ in cls._iter_extensions():
            delattr(cls.bl_type, key)

    @classmethod
    def _iter_extensions(cls):
        functype = type(lambda: None)
        for key, attr in cls.__dict__.items():
            if isinstance(attr, (property, functype)):
                yield key, attr


def fproperty(funcs):
    return property(*funcs())

class TextLineAPI(APIBase):
    """Extends bpy.types.TextLine"""

    bl_type = bpy.types.TextLine

    # The level of indentation, e.g two taps on TAB means two levels.
    @fproperty
    def indent_level():
        def getter(self):
            return self.indent // self.id_data._get_tab_width()
        def setter(self, level):
            indent = " " * self.id_data._get_tab_width() * level
            self.body = indent + self.body.lstrip()
        return getter, setter

    @property
    def format(self, offset=TextLine.format.offset):
        fmt = c_char_p.from_address(as_p(self) + offset).value
        if fmt is None:
            fmt = b"q" * len(self.body)
        return fmt

    # The number of indentation in spaces. If indent level is 2 and 4 spaces
    # equals one indentation, then 'indent' should return 8.
    @property
    def indent(self):
        string = self.body
        if "\t" in string:
            from . import utils
            string = utils.tabs_to_spaces(string, self.id_data._get_tab_width())
        return len(string) - len(string.lstrip(" "))


def _clamp_line_column(text, line, column) -> tuple[int, int]:
    if line < 0:
        line = 0
    if column < 0:
        column = 0

    try:
        assert column <= len(text.lines[line].body)
    except (AssertionError, IndexError):
        line = len(text.lines) if line > 0 else 0
        column = len(text.lines[line].body)

    return line, column


class TextCursor:
    __slots__ = ("_id", "_index", "_state")

    def copy(self):
        return TextCursor(self.text)

    def __init__(self, text: bpy.types.Text):
        self._id = text.id
        self._index = indexOf(bpy.data.texts, text)
        # Take a snapshot of current selection. This makes it possible to
        # assign a previously stored cursor to Text.cursor.
        self._state = self.get()

    @property
    def text(self) -> bpy.types.Text:
        # Weakly reference the cursor's associated text by ``_id``.
        # ``_index`` is used for faster access.
        try:
            text = bpy.data.texts[self._index]
            assert text.id == self._id
            return text

        except IndexError:
            for t in bpy.data.texts:
                if t.id == self._id:
                    self._index = indexOf(bpy.data.texts, t)
                    return t
            else:
                raise Exception(f"Text reference to TextCursor was removed")

    # The part of cursor that stays put during a drag selection.
    @property
    def anchor(self) -> tuple[int, int]:
        return self.text.current_line_index, self.text.current_character
    @anchor.setter
    def anchor(self, new_anchor):
        text = self.text
        text.current_line_index, text.current_character = _clamp_line_column(text, *new_anchor)

    # The part of cursor that moves during a drag selection.
    @property
    def focus(self) -> tuple[int, int]:
        text = self.text
        return text.select_end_line_index, text.select_end_character
    @focus.setter
    def focus(self, new_focus):
        text = self.text
        text.select_end_line_index, text.select_end_character = _clamp_line_column(text, *new_focus)

    @property
    def focus_line(self) -> int:
        return self.focus[0]

    @property
    def focus_column(self) -> int:
        return self.focus[1]

    @property
    def start_column(self) -> int:
        return self.start[1]

    @property
    def end_column(self) -> int:
        return self.end[1]

    @property
    def start_line(self) -> int:
        return self.start[0]

    @property
    def end_line(self) -> int:
        return self.end[0]

    def get(self) -> tuple[int, int, int, int]:
        return (*self.anchor, *self.focus)

    def set(self, line: int, col: int = 0, line2: int = None, col2: int = None):
        text = self.text
        line, col = _clamp_line_column(text, line, col)
        if line2 is None:
            text.cursor_set(line, character=col)
        else:
            line2, col2 = _clamp_line_column(text, line2, col2)
            text.select_set(line, col, line2, col2)

    def set_focus(self, line: int, col: int = 0):
        self.focus = line, col

    # The part closest to the top left corner.
    @property
    def start(self) -> tuple[int, int]:
        return min(self.anchor, self.focus)
    # The part closest to the bottom right corner.
    @property
    def end(self) -> tuple[int, int]:
        return max(self.anchor, self.focus)

    @property
    def sorted(self) -> tuple[int, int, int, int]:
        return (*self.start, *self.end)

    @property
    def is_flipped(self) -> bool:
        return self.sorted != self.get()

    # Allow unpacking by decomposing the cursor into indices.
    def __iter__(self) -> Iterable[int]:
        return iter(self.get())


class TextAPI(APIBase):
    """Extends bpy.types.Text"""

    bl_type = bpy.types.Text

    def get_leading_body(self):
        """Returns the body leading up to the start of the cursor."""
        return self.cursor_start_line.body[:self.cursor_start[1]]

    @CachedStruct
    def internal(self):
        """Exposes the C instance"""
        return Text.from_address(self.as_pointer())

    def string_from_indices(self, line_start, col_start, line_end, col_end):
        if line_start == line_end:
            return self.lines[line_start].body[col_start:col_end]

        sel = [l.body for l in self.lines[line_start:line_end + 1]]
        sel[0] = sel[0][col_start:]
        sel[-1] = sel[-1][:col_end]
        return "\n".join(sel)

    # The current selection of text. Read-only.
    @property
    def selected_text(self):
        return self.string_from_indices(*self.cursor_sorted)

    # Given a set of cursor indices, return the string format.
    def format_from_indices(self, *cursor):
        curl, curc, sell, selc = cursor
        
        fmt = [l.format for l in self.lines[curl:sell + 1]]
        if curl == sell:
            return b"".join(fmt)[curc:selc]
        if fmt:
            fmt[0] = fmt[0][curc:]
            fmt[-1] = fmt[-1][:selc]
        return b"\n".join(fmt)

    # The current selection of text in syntax format. Bytes. Read-only.
    @property
    def selected_format(self):
        return self.format_from_indices(*self.cursor_sorted)

    # The top-most line and accompanying column indices.
    @property
    def cursor_start(self):
        return self.cursor_sorted[:2]

    # The bottom-most line and accompanying column indices.
    @property
    def cursor_end(self):
        return self.cursor_sorted[2:]

    # The top-most TextLine
    @property
    def cursor_start_line(self):
        if self.current_line_index <= self.select_end_line_index:
            return self.current_line
        return self.select_end_line

    @property
    def cursor_start_line_index(self):
        return min(self.current_line_index, self.select_end_line_index)

    # The bottom-most TextLine
    @property
    def cursor_end_line(self):
        if self.select_end_line_index <= self.current_line_index:
            return self.select_end_line
        return self.current_line

    # The bottom-most line
    @property
    def cursor_end_line_index(self):
        return max(self.current_line_index, self.select_end_line_index)

    @property
    def cursor(self):
        return TextCursor(self)

    cursor2 = property(attrgetter("current_line_index", "current_character", "select_end_line_index", "select_end_character"))

    @property(None).setter
    def cursor_focus(self, new_focus):
        self.select_end_line_index, self.select_end_character = _clamp_line_column(self, *new_focus)
    cursor_focus = cursor_focus.getter(attrgetter("select_end_line_index", "select_end_character"))

    @property(None).setter
    def cursor_anchor(self, new_anchor):
        self.current_line_index, self.current_character = _clamp_line_column(self, *new_anchor)
    cursor_anchor = cursor_anchor.getter(attrgetter("current_line_index", "current_character"))

    cursor_columns = property(attrgetter("current_character", "select_end_character"))

    @cursor.setter
    def cursor(self, cursor: tuple[int, int] | TextCursor):
        try:
            if isinstance(cursor, TextCursor):
                cursor = cursor._state
            elif len(cursor) == 2:
                cursor += cursor
            self.select_set(*cursor)
        except:
            raise Exception(f"Expected a sequence of 2 or 4 ints, got {cursor}")

    @property
    def cursor_sorted(self):
        anchor = self.cursor_anchor
        focus = self.cursor_focus
        return (*anchor, *focus) if anchor < focus else (*focus, *anchor)

    @fproperty
    def curl():
        def getter(self):
            return self.current_line_index
        def setter(self, line):
            self.current_line_index = line
        return getter, setter

    @fproperty
    def curc():
        def getter(self):
            return self.current_character
        def setter(self, col):
            self.current_character = max(0, min(col, len(self.current_line.body)))
        return getter, setter

    @fproperty
    def sell():
        def getter(self):
            return self.select_end_line_index
        def setter(self, line):
            self.select_end_line_index = line
        return getter, setter

    @fproperty
    def selc():
        def getter(self):
            return self.select_end_character
        def setter(self, col):
            self.select_end_character = max(0, min(col, len(self.select_end_line.body)))
        return getter, setter

    def _get_tab_width(self):
        """Private use. Only works when the text is open in an editor.
        
        Normally this should be called on the space data itself, but it's
        convenient having a naive way of getting indentation per line.
        """
        for w in bpy.context.window_manager.windows:
            for a in w.screen.areas:
                if a.type == 'TEXT_EDITOR':
                    if a.spaces.active.text == self:
                        return a.spaces.active.tab_width
        return 4

    @property
    def indent_string(self):
        if self.indentation == 'SPACES':
            return " " * self._get_tab_width()
        return "\t"

    # Returns whether the cursor selection is reversed.
    @property
    def cursor_flipped(self):
        curl = self.current_line_index
        sell = self.select_end_line_index

        # XXX Something wrong with 'current_character'
        if curl == sell:
            t = Text(self)
            return t.curc > t.selc
        return curl > sell

    # Make it possible to access lines on text using subscription.
    # def __getitem__(self, index):
    #     if isinstance(index, (slice, int)):
    #         return self.lines[index]
    #     elif isinstance(index, str):
    #         # return super().__getitem__(index)
    #         return self.__getattribute__(index)

    # Make it possible to loop over text lines directly.
    def __iter__(self):
        return iter(self.lines)

    # Make it possible to do len(text) to get number of lines.
    # def __len__(self, len=len):
    #     return len(self.lines)

class View2DAPI(APIBase):
    """Extends bpy.types.View2D"""

    bl_type = bpy.types.View2D

    @CachedStruct
    def internal(self):
        """Exposes the DNA struct"""
        return View2D.from_address(self.as_pointer())

    @property
    def width(self):
        return self.internal.tot.xmax


class WindowAPI(APIBase):
    """Extends bpy.types.Window"""

    bl_type = bpy.types.Window

    @CachedStruct
    def event(self):
        return self.internal.eventstate.contents

    @CachedStruct
    def mouse(self):
        return c_int2.from_address(_dynaddr(self.event, wmEvent.posx))

    @CachedStruct
    def internal(self):
        return wmWindow(self)


class PreferencesSystemAPI(APIBase):
    """Extends bpy.types.PreferencesSystem"""

    bl_type = bpy.types.PreferencesSystem

    @CachedCData
    def dpi_fac(self):
        return c_float.from_address(_dynaddr(UserDef(self), UserDef.dpi_fac))

    # Exposes widget units (preferences.system.wu), a commonly used unit
    # throughout Blender's code base
    @CachedCData
    def wu(self):
        return c_short.from_address(_dynaddr(UserDef(self), UserDef.widget_unit))


def _dynaddr(*elems):
    """Dynamically sum the offset for instances and fields. Use with cache."""
    offset = 0
    field_type = type(rctf.xmin)
    for e in elems:
        if isinstance(e, Structure):
            offset += addressof(e)
        elif isinstance(e, field_type):
            offset += e.offset
        elif isinstance(e, (type(Array), type(c_int))):
            offset += sizeof(e)
        else:
            raise Exception(f"Cannot sum address of C type {e, type(e)}")
    return offset


# class CachedProperty:
#     def __new__(cls, func):
#         (self := super().__new__(cls)).func = func
#         return self

#     def __set_name__(self, cls, attr):
#         class Cache(dict):
#             def __missing__(self, key, func=self.func):
#                 return self.setdefault(key, func(key))
#         import types
#         setattr(cls, attr, property(Cache().__getitem__))

class SpaceTextEditorAPI(APIBase):
    """Extends bpy.types.SpaceTextEditor"""

    bl_type = bpy.types.SpaceTextEditor

    @CachedStruct
    def internal(self):
        """Exposes the DNA struct"""
        return SpaceText(self)

    # @CachedStruct  NOTE: Becomes invalid on blend file load.
    @property
    def drawcache(self, temp=_tmp(DrawCache)):
        ret = self.runtime.drawcache
        if not ret:
            # Might be null. Return a dummy which won't be cached.
            return temp
        return ret.contents

    @CachedStruct
    def runtime(self):
        return SpaceText(self).runtime

    @CachedStruct
    def offsets(self):
        return self.runtime.scroll_ofs_px

    @CachedStruct
    def scroll_select_y(self):
        return c_int2.from_address(
            _dynaddr(self.runtime, c_int2, SpaceText_Runtime.scroll_region_select))

    @CachedCData(write=True)
    def flags(self):
        return c_short.from_address(_dynaddr(self.internal, SpaceText.flags))

    @CachedCData(write=True)
    def left(self):
        return c_int.from_address(_dynaddr(self.internal, SpaceText.left))

    @CachedCData()
    def cwidth(self):
        return c_int.from_address(_dynaddr(self.runtime, SpaceText_Runtime.cwidth_px))

    # The line height in pixels.
    @property
    def line_height(self):
        return int(self.runtime._lheight_px * 1.3)


class RegionAPI(APIBase):
    """Extends bpy.types.Region"""

    bl_type = bpy.types.Region

    @CachedStruct
    def internal(self):
        """Exposes the DNA struct"""
        return ARegion.from_address(self.as_pointer())
    
    # internal2 = property(_internal.__getitem__)
    # Offset in pixels when a region is scrolled/panned (eg. headers, panels)
    @CachedCData(write=True)
    def offsetx(self):
        return c_float.from_address(_dynaddr(self.view2d.internal, View2D.cur))

    @CachedCData(write=True)
    def offsety(self):
        return c_float.from_address(_dynaddr(self.view2d.internal, c_float, View2D.cur))

    @CachedStruct
    def window(self):
        from .utils import window_from_region
        return window_from_region(self)

    @CachedStruct
    def mouse(self):
        return self.window.mouse

    @property
    def mouse_x(self: bpy.types.Region) -> int:
        return self.mouse[0] - self.x

    # Expose 'mouse_region_y'
    @property
    def mouse_y(self: bpy.types.Region) -> int:
        return self.mouse[1] - self.y


class AreaAPI(APIBase):
    """Extends bpy.types.Area"""

    bl_type = bpy.types.Area

    @CachedStruct
    def internal(self):
        """Exposes the DNA struct"""
        return ScrArea.from_address(self.as_pointer())
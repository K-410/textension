import bpy
import _bpy
from time import perf_counter, monotonic
from collections import deque
from . import highlights
from . import utils


bl_info = {
    "name": "Textension",
    "description": "Convenience operators for text editor",
    "author": "kaio",
    "version": (1, 0, 0),
    "blender": (2, 82, 0),
    "location": "Text Editor",
    "category": "Text Editor"
}


if locals().get('loaded'):
    loaded = False
    from importlib import reload
    from sys import modules

    modules[__name__] = reload(modules[__name__])
    for name, module in modules.items():
        if name.startswith(f"{__package__}."):
            globals()[name] = reload(module)
    del reload, modules

prefs = None
_call = _bpy.ops.call
setdefault = utils.setdefault
iadd_default = utils.iadd_default
STRuntime = utils.STRuntime
wunits_get = utils.wunits_get


def cursor_history_add(context, *args, **kwargs):
    textman(context.edit_text).cursor.add(*args, **kwargs)


def cursor_history_step(context, direction):
    tm = textman(context.edit_text)
    if direction == 'FORWARD':
        return tm.cursor.step_forward()
    return tm.cursor.step_back()


# Return True if cursor is within x1 <-> x2.
def cursor_isect_x(event, x1, x2):
    return x1 < event.mouse_region_x <= x2


def cursor_isect_xy(event, x1, x2, y1, y2):
    mrx = event.mouse_region_x
    mry = event.mouse_region_y
    return x1 < mrx <= x2 and y1 < mry < y2


# Scroll activates 1 pixel too late (uses >, but needs >=).
def in_scroll(event, region):
    rw = region.width
    return cursor_isect_xy(
        event, rw - (wunits_get() * 0.6), rw, 0, region.height)


# Return a tuple of cursor indices.
def cursor_get(text, sort=False) -> tuple:
    curl = text.current_line_index
    curc = text.current_character
    sell = text.select_end_line_index
    selc = text.select_end_character

    if sort:
        if (curl == sell and curc > selc) or curl > sell:
            curc, selc = selc, curc
        if curl > sell:
            curl, sell = sell, curl
    return curl, curc, sell, selc


def selection_as_string(text) -> str:
    curl, curc, sell, selc = cursor_get(text, sort=True)
    sel = [l.body for l in text.lines[curl:sell + 1]]
    if curl == sell:
        return "".join(sel)[curc:selc]
    sel[0] = sel[0][curc:]
    sel[-1] = sel[-1][:selc]
    return "\n".join(sel)


def copy_string(context, cut=False):
    tc = TextContext(context)
    curl = tc.curl_sorted
    curc = tc.curc
    buffer = Buffer.get()
    buffer.state_set(not tc.has_sel)

    end = tc.lenl == curl + 1

    # Select entire line (line + first index of next line).
    if not tc.has_sel:
        tc.cursor = (curl - end,
                     tc.lenl - 1 and -end,
                     curl + 1,
                     -end)

    buffer.string_set(tc.sel_string_ex)
    if not tc.has_sel and not cut:
        tc.cursor = curl, curc
    # TODO use bpy.ops.text.copy.
    context.window_manager.clipboard = tc.sel_string


def set_text_context_menu(state):
    funcs = bpy.types.TEXT_MT_context_menu._dyn_ui_initialize()
    if state:
        return funcs.insert(0, lambda s, c: s.layout.operator("text.new"))

    next((funcs.__delitem__(funcs.index(f))
         for f in funcs if f.__module__ == __name__), 0)


# Get the offset (px) from line number margin.
def lnum_margin_width_get(st) -> int:
    pad = left_pad_get(st)
    return pad - cwidth_get_ex(pad, st)


# Get the x coordinate where text body starts.
def left_pad_get(st) -> int:
    return st.region_location_from_cursor(0, 0)[0]


# Get total line number digits. Padding (2) not included.
def lnum_digits_get(text) -> int:
    return len(repr(len(text.lines)))


# Slightly faster version, but needs left pad. Assumes margin visible.
def cwidth_get_ex(pad, st):
    return int(pad / (lnum_digits_get(st.text) + 3))


# Get character width (px) of current text editor.
def cwidth_get(st) -> int:
    if st.show_word_wrap:
        return int(left_pad_get(st) / (lnum_digits_get(st.text) + 3))

    loc = st.region_location_from_cursor
    for idx, line in enumerate(st.text.lines):
        if line.body:
            return loc(idx, 1)[0] - loc(idx, 0)[0]
    else:
        from blf import size, dimensions
        size(1, st.font_size, 72)
        return round(dimensions(1, "W")[0] * (utils.wunits_get() * 0.05))


# Get wrap offset (in lines) between 'start' and 'end'.
def offset_lines_get(st, rw, start=0, end=0) -> int:
    text = st.text
    lines = text.lines
    pad = cwidth = cwidth_get(st)
    if st.show_line_numbers:
        pad *= lnum_digits_get(text) + 3
    c_max = max((rw - utils.wunits_get() - pad) // cwidth, 8)

    offset = 0
    for idx, line in enumerate(lines[start:end or len(lines)], start):
        body = line.body
        if len(body) < c_max:
            continue
        c_start = 0
        c_end = c_max
        for c_pos, c in enumerate(body):
            if c_pos - c_start >= c_max:
                offset += 1
                c_start = c_end
                c_end += c_max
            elif c in " -":
                c_end = c_pos + 1
    return offset


# Get maximum allowed to scroll (highest st.top). Same as Blender vanilla op.
def scroll_max_get(st, rw) -> int:
    offl = st.show_word_wrap and offset_lines_get(st, rw)
    view_half = st.visible_lines // 2
    return len(st.text.lines) - view_half + offl


# A text manager to store runtime data. For now just storing cursor history.
# per text block.
class TextMan:
    class CursorHistory(dict):
        def __init__(self, text):
            self.text_name = text.name
            self.head = 0
            self.history = deque(maxlen=25)
            self.history.appendleft(cursor_get(self.text)[-2:])

        @property
        def text(self):
            text = bpy.data.texts.get(self.text_name)
            if not text:  # Should never happen.
                raise ValueError
            return text

        def step_back(self) -> bool:
            if self.head < len(self.history) - 1:
                self.head += 1
                self.scroll()
                return True

        def step_forward(self) -> bool:
            if self.head > 0:
                self.head -= 1
                self.scroll()
                return True

        def scroll(self) -> set:
            line, char = self.history[self.head]
            return bpy.ops.textension.scroll(
                type='JUMP', jump=line, char=char, history=False)

        # Append cursor position to history.
        def add(self, line=None, char=None, replace=False) -> bool:

            self.gc()
            history = self.history
            if line is None or char is None:
                line, char = cursor_get(self.text)[-2:]

            # When adding to history, remove anything past current position
            # otherwise history won't be linear.
            if self.head != 0:
                for i in range(self.head):
                    history.popleft()
                self.head = 0

            if not history:
                history[:] = (0, 0)
            prev = history[0][0]

            # Update if within 10 lines of previous history. Force update with
            # replace=True.
            if replace or line in range(max(0, prev - 10), prev + 10):
                history[0] = line, char
                return True

            return bool(history.appendleft((line, char)))

        def gc(self):
            for key in tuple(vars(textman)):
                if not bpy.data.texts.get(key):
                    del vars(textman)[key]

    # Store cursor (history) per text block.
    class TextData:
        def __init__(self, text):
            self.cursor = TextMan.CursorHistory(text)

    def __getitem__(self, text):
        name = text.name
        dic = vars(self)
        if name not in dic:
            dic[name] = self.TextData(text)
        return dic[name]

    def __call__(self, item):
        return self[item]


textman = TextMan()


# Simple selection buffer to remember states. Use for cut/copy text and
# comparing clipboard content.
class Buffer:
    @classmethod
    def get(cls):
        if not hasattr(Buffer, "_buffer"):
            cls._buffer = Buffer()
        return cls._buffer

    def __init__(self):
        self.buffer = ""
        self.no_sel = False

    def string_get(self) -> str:
        return self.buffer

    def string_set(self, value):
        self.buffer = value

    def state_get(self) -> bool:
        return self.no_sel

    def state_set(self, value: bool):
        self.no_sel = value


# A convenience class for getting/setting runtime data. Each context is only
# valid for the lifetime of an operator.
class TextContext:
    def init_cursor(self):
        self._curl, self._curc, self._sell, self._selc = cursor_get(self.text)

    @property
    def curl(self) -> int:
        if self._curl is None:
            self.init_cursor()
        return self._curl

    # Return updated current line index.
    @property
    def curl_ext(self) -> int:
        return self.text.current_line_index

    @curl.setter
    def curl(self, val: int):
        val = max(0, min(val, len(self.lines) - 1))
        self.text.current_line_index = val

    @property
    def curl_sorted(self) -> int:
        return min(self.curl, self.sell)

    @property
    def curc(self) -> int:
        if self._curc is None:
            self.init_cursor()
        return self._curc

    @curc.setter
    def curc(self, val: int):
        val = max(0, min(val, len(self.lines[self.curl].body)))
        self.text.current_character = val

    @property
    def curc_sorted(self) -> int:
        if self.reverse:
            return self.selc
        return self.curc

    @property
    def sell(self) -> int:
        if self._sell is None:
            self.init_cursor()
        return self._sell

    @sell.setter
    def sell(self, val: int):
        val = max(0, min(val, len(self.lines) - 1))
        self.text.select_end_line_index = val

    @property
    def sell_sorted(self) -> int:
        return max(self.curl, self.sell)

    @property
    def selc(self) -> int:
        if self._selc is None:
            self.init_cursor()
        return self._selc

    @selc.setter
    def selc(self, val: int):
        val = max(0, min(val, len(self.endl_body)))
        self.text.select_end_character = val

    @property
    def selc_sorted(self) -> int:
        return
        if self.reverse:
            return self.curc
        return self.selc

    @property
    def cursor(self) -> tuple:
        return self.curl, self.curc, self.sell, self.selc

    # Set both cursor ends (tuple of two), or separately (tuple of four).
    @cursor.setter
    def cursor(self, *args):
        if isinstance(args[0], tuple):
            args, = args
            if len(args) == 2:
                args = (*args, *args)
        self.select_set(*args)

    # Get sorted cursor range.
    @property
    def cursor_sorted(self) -> tuple:
        chr_pos = self.curc, self.selc
        curl, sell = sorted((self.curl, self.sell))
        curc, selc = sorted(chr_pos) if self.reverse else chr_pos
        return curl, curc, sell, selc

    # Return body from lowest line in selection.
    @property
    def endl_body(self) -> str:
        return self.lines[self.sell_sorted].body

    # Return True if text has selection.
    @property
    def has_sel(self) -> bool:
        return bool(self.curl - self.sell or self.curc - self.selc)

    # Refresh TextContext instance.
    def update(self, context):
        self.__init__(context)

    @property
    def sel_string(self):
        if self._sel_string is None:
            self._sel_string = selection_as_string(self.text)
        return self._sel_string

    # Return updated selection as string.
    @property
    def sel_string_ex(self):
        self._sel_string = selection_as_string(self.text)
        return self._sel_string

    # List of TextLine objects from selection.
    @property
    def sel_lines(self) -> list:
        return [l for l in self.lines[self.curl_sorted:self.sell_sorted + 1]]

    # Return indent for string.
    def indent_get(self, string: str) -> int:
        return next((i for i, c in enumerate(string) if c not in " \t"), 0)

    # Try detect indent unit with st.tab_width as fallback.
    def _tab(self) -> int:
        tab_width = self.st.tab_width
        for line in self.lines:
            indent = self.indent_get(line.body)
            if indent:
                return indent % tab_width and tab_width or indent
        return tab_width

    @property
    def lines(self):
        return self.text.lines

    @property
    def lenl(self):
        return len(self.lines)

    @property
    def reverse(self):
        curl = self.curl
        sell = self.sell
        return bool(curl > sell or curl == sell and self.curc > self.selc)

    @property
    def tab(self):
        __tab = getattr(self, "__tab", None)
        if __tab is None:
            self.__tab__ = __tab = self._tab()
        return __tab

    # TODO Lazy load some attributes eg. text selection.
    def __init__(self, context):
        self.st = st = context.space_data
        self.text = text = st.text
        self.select_set = text.select_set
        self.vlines = st.visible_lines
        self._curl = self._curc = \
            self._sell = self._selc = None
        self._sel_string = None

    def __new__(cls, context, *args, **kwargs):
        if not getattr(context, "edit_text"):
            return False
        return super(TextContext, cls).__new__(cls, *args, **kwargs)


# TODO: Use generic draw function instead.
class TEXTENSION_MT_override_preferences(bpy.types.Menu):
    """Make text editor always start with this enabled"""
    bl_label = "Text Editor Preferences"

    def draw(self, context):
        pass

    def draw_syntax_footer(self, context):
        tc = TextContext(context)

        if tc:
            layout = self.layout
            row = layout.row(align=True)
            row.alignment = 'RIGHT'

            row.separator_spacer()
            text = "Ln %s   Col %s" % (tc.sell, tc.selc)
            if tc.sel_string:
                text = "(%s selected)    " % len(tc.sel_string) + text
            row.operator("textension.goto", text=text, emboss=False)

            props = (("show_line_highlight", "COLLAPSEMENU"),
                     ("show_line_numbers", "LINENUMBERS_"),
                     ("show_syntax_highlight", "SYNTAX_"),
                     ("show_word_wrap", "WORDWRAP_"))

            row = layout.row(align=True)
            for prop, icon in props:
                prop_val = getattr(prefs, prop)

                if icon.endswith("_"):
                    icon += "ON" if prop_val else "OFF"
                row.prop(prefs, prop, icon=icon, text="")

                # Force text editor settings from preferences.
                if getattr(tc.st, prop) != prop_val:
                    setattr(tc.st, prop, prop_val)

    @classmethod
    def register(cls):
        bpy.types.TEXT_HT_footer.append(cls.draw_syntax_footer)

    @classmethod
    def unregister(cls):
        bpy.types.TEXT_HT_footer.remove(cls.draw_syntax_footer)


# ----------------------------------------------------------------------------
#    Text Editor Operators
# ----------------------------------------------------------------------------
class TEXTENSION_OT_cut_internal(utils.TextOperator):
    bl_idname = "textension.cut_internal"
    bl_label = "Cut Text"
    bl_options = {'INTERNAL'}

    init: bpy.props.BoolProperty(
        default=False, options={'SKIP_SAVE', 'HIDDEN'})

    @utils.classproperty
    @classmethod
    def has_selection(cls):
        return setdefault(cls, "_has_selection", False)

    @has_selection.setter
    @classmethod
    def has_selection(cls, state):
        cls._has_selection = state

    @utils.classproperty
    @classmethod
    def cursor(cls):
        return setdefault(cls, "_cursor", None)

    @cursor.setter
    @classmethod
    def cursor(cls, state):
        cls._cursor = state

    def execute(self, context):
        tc = TextContext(context)
        if self.init:
            self.has_selection = tc.has_sel
            self.cursor = tc.cursor_sorted
            copy_string(context, cut=True)
        else:
            if self.cursor:
                # Move cursor to the end of previous line.
                if tc.curl_ext == tc.lenl - 1:
                    if not self.has_selection:
                        tc.cursor = tc.curl, len(tc.endl_body)
        return {'FINISHED'}


class TEXTENSION_OT_cut(utils.TextMacro):
    bl_idname = "textension.cut"
    bl_label = "Cut Text"
    bl_options = {'MACRO', 'UNDO'}

    @classmethod
    def register_keymaps(cls):
        name = cls.bl_idname
        utils.kmi_new(cls, "Text", name, 'X', 'PRESS', ctrl=1)
        utils.kmi_new(cls, "Screen Editing", name, 'X', 'PRESS', ctrl=1)

    @classmethod
    def _register(cls):
        cls.define(cls.__name__ + "_internal").properties.init = True
        cls.define("TEXT_OT_cut")
        cls.define(cls.__name__ + "_internal")


class TEXTENSION_OT_copy(utils.TextOperator):
    bl_idname = "textension.copy"
    bl_label = "Copy Text"

    @classmethod
    def register_keymaps(cls):
        name = cls.bl_idname
        utils.kmi_new(cls, "Text", name, 'C', 'PRESS', ctrl=1)
        utils.kmi_new(cls, "Screen Editing", name, 'C', 'PRESS', ctrl=1)

    def execute(self, context):
        copy_string(context)
        return {'FINISHED'}


class TEXTENSION_OT_paste_internal(utils.TextOperator):
    bl_idname = "textension.paste_internal"
    bl_label = "Paste Text"
    bl_options = {'INTERNAL'}

    init: bpy.props.BoolProperty(
        default=False, options={'SKIP_SAVE', 'HIDDEN'})

    @utils.classproperty
    @classmethod
    def cursor(cls):
        return setdefault(cls, "_cursor", None)

    @cursor.setter
    @classmethod
    def cursor(cls, state):
        cls._cursor = state

    def execute(self, context):
        if self.init:
            buffer = Buffer.get()
            string = buffer.string_get()
            wm = context.window_manager
            if buffer.state_get() and string == wm.clipboard:
                tc = TextContext(context)
                self.cursor = tc.cursor
                type = 'LINE_END' if tc.curl == tc.lenl - 1 else 'LINE_BEGIN'
                bpy.ops.text.move(type=type)
            return {'FINISHED'}

        if self.cursor is not None:
            tc = TextContext(context)
            curl, curc, _, _ = self.cursor
            tc.cursor = curl + 1, curc
            self.cursor = None

        return {'FINISHED'}


class TEXTENSION_OT_paste(utils.TextMacro):
    bl_idname = "textension.paste"
    bl_label = "Paste Text"
    bl_options = {'MACRO', 'UNDO'}

    @classmethod
    def register_keymaps(cls):
        name = cls.bl_idname
        utils.kmi_new(cls, "Text", name, 'V', 'PRESS', ctrl=1)
        utils.kmi_new(cls, "Screen Editing", name, 'V', 'PRESS', ctrl=1)

    @classmethod
    def _register(cls):
        cls.define(cls.__name__ + "_internal").properties.init = True
        cls.define("TEXT_OT_paste")
        cls.define(cls.__name__ + "_internal")


class TEXTENSION_OT_insert_internal(utils.TextOperator):
    bl_idname = "textension.insert_internal"
    bl_label = "Insert Internal"
    bl_options = {'INTERNAL'}

    init: bpy.props.BoolProperty(
        default=False, options={'SKIP_SAVE', 'HIDDEN'})

    @classmethod
    def cursor(cls, value=None):
        if value is None:
            return setdefault(cls, "_cursor", (-1,) * 4)
        cls._cursor = value

    @classmethod
    def do_close(cls, state=None):
        if state is None:
            return setdefault(cls, "_do_close", False)
        cls._do_close = state

    @classmethod
    def c_next(cls, char=None):
        if char is None:
            return setdefault(cls, "_c_next", "")
        cls._c_next = char

    def close(self, tc, c_in):
        curl, curc, sell, selc = self.cursor()
        c_in_r = dict(("()", "[]", "{}", '""', "''"))[c_in]
        tc.text.write("%s%s" % (Buffer.get().string_get(), c_in_r))

        # Add one to end if selection is inline.
        tc.select_set(curl, curc + 1, sell, selc + (curl == sell))

    def quote_insert(self, tc, c_in):
        if not self.do_close():
            if self.c_next() == c_in:
                bpy.ops.text.delete(type='PREVIOUS_CHARACTER')
                tc.selc += 1
                tc.curc += 1
        else:
            self.close(tc, c_in)
        return {'FINISHED'}

    def bracket_insert(self, tc, c_in):
        if c_in in ")]}" and self.c_next() == c_in:
            bpy.ops.text.delete(type='NEXT_CHARACTER')

        if c_in in {"(", "[", "{"} and self.do_close():
            self.close(tc, c_in)
        return {'FINISHED'}

    # Test and pass alt so other operators can listen for it.
    def test_alt(self, context, event):
        kc = context.window_manager.keyconfigs.active.keymaps.get("Text")
        if kc:
            return next((0 for k in kc.keymap_items
                        if k.alt and k.type == event.type), 1)

    # Test to see if entered quote one of a pair or escape.
    def escape_quotes(self, text, c_in):
        body = text.select_end_line.body
        quote_strip = "".join(c for c in body if c not in "\\\"\\\'")
        ci_next = text.select_end_character + 1
        quote_doub = quote_strip.count("\"", 0, ci_next) % 2 and "\""
        quote_sing = quote_strip.count("\'", 0, ci_next) % 2 and "\'"
        return c_in not in {quote_doub, quote_sing}

    def invoke(self, context, event):
        unicode = event.unicode
        # TEXT_INPUT may trigger on non-printable keys on some keyboards.
        # Escape these by passing them through the operator.
        if event.type in {'DEL', 'BACK_SPACE'} or not unicode or \
           event.alt and not self.test_alt(context, event):
            return {'PASS_THROUGH'}

        text = context.edit_text
        buffer = Buffer.get()

        # If next char is any of these, allow closing bracket.
        # And check against quotes if the typed char isn't one.
        allow_close = {False, *" )]}.,:;>"}
        if unicode not in "\"\'":
            allow_close.update("\"\'")

        # 2nd pass.
        if not self.init:
            tc = TextContext(context)
            if unicode in "{}()[]":
                return self.bracket_insert(tc, unicode)
            elif unicode in "\"\'":
                return self.quote_insert(tc, unicode)
            return {'FINISHED'}

        # Init (1st) pass.
        tc = TextContext(context)
        self.cursor(tc.cursor_sorted)

        # Check for escaping or uneven quotes.
        skip = self.escape_quotes(text, unicode)
        if unicode in "\"\'":
            # Don't surround commented lines unless there's a selection.
            comment = text.select_end_line.body.lstrip().startswith("#")
            skip &= not comment or tc.has_sel

        # If next character, return it, else False.
        # Use c_next to determine close bracket logic.
        self.c_next(tc.lines[tc.sell].body[tc.selc:][:1] or False)

        # Determine whether to enclose in 2nd pass or not.
        do_close = prefs.closing_bracket
        do_close &= tc.has_sel or (self.c_next() in allow_close and skip)
        self.do_close(do_close)
        # If there's a selection, store it in case of enclosing brackets.
        buffer.string_set(tc.sel_string)
        return {'FINISHED'}


class TEXTENSION_OT_insert(utils.TextMacro):
    bl_idname = "textension.insert"
    bl_label = "Insert"
    bl_options = {'MACRO', 'UNDO'}

    @classmethod
    def register_keymaps(cls):
        utils.kmi_new(cls, "Text", cls.bl_idname, 'TEXTINPUT', 'ANY')
        utils.kmi_mute(cls, "Text", idname="text.insert", type='TEXTINPUT')
        args = cls, "Text"
        utils.kmi_mute(*args, idname="text.delete", type='BACK_SPACE', ctrl=0)
        utils.kmi_mute(*args, idname="text.delete", type='BACK_SPACE', ctrl=1)

    @classmethod
    def _register(cls):
        cls.define(cls.__name__ + "_internal").properties.init = True
        cls.define("TEXT_OT_insert")
        cls.define(cls.__name__ + "_internal")


class TEXTENSION_OT_delete_internal(utils.TextOperator):
    bl_idname = "textension.delete_internal"
    bl_label = "Delete Internal"
    bl_options = {'INTERNAL'}

    init: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})

    def invoke(self, context, event):
        cls = __class__
        if self.init:
            # Delete word.
            if event.ctrl:
                cls.word = True
                return {'FINISHED'}
            tc = TextContext(context)
            body = tc.endl_body
            selc = tc.selc

            # Check if cursor bounds have matching brackets.
            if body[selc:] and not tc.has_sel:
                match = body[selc - 1: selc + 1] or False
                cls.match = match in {"()", "[]", "{}", "\"\"", "\'\'"}
            return {'FINISHED'}

        if getattr(cls, "word", None):
            cls.word = None
            bpy.ops.text.delete(type='PREVIOUS_WORD')
        elif getattr(cls, "match", None):
            cls.match = None
            bpy.ops.text.delete(type='NEXT_CHARACTER')
        return {'FINISHED'}


class TEXTENSION_OT_delete(utils.TextMacro):
    bl_idname = "textension.delete"
    bl_label = "Delete"
    bl_options = {'MACRO', 'UNDO'}

    @classmethod
    def register_keymaps(cls):
        args = cls, "Text"
        utils.kmi_new(*args, cls.bl_idname, 'BACK_SPACE', 'PRESS')
        utils.kmi_new(*args, cls.bl_idname, 'BACK_SPACE', 'PRESS', ctrl=1)
        utils.kmi_mute(*args, idname="text.delete", type='BACK_SPACE', ctrl=1)
        utils.kmi_mute(*args, idname="text.delete", type='BACK_SPACE', ctrl=0)

    @classmethod
    def _register(cls):
        cls.define("%s_internal" % cls.__name__).properties.init = True
        cls.define("TEXT_OT_delete").properties.type = 'PREVIOUS_CHARACTER'
        cls.define("%s_internal" % cls.__name__)


class TEXTENSION_OT_drag_select(utils.TextOperator):
    """Drag selection and snap onto words"""
    bl_idname = "textension.drag_select"
    bl_label = "Drag Select"

    _end_keys = {'LEFTMOUSE', 'RIGHTMOUSE', 'ESC'}

    def modal(self, context, event):
        text = context.edit_text
        if event.type == 'MOUSEMOVE':
            self.word_select(event)
            icurl, icurc, iselc = self.init_get()
            curl, curc, sell, selc = cursor_get(text)
            if (curc < icurc and curl == icurl) or curl < icurl:
                icurc, selc = iselc, curc
            text.select_set(icurl, icurc, sell, selc)

        elif event.type in self._end_keys:
            return {'FINISHED'}
        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        # Skip if cursor is scrollbar region.
        if in_scroll(event, context.region):
            return {'PASS_THROUGH'}
        # Set initial selection range.
        bpy.ops.text.select_word()
        tc = TextContext(context)
        icurl, icurc, isell, iselc = tc.cursor

        def init_get():
            return icurl, icurc, iselc

        # TODO: Use _call.
        def word_select(event):
            mrx, mry = event.mouse_region_x, event.mouse_region_y
            bpy.ops.text.cursor_set(x=mrx + 2, y=mry)
            bpy.ops.text.select_word('INVOKE_DEFAULT')

        self.word_select = word_select
        self.init_get = init_get
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


class TEXTENSION_OT_extend_selection(utils.TextOperator):
    """Extend selection on either side cursor"""
    bl_idname = "textension.extend_selection"
    bl_label = "Extend Selection"

    @classmethod
    def register_keymaps(cls):
        name = cls.bl_idname
        utils.kmi_new(cls, "Text", name, 'LEFTMOUSE', 'PRESS', shift=1)

    _stop_keys = {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE',
                  'ESC', 'SPACE', 'RET'}

    def modal(self, context, event):
        event_type = event.type

        if event_type == 'MOUSEMOVE':
            self.extend_selection()

        if event_type in self._stop_keys or \
           event_type == self.init_key and event.value == 'RELEASE':
            return {'FINISHED'}
        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        tc = TextContext(context)
        curl, curc = tc.cursor[:2]

        # TODO: Use _call.
        def extend_selection():
            bpy.ops.text.cursor_set('INVOKE_DEFAULT')
            tc.curl = curl
            tc.curc = curc

        self.init_key = event.type
        self.extend_selection = extend_selection
        extend_selection()
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


class TEXTENSION_OT_line_break_internal(utils.TextOperator):
    bl_idname = "textension.line_break_internal"
    bl_label = "Line Break Internal"
    bl_options = {'INTERNAL'}

    init: bpy.props.BoolProperty(
        default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def do_init(self, cls, tc, context):
        cls.selection = tc.has_sel
        cls.cursor = tc.cursor
        cls.body = tc.endl_body
        # New line, move only cursor.
        if setdefault(self, "ctrl", False) or self.shift:

            # Add colon on current like (shift) if the line is a
            # control-flow, loop, function or class statement.
            if self.shift:
                line = tc.text.select_end_line
                tmp = line.body.strip().lstrip()
                startsw = tmp.startswith
                endsw = tmp.endswith
                kw = ("class ", "def ", "if ", "elif ", "for ", "while ")
                if (startsw(kw) and not endsw(":")) or \
                   (startsw("else") and endsw("else")):
                    if startsw("def ") and not any(c in tmp for c in "()"):
                        line.body += "()"
                    line.body += ":"

            bpy.ops.text.move(type='LINE_END')
        # Delete selection.
        elif tc.has_sel and not self.ctrl:
            bpy.ops.text.delete(type='PREVIOUS_CHARACTER')
            cls.body = cls.body.replace(tc.sel_string, "")
            tc.update(context)
            cls.cursor = tc.cursor
            cls.selection = False
        return {'FINISHED'}

    def execute(self, context):
        def line_break():
            return bpy.ops.text.line_break('INVOKE_DEFAULT')
        cls = __class__
        tc = TextContext(context)

        if self.init:
            return self.do_init(cls, tc, context)

        curl, curc, sell, selc = cls.cursor
        body = cls.body
        has_selection = cls.selection

        # Strip trailing whitespace.
        def strip():
            line = tc.lines[tc.curl_ext - 1]
            if not line.body.strip():
                line.body = ""

        indent_l1 = tc.indent_get(body) + tc.tab
        if not selc or body[selc - 1] not in {"(", "[", "{"} or has_selection:
            strip()
            return {'FINISHED'}

        body_new = " " * indent_l1 + body[selc:]
        cursor_pos = curl + 1, indent_l1, curl + 1, indent_l1

        # Find first closing bracket.
        for c in body[selc:]:

            # Add two newlines.
            if c in {")", "]", "}"}:
                line_break()
                body_new = " " * indent_l1
                cursor_pos = curl + 1, curc, curl + 1, selc

            elif c == " ":
                continue
            break

        if not self.ctrl:
            tc.lines[curl + 1].body = body_new
        tc.cursor = cursor_pos
        strip()
        return {'FINISHED'}

    def invoke(self, context, event):
        self.ctrl = event.ctrl
        self.shift = event.shift
        return self.execute(context)


class TEXTENSION_OT_line_break(utils.TextMacro):
    """Contextual line break"""
    bl_idname = "textension.line_break"
    bl_label = "Line Break"
    bl_options = {'MACRO', 'UNDO'}

    @classmethod
    def register_keymaps(cls):
        utils.kmi_args(cls, "Text", cls.bl_idname, 'PRESS')
        utils.kmi_new('RET')
        utils.kmi_new('RET', ctrl=1)
        utils.kmi_new('RET', shift=1)
        utils.kmi_new('NUMPAD_ENTER')
        utils.kmi_new('NUMPAD_ENTER', ctrl=1)

    @classmethod
    def _register(cls):
        cls.define(cls.__name__ + "_internal").properties.init = True
        cls.define("TEXT_OT_line_break")
        cls.define(cls.__name__ + "_internal")


# Fixes blender not able to unindent lines under tab length.
class TEXTENSION_OT_unindent_internal(utils.TextOperator):
    bl_idname = "textension.unindent_internal"
    bl_label = "Unindent Internal"
    bl_options = {'INTERNAL'}

    init: bpy.props.BoolProperty(
        default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def unindent_lines(cls, lines=None):
        if lines is None:
            return getattr(cls, "_lines", [])
        cls._lines = lines

    def execute(self, context):
        tc = TextContext(context)
        if self.init:

            def count_whitespace(string):
                for idx, c in enumerate(string):
                    if c not in " \t":
                        return idx
                return 0

            index = tc.lines[:].index
            lines = [index(line) for line in tc.sel_lines
                     if 0 < count_whitespace(line.body) < 4]
            self.unindent_lines(lines)
            return {'FINISHED'}

        else:
            for idx in self.unindent_lines():
                line = tc.lines[idx]
                line.body = line.body.lstrip(" ")
            return {'FINISHED'}


class TEXTENSION_OT_unindent(utils.TextMacro):
    bl_idname = "textension.unindent"
    bl_label = "Unindent"
    bl_options = {'MACRO', 'UNDO'}

    @classmethod
    def register_keymaps(cls):
        utils.kmi_new(cls, "Text", cls.bl_idname, 'TAB', 'PRESS', shift=1)

    @classmethod
    def _register(cls):
        cls.define("%s_internal" % cls.__name__).properties.init = True
        cls.define("TEXT_OT_unindent")
        cls.define("%s_internal" % cls.__name__)


class TEXTENSION_OT_move_toggle(utils.TextOperator):
    """Toggle cursor position between line start and indent"""
    bl_idname = "textension.move_toggle"
    bl_label = "Move Toggle"

    @classmethod
    def register_keymaps(cls):
        utils.kmi_new(cls, "Text", cls.bl_idname, 'HOME', 'PRESS')
        utils.kmi_new(cls, "Text", cls.bl_idname, 'HOME', 'PRESS', shift=1)

    def invoke(self, context, event):
        tc = TextContext(context)
        body = tc.endl_body
        indent = tc.indent_get(body)

        pos = bool(tc.selc != indent and body.strip()) and indent
        tc.text.cursor_set(tc.sell, character=pos, select=event.shift)
        return {'FINISHED'}


class DefaultDict(dict):
    def __missing__(self, key):
        return None


class TEXTENSION_OT_expand_to_brackets(utils.TextOperator):
    """Expand selection to closest brackets"""
    bl_idname = "textension.expand_to_brackets"
    bl_label = "Expand to Brackets"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def register_keymaps(cls):
        utils.kmi_new(cls, "Text", cls.bl_idname, 'A', 'PRESS', alt=1)

    def execute(self, context):
        tc = TextContext(context)
        body = tc.endl_body
        curl, curc, sell, selc = tc.cursor_sorted

        # Multi-line expansion not supported.
        if curl != sell:
            return {'CANCELLED'}
        pos = range(curc, selc + 1)

        bopen = DefaultDict(("()", "[]", "{}"))
        bquot = DefaultDict(("\"\"", "\'\'"))
        bclose = DefaultDict((")(", "][", "}{"))

        # Grow selection if boundary at brackets.
        if curc and selc < len(body):
            c = body[curc - 1]
            if (bopen[c] == body[selc] or bquot[c] == body[selc]):
                tc.cursor = curl, curc - 1, curl, selc + 1
                return {'FINISHED'}

        # Find quotes leading up to cursor.
        bpre = []
        for i, c in enumerate(body):
            if c in bquot and i < pos[0]:
                if bpre and c == bpre[-1][1]:
                    bpre.pop()
                elif c not in bpre:
                    bpre.append((i, c))

        # Skip inner, escapable single-quotes.
        if len(bpre) > 1 and bpre[-1][1] == "\'":
            bpre.pop()

        # Determine if cursor is inside a quote.
        if bpre and not (len(bpre) / 2).is_integer():
            qi, q = bpre[-1]
            for i, c in enumerate(body[qi + 1:], qi + 1):
                if c == q:
                    tc.cursor = curl, qi + 1, curl, i
                    return {'FINISHED'}

        # Find the first (open) bracket leading up to cursor.
        stack = []
        inner = outer = -1
        for i, c in enumerate(body):
            if i < pos[0]:
                if c in bopen:
                    stack.append((i, c))
                elif stack and bclose[c] == stack[-1][1]:
                    stack.pop()
        if stack:
            inner = stack[-1][0]

            # Find the first (closed) bracket past cursor.
            stack2 = []
            for i, c in enumerate(body[inner:], inner):
                if i >= pos[-1]:
                    if c in bopen:
                        stack2.append(c)
                    elif c in bclose:
                        if stack2 and bclose[c] == stack2[-1]:
                            stack2.pop()
                        elif bclose[c] == body[inner]:
                            outer = i
                            break
            if outer != -1:
                tc.cursor = curl, inner + 1, curl, outer
                return {'FINISHED'}
        return {'CANCELLED'}


class TEXTENSION_OT_search_with_selection(utils.TextOperator):
    """Focus search with selected text"""
    bl_idname = "textension.search_with_selection"
    bl_label = "Search With Selection"

    @classmethod
    def register_keymaps(cls):
        utils.kmi_new(cls, "Text Generic", cls.bl_idname, 'F', 'PRESS', ctrl=1)

    def execute(self, context):
        tc = TextContext(context)
        tc.st.use_find_wrap = True

        if tc.sel_string:
            tc.st.find_text = tc.sel_string
        bpy.ops.text.start_find('INVOKE_DEFAULT')
        return {'CANCELLED'}


class TEXTENSION_OT_timeit_template(utils.TextOperator):
    """Add a simple timeit template"""
    bl_idname = "textension.timeit_template"
    bl_label = "Timeit Template"

    template = """from timeit import timeit


        def run():
            pass



        t = timeit("run()", "from __main__ import run", number=1)
        #print(f"{round(t * 1000, 4)} ms")
        print(" " * 20, f"{round(t * 1000, 4)} ms", sep="\\r", end="\\r")
        #print(run())
        """

    @classmethod
    def poll(cls, context):
        return context.area.type == 'TEXT_EDITOR'

    def execute(self, context):
        text = context.blend_data.texts.new("timeit.py")
        text.write(self.template.replace(" " * 8, ""))
        context.space_data.text = text
        context.area.tag_redraw()
        return {'FINISHED'}


# XXX Not used
# class TEXTENSION_OT_auto_sync(Operator):
#     """Automatically syncs changes from source file"""
#     bl_idname = "textension.auto_sync"
#     bl_label = "Auto Sync"

#     _sync_text = None

#     def sync_text(self, text):
#         if text.is_modified:
#             bpy.ops.text.resolve_conflict(resolution='RELOAD')

#     def execute(self, context):
#         st = context.space_data
#         cls = __class__

#         sync_text = getattr(cls, "_sync_text", None)

#         if sync_text:
#             try:
#                 st.draw_handler_remove(sync_text, 'WINDOW')
#             except ValueError:
#                 pass
#             setattr(cls, "_sync_text", None)

#         sync_text = st.draw_handler_add(
#             self.sync_text, (st.text,), 'WINDOW', 'POST_PIXEL')
#         setattr(cls, "_sync_text", sync_text)
#         return {'FINISHED'}


class TEXTENSION_OT_scroll(utils.TextOperator):
    """Smooth ish Scroll"""
    bl_idname = "textension.scroll"
    bl_label = "Scroll"

    @classmethod
    def register_keymaps(cls):
        kmi_new = utils.kmi_new
        utils.kmi_args(cls, "Text", cls.bl_idname, 'PRESS')

        kmi_new('HOME', ctrl=1, note="Scroll to Top").type = 'TOP'
        op = kmi_new('HOME', ctrl=1, shift=1, note="Select to Top")
        op.type = 'TOP'
        op.select = True

        kmi_new('END', ctrl=1, note="Scroll to Bottom").type = 'BOTTOM'
        op = kmi_new('END', ctrl=1, shift=1, note="Select to Bottom")
        op.type = 'BOTTOM'
        op.select = True

        kmi_new('PAGE_UP', note="Scroll Page Up").type = 'PAGEUP'
        op = kmi_new('PAGE_UP', shift=1, note="Select Page Up")
        op.type = 'PAGEUP'
        op.select = True

        kmi_new('PAGE_DOWN', note="Scroll Page Down").type = 'PAGEDN'
        op = kmi_new('PAGE_DOWN', shift=1, note="Select Page Down")
        op.type = 'PAGEDN'
        op.select = True

    _items = (('PAGEUP', "Page Up", "Scroll up one page"),
              ('PAGEDN', "Page Down", "Scroll down one page"),
              ('TOP', "Top", "Scroll to top"),
              ('BOTTOM', "Bottom", "Scroll to bottom"),
              ('CURSOR', "To Cursor", "Scroll to cursor"),
              ('JUMP', "Jump", "Jump to line"))

    type: bpy.props.EnumProperty(
        default='PAGEDN', items=_items, options={'SKIP_SAVE'})
    lines: bpy.props.IntProperty(default=1, options={'SKIP_SAVE'})
    jump: bpy.props.IntProperty(default=0, options={'SKIP_SAVE'})
    char: bpy.props.IntProperty(default=0, options={'SKIP_SAVE'})
    history: bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'})
    use_smooth: bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'})
    select: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})

    from mathutils.geometry import interpolate_bezier as bezier
    from mathutils import Vector

    def execute(self, context):
        tc = TextContext(context)
        st = context.space_data
        idx_max = tc.lenl - 1
        view_half = tc.vlines // 2
        region = context.region
        top = st.top

        # Next cursor position.
        sell_dest = min(idx_max, tc.sell + tc.vlines)

        # Scroll destination (new st.top).
        scroll_dest = tc.sell + view_half

        def clamp_top(val):
            return 0 if val < 0 else idx_max if val > idx_max else val
        direction = 'DOWN'
        if self.type == 'PAGEUP':
            direction = 'UP'
            sell_dest = clamp_top(tc.sell - tc.vlines)
            scroll_dest = clamp_top(sell_dest - view_half)
        elif self.type == 'TOP':
            direction = 'UP'
            sell_dest = scroll_dest = 0
        elif self.type == 'BOTTOM':
            direction = 'DOWN'
            sell_dest = idx_max
            scroll_dest = tc.lenl - view_half
        elif self.type == 'CURSOR':
            sell_dest = tc.sell
            if sell_dest > tc.sell:
                direction = 'DOWN'
            else:
                direction = 'UP'
            scroll_dest = clamp_top(tc.sell - view_half)
        elif self.type == 'JUMP':
            sell_dest = self.jump
            if sell_dest > tc.sell:
                direction = 'DOWN'
            else:
                direction = 'UP'
            scroll_dest = clamp_top(sell_dest - view_half)

        offset = offset_lines_get(st, region.width, end=max(0, sell_dest))

        # Allow instant scrolling. Prefs setting?
        if not self.use_smooth:
            st.top = scroll_dest
            return {'FINISHED'}

        # Animate scroll when cursor is outside of view.
        view_top = top - offset + 2
        view_bottom = top - offset + tc.vlines - 2

        if sell_dest not in range(view_top, view_bottom):
            bpy.ops.textension.scroll2(
                'INVOKE_DEFAULT', lines=scroll_dest - top, direction=direction)

        # Ensure cursor position on destination line.
        if self.type != 'CURSOR':
            if self.type == 'TOP':
                pos = 0
            elif self.type == 'BOTTOM':
                pos = len(tc.lines[-1].body)
            elif self.type == 'JUMP':
                pos = min(self.char, len(tc.lines[sell_dest].body))
            else:
                pos = min(len(tc.lines[sell_dest].body), tc.curc_sorted)
            tc.text.cursor_set(sell_dest, character=pos, select=self.select)

        # Append new cursor position to history.
        if self.history:
            textman[tc.text].cursor.add()
        return {'FINISHED'}


class TEXTENSION_OT_find(utils.TextOperator):
    bl_idname = "textension.find"
    bl_label = "Find"

    @classmethod
    def register_keymaps(cls):
        utils.kmi_args(cls, "Text", cls.bl_idname, 'PRESS', alt=1)
        utils.kmi_new('F', note="Find Next").direction = 'NEXT'
        utils.kmi_new('D', note="Find Previous").direction = 'PREV'

    case_sensitive: bpy.props.BoolProperty(default=False)
    direction: bpy.props.EnumProperty(
        items=(('NEXT', "Next", ""), ('PREV', "Previous", "")))

    def execute(self, context):
        tc = TextContext(context)
        string = tc.sel_string
        _lines = tc.lines

        if not string:
            return {'CANCELLED'}
        if not self.case_sensitive:
            string = string.lower()

        strlen = len(string)
        reverse = self.direction != 'NEXT'

        if reverse:
            lines = reversed((*enumerate(_lines[:tc.curl + 1]),))
            find = str.rfind
        else:
            lines = enumerate(_lines[tc.curl:], tc.curl)
            find = str.find

        def _find(lines, reverse=reverse):
            for idx, line in lines:
                body = line.body

                if not self.case_sensitive:
                    body = line.body.lower()

                if string in body:
                    head = 0 if reverse or idx > tc.curl else tc.selc
                    tail = tc.curc if reverse and idx == tc.curl else len(body)
                    match = find(body, string, head, tail)
                    if match != -1:
                        tc.cursor = idx, match, idx, match + strlen
                        textman[tc.text].cursor.add()
                        return idx

        ret = _find(lines)
        if ret is None:
            if reverse:
                lines = reversed((*enumerate(_lines[tc.curl:], tc.curl),))
            else:
                lines = enumerate(_lines[:tc.curl])
                find = str.find
            ret = _find(lines, reverse=not reverse)

        if ret is not None:
            st = context.space_data
            if ret not in range(st.top, st.top + tc.vlines - 2):
                bpy.ops.textension.scroll(type='CURSOR')
            return {'FINISHED'}
        return {'CANCELLED'}


# Used with goto operator.
class TEXTENSION_PT_goto(bpy.types.Panel):
    bl_label = "Go to Line"
    bl_space_type = 'TEXT_EDITOR'
    bl_region_type = 'WINDOW'

    def draw(self, context):
        layout = self.layout
        layout.ui_units_x = 8
        layout.label(text="Go to Line")
        layout.activate_init = True
        layout.prop(TEXTENSION_OT_goto.kmi_prop(), "line", text="")

    def __init__(self):
        endl = bpy.context.edit_text.select_end_line_index
        TEXTENSION_OT_goto.kmi_prop().line = str(endl + 1)


class TEXTENSION_OT_goto(utils.TextOperator):
    """A more streamlined goto operator"""
    bl_idname = "textension.goto"
    bl_label = "Go to Line"

    @staticmethod
    def kmi_prop():
        return __class__._keymaps[0][1].properties

    def goto(self, context):
        lenl = len(context.edit_text.lines)
        line = max(1, min(lenl, int(__class__.kmi_prop().line)))
        bpy.ops.textension.scroll(type='JUMP', jump=line - 1)

    line: bpy.props.StringProperty(options={'HIDDEN'}, update=goto)

    def invoke(self, context, event):
        mx, my = event.mouse_x, event.mouse_y
        cursor_warp = context.window.cursor_warp

        # Create popup somewhat centered.
        center_x = context.area.x + (context.area.width // 2)
        center_y = context.area.y - 20 + (context.area.height // 2)

        cursor_warp(center_x, center_y)
        bpy.ops.wm.call_panel(name="TEXTENSION_PT_goto", keep_open=False)
        cursor_warp(mx, my)
        return {'CANCELLED'}

    @classmethod
    def register_keymaps(cls):
        utils.kmi_new(cls, "Text Generic", cls.bl_idname, 'J', 'PRESS', ctrl=1)


class TEXTENSION_OT_cursor_history(utils.TextOperator):
    """Step through cursor history"""
    bl_idname = "textension.cursor_history"
    bl_label = "Cursor History"

    @classmethod
    def register_keymaps(cls):
        utils.kmi_args(cls, "Text", cls.bl_idname, 'PRESS')
        utils.kmi_new('BUTTON4MOUSE', note="Cursor History Back"
                      ).dir = 'BACK'
        utils.kmi_new('BUTTON5MOUSE', note="Cursor History Forward"
                      ).dir = 'FORWARD'

    _items = (("FORWARD", "Forward", ""), ("BACK", "Back", ""))
    dir: bpy.props.EnumProperty(items=_items)

    def execute(self, context):
        if cursor_history_step(context, self.dir):
            return {'FINISHED'}
        return {'CANCELLED'}


# Unified operator for setting cursor and selection.
# - Allow scrolling while selecting.
# - Allow line selection from line numbers margin.
# - Allow double click drag selection
# - Allow triple click line selection
class TEXTENSION_OT_cursor(utils.TextOperator):
    bl_idname = "textension.cursor"
    bl_label = "Cursor"

    @classmethod
    def register_keymaps(cls):
        note = "Set Cursor"
        args = cls, "Text"
        utils.kmi_new(*args, cls.bl_idname, 'LEFTMOUSE', 'PRESS', note=note)
        utils.kmi_mute(*args, idname="text.selection_set")
        utils.kmi_mute(*args, idname="text.cursor_set")
        utils.kmi_mute(*args, type='LEFTMOUSE', value='DOUBLE_CLICK')

    _clicks = 0
    _mxy = -1, -1
    _click_time = -1
    _ignore = {'TIMER', 'TIMER_REPORT'}
    _allow = {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'INBETWEEN_MOUSEMOVE',
              'MOUSEMOVE', 'NONE', 'EVT_TWEAK_L', *_ignore, 'LEFTMOUSE'}

    line_select: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})

    def execute(self, context):
        kwargs = self.as_keywords()
        return bpy.ops.textension.cursor('INVOKE_DEFAULT', **kwargs)

    def modal(self, context, event):
        # Update selection only when not scrolling.
        if TEXTENSION_OT_scroll2.jobs:
            return {'PASS_THROUGH'}

        # Ignore timer events.
        if event.type in self._ignore:
            return {'RUNNING_MODAL'}

        # Allow mouse wheel scrolling while selecting.
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            direction = 'DOWN'
            if event.type == 'WHEELUPMOUSE':
                direction = 'UP'
            bpy.ops.textension.scroll2('INVOKE_DEFAULT', direction=direction)
            return {'RUNNING_MODAL'}

        # End.
        if event.value == 'RELEASE' or event.type not in self._allow:
            if event.type not in {'EVT_TWEAK_L'}:
                __class__.active = False
                cursor_history_add(context)
                return {'FINISHED'}

        # Update cursor.
        self.cursor_set(event, line_select=self.line_select)
        cursor = self.cursor_edge(event)
        if cursor:
            self.scroll(cursor)
        return {'RUNNING_MODAL'}

    def select_line(self, context):
        tc = TextContext(context)
        tc.curc = 0
        if tc.sell != tc.lenl - 1:
            tc.selc = 0
            tc.sell += 1
        else:
            tc.selc = -1
        return {'FINISHED'}

    @utils.classproperty
    @classmethod
    def active(cls):
        return setdefault(cls, "_active", False)

    @active.setter
    @classmethod
    def active(cls, value):
        cls._active = value

    @utils.classproperty
    @classmethod
    def mxy(cls):
        return cls._mxy

    @mxy.setter
    @classmethod
    def mxy(cls, value):
        cls._mxy = value

    @utils.classproperty
    @classmethod
    def clicks(cls):
        return cls._clicks

    @clicks.setter
    @classmethod
    def clicks(cls, value):
        cls._clicks = value

    @utils.classproperty
    @classmethod
    def click_time(cls):
        return cls._click_time

    @click_time.setter
    @classmethod
    def click_time(cls, value):
        cls._click_time = value

    def isclose_vec2(self, vec2a, vec2b, tol=3):
        x1, y1 = vec2a
        x2, y2 = vec2b
        if abs(x1 - x2) > tol or abs(y1 - y2) > tol:
            return False
        return True

    def count_clicks(self, event):
        mxy = event.mouse_region_x, event.mouse_region_y
        # Allow an error margin of 3 pixels.
        if not self.isclose_vec2(self.mxy, mxy):
            self.mxy = mxy
            self.clicks = 1
            return 1
        if self.clicks == 1:
            self.mxy = mxy
        if mxy == self.mxy:
            if perf_counter() < self.click_time + 0.7:
                if self.clicks > 2:
                    self.clicks = 0
                    return 3
                self.clicks += 1
                return self.clicks

        self.clicks = 1
        self.mxy = mxy
        self.click_time = perf_counter()
        return self.clicks

    def invoke(self, context, event):
        region = context.region
        # Skip if cursor is scrollbar region.
        if in_scroll(event, region):
            return {'PASS_THROUGH'}

        st = context.space_data
        rh = region.height
        x2 = lnum_margin_width_get(st)
        clicks = self.count_clicks(event)
        if clicks == 1:
            self.click_time = perf_counter()

        # Don't count towards clicks if cursor in line number margin.
        in_marg = st.show_line_numbers and cursor_isect_xy(event, 0, x2, 0, rh)
        do_line_sel = prefs.use_line_number_select and in_marg

        if not do_line_sel:
            if clicks == 2:
                return bpy.ops.textension.drag_select('INVOKE_DEFAULT')
            if clicks == 3:
                self.clicks = 0
                return self.select_line(context)

        self.line_select = do_line_sel
        self.click_time = perf_counter()
        self.active = True
        lh = int(1.3 * (int(utils.wunits_get() * st.font_size) // 20))
        text = st.text
        rw = region.width

        # Approximate cursor position (line).
        def cursor_pos_y(event):
            return st.top + int((rh - event.mouse_region_y - 3) / lh)

        # Initial line during line selection. Keep track so the cursor end can
        # be moved up or down based on its relative position.
        self.init_line = cursor_pos_y(event)

        # Actual scroll function
        def scroll(val):
            _max = self.scroll_max
            val = min(5, max(-5, val))
            if val > 0 and st.top < _max or val < 0 and st.top > 0:
                st.top += val
                st.top = min(_max, max(0, st.top))

        # Determine if cursor is near region top/bottom.
        def cursor_edge(event=event):
            mry = event.mouse_region_y
            pos = None
            if mry > rh - lh:
                pos = (rh - lh - mry) // 10
            elif mry < lh:
                pos = (lh - mry) // 10
            return pos and min(5, max(-5, pos))

        # Cursor set function.
        def cursor_set(event, select=True, line_select=False):
            x = event.mouse_region_x
            y = min(rh, max(lh, event.mouse_region_y))
            _call('TEXT_OT_cursor_set', {}, {'x': x, 'y': y})
            if select:
                text.current_line_index = curl
                text.current_character = curc
            if line_select:
                if cursor_pos_y(event) >= self.init_line:
                    text.select_end_line_index += 1
                else:
                    text.current_character = len(text.current_line.body)

        self.cursor_set = cursor_set
        self.scroll = scroll
        self.cursor_edge = cursor_edge
        self.scroll_max = (len(text.lines) - st.visible_lines // 2)
        self.scroll_lines = prefs.wheel_scroll_lines
        if st.show_word_wrap:
            self.scroll_max += offset_lines_get(st, rw)

        context.window_manager.modal_handler_add(self)
        cursor_set(event, select=False, line_select=self.line_select)
        curl = text.current_line_index
        curc = text.current_character
        return {'RUNNING_MODAL'}


class TEXTENSION_OT_select_all(utils.TextOperator):
    bl_idname = "textension.select_all"
    bl_label = "Select All"

    @classmethod
    def register_keymaps(cls):
        utils.kmi_new(cls, "Text", cls.bl_idname, 'A', 'PRESS', ctrl=1)

    def execute(self, context):
        context.space_data.text.select_set(0, 0, -1, -1)
        return {'CANCELLED'}


# TODO: Needs fixing.
class TEXTENSION_OT_toggle_header(utils.TextOperator):
    bl_idname = "textension.toggle_header"
    bl_label = "Toggle Header"

    @classmethod
    def register_keymaps(cls):
        utils.kmi_args(cls, "Text Generic", cls.bl_idname, "PRESS")
        utils.kmi_new('LEFT_ALT')
        utils.kmi_new('RIGHT_ALT')

    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, "type", "") == 'TEXT_EDITOR'

    def modal(self, context, event):
        return self.modal_inner(context, event)

    def invoke(self, context, event):
        end = False
        wm = context.window_manager
        alts = {'LEFT_ALT', 'RIGHT_ALT'}
        mouse = {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}
        t = wm.event_timer_add(1 / 120, window=context.window)

        def modal_inner(context, event):
            nonlocal end
            if end:
                wm.event_timer_remove(t)
                return {'CANCELLED'}
            if event.type == 'TIMER':
                return {'PASS_THROUGH'}
            end = event.type not in alts | mouse
            if not event.alt:
                context.space_data.show_region_header ^= True
                end = True
            return {'PASS_THROUGH'}

        self.modal_inner = modal_inner
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}


# Allow line numbers margin to be used for selecting lines.
class TEXTENSION_OT_line_select(utils.TextOperator):
    bl_idname = "textension.line_select"
    bl_label = "Line Select"

    @classmethod
    def register_keymaps(cls):
        args = cls, "Text", cls.bl_idname
        utils.kmi_new(*args, 'MOUSEMOVE', 'ANY', note="HIDDEN")

    @classmethod
    def poll(cls, context):
        try:
            st = context.space_data
            return st.text and st.show_line_numbers and \
                not TEXTENSION_OT_cursor.active
        except AttributeError:
            pass

    @classmethod
    def operator_active(cls, state=None):
        if state is None:
            return getattr(cls, "_active", False)
        cls._active = state

    @classmethod
    def in_margin(cls, state=None):
        if state is None:
            return getattr(cls, "_in_margin", False)
        cls._in_margin = state

    # Stop when cursor isn't moving.
    @classmethod
    def timeout(cls, context, restore=False):
        context.window_manager.event_timer_remove(cls._timer)
        del cls._timer
        cls.operator_active(False)
        if restore:
            context.window.cursor_modal_restore()
        return {'CANCELLED'}

    def modal(self, context, event):
        return self.modal_inner(context, event)

    def in_region(self, x2, region, event):
        state = cursor_isect_xy(event, 0, x2, 0, region.height)
        self.in_margin(state)
        return state

    def invoke(self, context, event):
        region = context.region
        st = context.space_data
        if self.operator_active() or region.type != 'WINDOW' or \
           not st.show_line_numbers:
            return {'CANCELLED'}

        x2 = lnum_margin_width_get(st)
        in_region = self.in_region
        if not in_region(x2, region, event):
            return {'CANCELLED'}

        ttl = monotonic() + 0.1
        self.operator_active(True)
        assert getattr(__class__, "_timer", None) is None
        mouse_skip = {'LEFTMOUSE', 'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}

        def modal_inner(context, event):
            nonlocal ttl
            t = monotonic()
            # Extend time-to-live.
            if event.type == 'MOUSEMOVE':
                ttl = t + 0.1
            if in_region(x2, region, event):
                if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                    bpy.ops.textension.cursor(line_select=True)
                    return self.timeout(context)

                # Allow timeout inside margin.
                if event.type == 'TIMER' and t > ttl:
                    return self.timeout(context)
                if event.type not in mouse_skip:
                    return {'PASS_THROUGH'}
                return {'RUNNING_MODAL'}

            # Cursor is outside margin, exit and restore cursor type.
            return self.timeout(context, restore=True)

        self.modal_inner = modal_inner
        __class__._timer = context.window_manager.event_timer_add(
            0.03, window=context.window)
        context.window_manager.modal_handler_add(self)
        context.window.cursor_modal_set("DEFAULT")
        return {'RUNNING_MODAL'}


class TEXTENSION_OT_scroll_continuous(utils.TextOperator):
    bl_idname = "textension.scroll_continuous"
    bl_label = "Continuous Scroll"

    @classmethod
    def register_keymaps(cls):
        utils.kmi_new(cls, "Text", cls.bl_idname, 'MIDDLEMOUSE', 'PRESS')

    def invoke(self, context, event):
        region = context.region
        if in_scroll(event, region):
            return {'PASS_THROUGH'}
        my = event.mouse_region_y
        wm = context.window_manager
        scroll_max = scroll_max_get(context.space_data, region.width)
        timer = wm.event_timer_add(1e-5, window=context.window)
        rt = STRuntime(context, scroll_max)

        # Frame pacing.
        t_step = 1 / 200
        t_next = perf_counter() + t_step

        def in_sync():
            nonlocal t_next
            t_now = perf_counter()
            if t_now > t_next:
                t_next = t_now + t_step
                return True

        def inner_modal(event):
            if in_sync():
                # 30 px dead-zone.
                delta_y = my - event.mouse_region_y
                if -15 < delta_y < 15:
                    px = 0
                else:
                    # Gradually increase scroll by distance.
                    px = abs(delta_y ** 1.75) / 12 / 6 / 4 / 1.5
                    if delta_y < 0:
                        px = -px
                rt.offs_px += max(-200, min(200, px))
                region.tag_redraw()

            if event.value == 'RELEASE':
                # Snap to closest line.
                lh = rt.lheight
                px = rt.offsets[1]
                rt.offs_px += lh - px if px >= lh // 2 else -px
                wm.event_timer_remove(timer)
                context.window.cursor_modal_restore()
                region.tag_redraw()
                return {'FINISHED'}
            return {'RUNNING_MODAL'}

        self.inner_modal = inner_modal
        context.window.cursor_modal_set("SCROLL_Y")
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    # Handle modal in closed function.
    def modal(self, context, event):
        return self.inner_modal(event)


class TEXTENSION_OT_scroll2(utils.TextOperator):
    bl_idname = "textension.scroll2"
    bl_label = "Scroll2"

    @classmethod
    def register_keymaps(cls):
        utils.kmi_args(cls, "Text", cls.bl_idname, 'PRESS')
        utils.kmi_new('WHEELDOWNMOUSE', note="Scroll Up").direction = 'DOWN'
        utils.kmi_new('WHEELUPMOUSE', note="Scroll Down").direction = 'UP'

    _timer = None
    direction: bpy.props.EnumProperty(
        default='DOWN', name="Direction", description="Scroll direction",
        items=(('UP', "Up", "Scroll up"), ('DOWN', 'Down', "Scroll down")))
    lines: bpy.props.IntProperty(
        default=-1,
        name="Lines",
        description="Lines to scroll when called by script",
        options={'SKIP_SAVE'})

    @utils.classproperty
    @classmethod
    def jobs(cls):
        return setdefault(cls, "_jobs", 0)

    @jobs.setter
    @classmethod
    def jobs(cls, value):
        cls._jobs = value

    @classmethod
    def set_job(cls, context, state):
        if not iadd_default(cls, "jobs", 0, 1 if state else -1):
            context.window_manager.event_timer_remove(cls._timer)

    def modal(self, context, event):
        return self.inner_modal(context, event)

    def invoke(self, context, event):
        up = self.direction == 'UP'
        # Scroll by external call.
        if self.lines != -1:
            lines = self.lines
            frames = min(45, max(24, int(abs(lines) / 4)))
        # Mouse wheel scroll.
        else:
            lines = prefs.wheel_scroll_lines * (-1 if up else 1)
            frames = max(6, int(75 / prefs.scroll_speed) - self.jobs)

        region = context.region
        scroll_max = scroll_max_get(context.space_data, region.width)
        finished = False
        redraw = region.tag_redraw
        rt = STRuntime(context, scroll_max)
        lh = rt.lheight

        # Interpolate a smooth scroll curve. v is a value between 0 and 1.
        def custom(v):
            # mul: Steepness of in-curve: Higher is faster.
            mul = 0.25
            vm = 1 - v
            vm2 = v ** 1.5 * vm
            return mul * v * vm ** 2 + 2.5 * vm2 + 0.5 * vm2 + v ** 3
        # from math import sin, pi
        # Offsets: Sum distance (in pixels) needed to scroll.
        start = 0
        end = lh * lines
        offsets = deque()
        for f in range(frames):
            v = f / (frames - 1)
            # value = sin(v ** 1.25 * (pi / 1.9)) * end  # Sine unused.
            value = custom(v) * end
            offsets.append(value - start)
            start = value
        offsets.rotate(-1)

        data = iter(offsets)
        clamp = min if up else max

        # Frame pacing.
        t_step = 1 / 140
        t_next = perf_counter() + t_step

        def sync():
            nonlocal t_next, finished
            t_now = perf_counter()
            if t_now > t_next:
                value = next(data, None)
                # No more data, end.
                if value is None:

                    # Snap offset to closest line.
                    if self.jobs == 1:
                        px = rt.offsets[1]
                        rt.offs_px += lh - px if px >= lh // 2 else -px
                    finished = True
                    redraw()
                    return False
                rt.offs_px += clamp(value, 0)
                t_next = t_now + t_step
                return True
            return False

        def inner_modal(context, event):
            if region is None or finished:
                self.set_job(context, False)
                return {'FINISHED'}

            if sync():
                redraw()
            return {'PASS_THROUGH'}

        self.inner_modal = inner_modal
        wm = context.window_manager
        wm.modal_handler_add(self)
        if not self.jobs:
            # Single timer for all ops that ends with the last job.
            # handle = wm.event_timer_add(1/240, window=context.window)
            __class__._timer = wm.event_timer_add(1e-5, window=context.window)
        self.set_job(context, True)
        return {'RUNNING_MODAL'}


# Keep classes cached.
def classes(as_dict=False):
    cached = setdefault(classes, "cached", None)
    cacheddict = setdefault(classes, "cacheddict", None)

    if cached is not None and not as_dict:
        return cached
    elif cacheddict is not None and as_dict:
        return cacheddict

    classes.cached = []
    for cls in globals().values():
        if hasattr(cls, "bl_rna") and cls.__module__ == __name__:
            classes.cached.append(cls)
    classes.cacheddict = {cls.__name__: cls for cls in classes.cached}
    if as_dict:
        return classes.cacheddict
    return classes.cached


# Assume keymaps aren't ready, so defer registration.
def register(ready=False):
    utils.bpy_version_check()

    if ready:
        return _register()

    keymaps = "Text", "Text Generic", "View2D", "Screen Editing"
    from .utils import keymaps_ensure
    return keymaps_ensure(register, keymaps)


def _register():
    from bpy.utils import register_class, _preferences

    # Support "Reload Scripts" reloading all submodules.
    if not highlights.HighlightOccurrencesPrefs.is_registered:
        bpy.utils.register_class(highlights.HighlightOccurrencesPrefs)

    # The order of registration is important.
    for clsname in "KeymapData", "Operators", "TextensionPreferences":
        register_class(getattr(utils, clsname))

    highlights.register()

    # Register operators.
    for cls in classes():
        register_class(cls)

    global prefs, loaded
    prefs = _preferences.addons[__package__].preferences

    # Add New Text operator to right-click
    set_text_context_menu(True)
    loaded = True


def unregister():
    from bpy.utils import unregister_class

    highlights.unregister()
    unregister_class(highlights.HighlightOccurrencesPrefs)

    for clsname in "KeymapData", "Operators", "TextensionPreferences":
        unregister_class(getattr(utils, clsname))

    for cls in classes():
        unregister_class(cls)

    del classes.cached
    Buffer.get().__init__()

    set_text_context_menu(False)
    global prefs
    prefs = None


loaded = True
del utils.TextMacro, utils.TextOperator, _bpy

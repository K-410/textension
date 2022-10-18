import bpy
import _bpy
from _bpy import context as _context
from . import utils, types, plugins
from .utils import prefs
from .km_utils import kmi_new, kmi_mute, kmi_args, kmi_op_args
from . import c_utils
import re
from collections import deque
from time import perf_counter


bl_info = {
    "name": "*Textension",
    "description": "Convenience operators for text editor",
    "author": "kaio",
    "version": (1, 0, 2),
    "blender": (3, 1, 0),
    "location": "Text Editor",
    "category": "*Text Editor"
}


_call = _bpy.ops.call
setdefault = utils.setdefault
iadd_default = utils.iadd_default
system = bpy.context.preferences.system

is_spacetext = bpy.types.SpaceTextEditor.__instancecheck__


def ensure_cursor_view(context, *, action:str = "center", smooth=True):
    """
    Ensure that the text cursor is in view in the editor.

    The default action, 'center' tries to center the view on the cursor line.
    The other action, 'lazy' scrolls a few lines past the cursor

    force: Whether to force scrolling even when cursor is in view.
    """
    text = context.edit_text
    st = context.space_data
    line, column = text.cursor_position
    top = st.internal.top
    bottom = top + st.visible_lines - 2
    y = st.region_location_from_cursor(0, 0)[1]
    y -= st.region_location_from_cursor(line, column)[1]
    cursor_line = y // st.runtime.lheight_px
    # TODO: clean this up...

    # print("\n" * 20)
    # print("Ensuring..")
    if top <= cursor_line <= bottom:
        # print("Nothing to ensure")
        return
    center = top + (st.visible_lines // 2)
    # Relative lines needed so that the top line is the cursor line.
    relative_lines = cursor_line - st.top
    # print("Top:", top)
    # print("Center:", center)
    # print("Bottom:", bottom)
    # print("Cursor line:", cursor_line)
    # print("Relative lines:", relative_lines)
    # print("Visible:", st.visible_lines)

    # print("Action:", action)

    # if cursor_line > bottom:
    #     # print("cursor is BELOW by", cursor_line - bottom)
    # elif cursor_line < top:
        # print("cursor is ABOVE by", top - cursor_line)
    # else:
    #     print("cursor is IN VIEW")

    if action == "center":
        move_lines = cursor_line - bottom - top
        # print("value:", move_lines)

    else:  # Implies "lazy"
        if cursor_line < top:
            move_lines = cursor_line - top
        elif cursor_line > bottom:
            move_lines = cursor_line - bottom
        else:
            return

    if smooth:
        bpy.ops.textension.scroll_lines('INVOKE_DEFAULT', lines=move_lines, exclusive=True)
    else:
        st.top += move_lines
    # Center text view on the cursor
    # if action == "center":
    #     print("performing center")
    #     relative_lines = center - top
    #     # relative_lines -= st.visible_lines // 2

    # elif action == "lazy":
    #     pass
    #     # if cursor_line >= bottom:
    #     #     relative_lines -= st.visible_lines - 2

    # if smooth:
    #     bpy.ops.textension.scroll_lines('INVOKE_DEFAULT', lines=relative_lines - st.visible_lines)
    # else:
    #     st.internal.top += relative_lines


def cursor_history_add(context, *args, **kwargs):
    textman(context.edit_text).cursor.add(*args, **kwargs)


def cursor_history_poll(context, forward=False):
    cursor = textman(context.edit_text).cursor
    if forward:
        return cursor.head
    return len(cursor.history) - 1 > cursor.head


def cursor_history_step(context, direction):
    tm = textman(context.edit_text)
    if direction == 'FORWARD':
        return tm.cursor.step_forward()
    return tm.cursor.step_back()


# Return True if cursor is within x1 <-> x2.
def cursor_isect_x(event, x1, x2):
    mrx = event.mouse_region_x
    if x1 < mrx:
        return mrx <= x2
    return False


def cursor_isect_xy(event, x1, x2, y1, y2):
    mrx = event.mouse_region_x
    mry = event.mouse_region_y
    if x1 < mrx:
        if mrx <= x2:
            if y1 < mry:
                return mry < y2
    return False


# Scroll activates 1 pixel too late (uses >, but needs >=).
def in_scroll(event, region):
    rw = region.width
    return cursor_isect_xy(
        event, rw - (system.wu * 0.6), rw, 0, region.height)


def copy_selection(text: bpy.types.Text, line_fallback=True) -> str:
    """Copies a text's current selection to the clipboard.
    If no selection, copy the entire line.
    """
    if len(text.lines) == 1 and not text.current_line.body:
        return None

    ltop, ctop, lbot, cbot = text.cursor_sorted

    # Nothing is selected - copy entire line
    if (ltop == lbot) is (ctop == cbot) is True and line_fallback:
        string = text.current_line.body + "\n"

    # Else copy just the selection
    else:
        string = text.selected_text

    bpy.context.window_manager.clipboard = string
    return string


def nav_menu_extend(self, context):
    layout = self.layout
    op = layout.operator
    layout.separator()
    op("textension.scroll", text="Page Up").type = 'PAGEUP'
    op("textension.scroll", text="Page Down").type = 'PAGEDN'
    layout.separator()
    op("textension.goto", text="Go To Line..")
    layout.separator()
    row = layout.row()
    row.operator("textension.cursor_history",
                 text="Next Cursor").dir_ = 'FORWARD'
    row.active = cursor_history_poll(context, forward=True)
    row = layout.row()
    row.operator(
        "textension.cursor_history", text="Previous Cursor").dir_ = 'BACK'
    row.active = cursor_history_poll(context)


def set_text_context_menu(state):
    rmb_menu = bpy.types.TEXT_MT_context_menu._dyn_ui_initialize()
    nav_menu = bpy.types.TEXT_MT_view_navigation._dyn_ui_initialize()
    if state:
        nav_menu.append(nav_menu_extend)
        rmb_menu.insert(0, lambda s, c: s.layout.operator("text.new"))
        return

    next((rmb_menu.__delitem__(rmb_menu.index(f))
         for f in rmb_menu if f.__module__ == __name__), 0)
    nav_menu.remove(nav_menu_extend)


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
    return max(1, int(pad / (lnum_digits_get(st.text) + 3)))


# Get wrap offset (in lines) between 'start' and 'end'.
def offl_get(st, rw, start=0, end=None) -> int:
    if not st.show_word_wrap:
        return 0
    text = st.text
    lines = text.lines
    c_max = get_wrap_width(st)
    # c_max = c_max_get(st, rw)
    if end is None:
        end = st.drawcache.nlines

    offset = 0
    for idx, line in enumerate(lines[start:end], start):
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


def string_wrap_offset(c_max, string):
    c_start = wraps = 0
    c_end = c_max
    for c_pos, c in enumerate(string):
        if c_pos - c_start >= c_max:
            wraps += 1
            c_start = c_end
            c_end += c_max
        elif c in " -":
            c_end = c_pos + 1
    return wraps


# Get wrap offset (in lines) between 'start' and 'end'.
def offl_get_ex(st, rw, start=0, end=None) -> int:
    if not st.show_word_wrap:
        return 0
    text = st.text
    lines = text.lines
    c_max = get_wrap_width(st)
    # c_max = c_max_get(st, rw)
    if end is None:
        end = st.drawcache.nlines

    # wraps = 0
    offset = 0
    for idx, line in enumerate(lines[start:end], start):
        body = line.body
        if len(body) < c_max:
            continue
        c_start = 0
        c_end = c_max
        for c_pos, c in enumerate(body):
            if c_pos - c_start >= c_max:
                offset += 1
                if offset + idx > end:
                    return idx
                c_start = c_end
                c_end += c_max
            elif c in " -":
                c_end = c_pos + 1
    return idx




# Get the absolute line/col from skipping screen space lines.
def skip_lines(context, numlines, line, col):
    st = context.space_data
    lines = st.text.lines
    if not st.show_word_wrap:
        return clamp(min(len(st.text.lines) - 1, line + numlines)), 0

    wrap_lines = deque()

    # Append to the right.
    if numlines > 0:
        start = line
        end = line + numlines
        append = wrap_lines.append
    # Append to the left.
    else:
        numlines = -numlines - 1
        start = clamp(line - numlines)
        end = line
        append = wrap_lines.appendleft

    # Append each wrap, storing line index and start offset.
    c_max = get_wrap_width(st)
    # c_max = c_max_get(st, context.region.width)
    for idx, line in enumerate(lines[start:end], start):
        body = line.body
        if len(body) <= c_max:
            append((idx, 0))
            continue

        c_start = 0
        c_end = c_max
        for c_pos, c in enumerate(body):
            if c_pos - c_start >= c_max:
                append((idx, c_start))
                c_start = c_end
                c_end += c_max
            elif c in " -":
                c_end = c_pos + 1
        append((idx, c_start))

    # Find the line and offset by counting relative lines.
    # If idx is not greater than numlines, return the last element.
    if wrap_lines:
        for idx, (line_idx, offset) in enumerate(wrap_lines, 2):
            if idx > numlines:
                break
        return line_idx, offset

    return line, 0


# Get the wrap offset (in lines) by current cursor position.
def offl_by_col(st: bpy.types.SpaceTextEditor, line, col) -> int:
    if not st.show_word_wrap:
        return 0
    body = st.text.lines[line].body
    c_max = get_wrap_width(st)
    if len(body) < c_max:
        return 0

    offset = c_start = 0
    c_end = c_max
    for c_pos, c in enumerate(body):
        if c_start >= col:
            return offset
        if c_pos - c_start >= c_max:
            offset += 1
            c_start = c_end
            c_end += c_max
        elif c in " -":
            c_end = c_pos + 1
    return offset


def get_wrap_width(st):
    runtime = st.runtime
    cwidth_px = runtime.cwidth_px or 8
    if st.show_line_numbers:
        x = cwidth_px * (runtime.lnum + 3)
    else:
        x = cwidth_px * 2
    ret = (st.region.width - system.wu - x) // cwidth_px
    if ret > 8:
        return ret
    return 8

# def c_max_get(st, rw, cwidth=None, as_float=False, system=system):
#     """
#     Return the maximum number of characters on a line before it gets wrapped.
#     """
#     if cwidth is None:
#         cwidth = st.drawcache.cwidth_px[0]
#     if cwidth == 0:
#         cwidth = 10
#     pad = cwidth
#     if st.show_line_numbers:
#         pad *= lnum_digits_get(st.text) + 3
#     span = rw - system.wu - pad
#     if as_float:
#         return max(span / cwidth, 8)
#     return max(span // cwidth, 8)


def clamp(val, limit=0) -> int:
    return limit if val < limit else val


# More performant way of calling bpy.ops.textenison.scroll2.
def scroll(*args, **kwargs):
    return _call("TEXTENSION_OT_scroll2", {}, kwargs, 'INVOKE_DEFAULT')


# A text manager to store runtime data. For now just storing cursor history.
# per text block.
class TextMan:
    class CursorHistory(dict):
        def __init__(self, text):
            self.text_name = text.name
            self.head = 0
            self.history = deque(maxlen=25)
            self.history.appendleft(self.text.cursor[-2:])

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

        # Jump cursor to a point in history.
        def scroll(self) -> set:
            line, char = self.history[self.head]
            return bpy.ops.textension.scroll(
                type='JUMP', jump=line, char=char, history=False)

        # Append cursor position to history.
        def add(self, line=None, char=None, replace=False) -> bool:

            self.gc()
            history = self.history
            if line is None or char is None:
                line, char = self.text.cursor[-2:]

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
            if replace or line in range(clamp(prev - 10), prev + 10):
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


# # Simple selection buffer to remember states. Use for cut/copy text and
# # comparing clipboard content.
# class Buffer:
#     @classmethod
#     def get(cls):
#         if not hasattr(Buffer, "_buffer"):
#             cls._buffer = Buffer()
#         return cls._buffer

#     def __init__(self):
#         self.buffer = ""
#         self.no_sel = False

#     def string_get(self) -> str:
#         return self.buffer

#     def string_set(self, value):
#         self.buffer = value

#     def state_get(self) -> bool:
#         return self.no_sel

#     def state_set(self, value: bool):
#         self.no_sel = value



class TEXTENSION_MT_override_preferences(bpy.types.Menu):
    """Make text editor always start with this enabled"""
    bl_label = "Text Editor Preferences"

    def draw(self, context):
        pass

    # TODO: Use a generic draw method for displaying stats.
    def draw_syntax_footer(self, context):
        text = context.edit_text
        if text is None:
            return

        layout = self.layout
        row = layout.row(align=True)
        row.alignment = 'RIGHT'

        # Dynamically add spacers based on the region width, since each
        # spacer occupies some fixed real estate. 
        separator_spacer = row.separator_spacer
        for i in range(int(context.region.width // 300 * system.wu * 0.05)):
            separator_spacer()

        stat = (f"Ln {text.select_end_line_index}   "
                f"Col {text.select_end_character}")

        lenseltext = len(text.selected_text)
        if lenseltext:
            text = "%s selected    " % lenseltext
            row.label(text=text)
        row.operator("textension.goto", text=stat, emboss=False)


    @classmethod
    def register(cls):
        bpy.types.TEXT_HT_footer.append(cls.draw_syntax_footer)

    @classmethod
    def unregister(cls):
        bpy.types.TEXT_HT_footer.remove(cls.draw_syntax_footer)


# ----------------------------------------------------------------------------
#    Text Editor Operators
# ----------------------------------------------------------------------------

class TEXTENSION_OT_new(types.TextOperator):
    @classmethod
    def poll(cls, context):
        # There are no requirements.
        return is_spacetext(context.space_data)

    def execute(self, context):
        context.space_data.text = bpy.data.texts.new("")
        return {'FINISHED'}


class TEXTENSION_OT_unlink(types.TextOperator):

    def execute(self, context):
        texts = bpy.data.texts
        index = texts[:].index(context.edit_text)
        texts.remove(context.edit_text)
        
        if texts:
            context.space_data.text = texts[min(index, len(texts) - 1)]
        return {'FINISHED'}


class TEXTENSION_OT_cut(types.TextOperator):

    def execute(self, context):
        text = context.edit_text
        if copy_selection(text) is None:
            return {'CANCELLED'}

        line, pos = cursor = text.cursor_start

        utils.push_undo(text)
        if not text.selected_text:
            # Line cut - when cursor is on the last line
            if len(text.lines) > 1 and line == len(text.lines) - 1:
                startcol = len(text.lines[line - 1].body)
                endcol = len(text.lines[line].body)
                text.cursor = line - 1, startcol, line, endcol
                cursor = line - 1, startcol

            # Line cut - regular
            elif line != len(text.lines) - 1:
                text.cursor = line, 0, line + 1, 0
                cursor = line, 0

            # Line cut - when there's only one line of text
            else:
                text.cursor = 0, 0, 0, len(text.current_line.body)
        text.write("")
        text.cursor = cursor
        utils.tag_modified(self)
        return {'FINISHED'}

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'X', 'PRESS', ctrl=1, repeat=True)
        kmi_new(cls, "Screen Editing", cls.bl_idname, 'X', 'PRESS',
                ctrl=1, note="HIDDEN", repeat=True)



class TEXTENSION_OT_copy(types.TextOperator):
    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'C', 'PRESS', ctrl=1)
        kmi_new(cls, "Screen Editing", cls.bl_idname, 'C', 'PRESS',
                ctrl=1, note="HIDDEN")

    def execute(self, context):
        if copy_selection(context.edit_text):
            return {'FINISHED'}
        return {'CANCELLED'}


class TEXTENSION_OT_paste(types.TextOperator):

    def execute(self, context):
        string = context.window_manager.clipboard
        if not string:
            return {'CANCELLED'}

        text = context.edit_text
        utils.push_undo(text)

        line, pos = text.cursor_start
        endline, endcol = line, pos

        tmp = string.splitlines()
        lines = len(tmp) - 1
        col_move = len(tmp[-1])
        endcol += col_move
        if lines > 0:
            endline += lines
            endcol = col_move

        if not text.selected_text:
            if string.count("\n") is 1 and string[-1:] is "\n":
                text.cursor = line, 0
                endline = line + 1
                endcol = pos

        text.write(string)
        text.cursor = endline, endcol
        utils.tag_modified(self)
        ensure_cursor_view(context, action="lazy")
        return {'FINISHED'}

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'V', 'PRESS', ctrl=1)
        kmi_new(cls, "Screen Editing", cls.bl_idname, 'V', 'PRESS',
                ctrl=1, note="HIDDEN")


class TEXTENSION_OT_undo(types.TextOperator):

    def execute(self, context):
        if types.TextUndo(context.edit_text).load_state(0):
            return {'FINISHED'}
        return {'CANCELLED'}

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text Generic", cls.bl_idname, 'Z', 'PRESS', repeat=1, ctrl=1)


class TEXTENSION_OT_redo(types.TextOperator):

    def execute(self, context):
        if types.TextUndo(context.edit_text).load_state(1):
            return {'FINISHED'}
        return {'CANCELLED'}

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text Generic", cls.bl_idname, 'Z', 'PRESS', repeat=1, ctrl=1, shift=1)




# def test_line_numbers(mrx: int, mry: int):
#     st = bpy.context.space_data
#     p = prefs()
#     if st.show_line_numbers and p.use_line_number_select:
#         if mrx <= st.runtime.lpad_px - st.runtime.cwidth_px:
#             if mrx >= 1 and mry <= bpy.context.region.height:
#                     return mry >= 0



class TEXTENSION_OT_insert(types.TextOperator):
    insert_hooks = []

    # @classmethod
    # def poll(cls, context):
    #     if types.TextOperator.poll(context):
    #         import ctypes
    #         # print(context.window.event.ascii)
    #         # print(*[(i, v) for i, v in enumerate(ctypes.string_at(ctypes.addressof(context.window.event), 100))])
    #         return True

    def altkey_combo_exists(self, context, event):
        # Don't try to recover from unlikely KeyErrors caused by corrupt
        # keyconfig. It's like stomping out a thermite reaction.
        km = context.window_manager.keyconfigs.active.keymaps["Text"]
        for kmi in km.keymap_items:
            if kmi.alt and kmi.type == event.type:
                return True
        return False

    def enclose_selection(self, text, bracket):
        opposite = dict(zip("([{\"\'", ")]}\"\'"))
        selected_text = text.selected_text

        assert selected_text and bracket in opposite

        tmp = f"{bracket}{selected_text}{opposite[bracket]}"
        curl, curc, sell, selc = text.cursor

        # Inline: increment both columns
        # Multi-line: increment the top column
        selc += 1 - (curl < sell)
        curc += 1 - (curl > sell)

        text.write(tmp)
        text.cursor = curl, curc, sell, selc

    def invoke(self, context, event):
        typed_char = event.unicode

        # Pass on when mouse hovers the line numbers margin.
        if test_line_numbers(TEXTENSION_OT_hit_test.get_data(context)):
            return {'PASS_THROUGH'}

        # Pass on delete and backspace keys.
        if event.type in {'DEL', 'BACK_SPACE'}:
            return {'PASS_THROUGH'}

        # Pass on zero-length text inputs.
        if not bool(typed_char):
            return {'PASS_THROUGH'}

        # Pass on alt key combinations by other operators.
        if event.alt and self.altkey_combo_exists(context, event):
            return {'PASS_THROUGH'}

        text = context.edit_text
        utils.push_undo(text)
        body = text.cursor_start_line.body
        curc = text.current_character
        try:
            next_char = body[curc]
        except IndexError:
            # Cursor was at EOF
            next_char = ""

        line, col = text.cursor_sorted[:2]


        if text.selected_text and typed_char in {'"', "'", '(', '[', '{'}:
            self.enclose_selection(text, typed_char)

        else:
            closed = {")", "]", "}"}
            pairs  = {"(": ")", "[": "]", "{": "}"}

            # If the character is a quote...
            if typed_char in {'"', "'"}:

                # .. then check whether we're inside a string
                # to figure out if we should add a closing quote.
                in_string = False
                for pair in _iter_expand_tokens(text.as_string()):
                    if (line, col) in pair:
                        if pair.type in {"STRING", "MULTILINE_STRING"}:
                            in_string = True
                            break

                # If the character forms a multi-line bracket, close.
                if (ml := body[curc - 2: curc] + typed_char) in {'"""', "'''"} and \
                    next_char != typed_char:
                        typed_char += ml

                # If the character forms a pair, advance.
                elif next_char == typed_char:
                    typed_char = ""

                # If we're not inside a string or on a word boundary, close.
                elif next_char in {" ", ""} | closed and not in_string:
                    typed_char += typed_char

            # If the character opens a bracket on a word boundary, close.
            elif typed_char in pairs and next_char in {" ", ""} | closed:
                typed_char += pairs[typed_char]

            # If the character is closed, advance.
            elif typed_char in closed and next_char == typed_char:
                typed_char = ""

            text.write(typed_char)
            text.select_set(line, col + 1, line, col + 1)
            utils.tag_modified(self)

            for func in self.insert_hooks:
                func()
        ensure_cursor_view(context, action="lazy")
        return {'FINISHED'}

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'TEXTINPUT', 'ANY', repeat=True,
                note="HIDDEN")




def find_word_boundary(string, reverse=False, strict=False) -> int:
    """
    Given a string, find the first index of a word boundary.
    When strict is True, spaces and punctuation are treated as a boundary.
    """
    if reverse:
        string = string[::-1]
    pattern = r"\s{2,}|\s?\w+|\s?[^a-zA-Z0-9_ ]*"
    if strict:
        pattern = r"\s{2,}|\w+|[^a-zA-Z0-9_]"

    ret = re.match(pattern, string)
    if ret:
        return ret.span()[1]
    return 0


class TEXTENSION_OT_delete(types.TextOperator):
    delete_hooks = []

    type: bpy.props.EnumProperty(
        items=(('NEXT_CHARACTER', "Next Character", ""),
               ('PREVIOUS_CHARACTER', "Previous Character", ""),
               ('NEXT_WORD', "Next Word", ""),
               ('PREVIOUS_WORD', "Previous Word", "")),
        default='NEXT_CHARACTER',
    )

    def delete(self, text, cursor_post, cursor_sel=None):
        # cursor_post: Where the cursor is set after deleting
        # cursor_sel:  What the cursor extends (selects) before deleting
        utils.push_undo(text)
        if cursor_sel is not None:
            text.cursor = (*cursor_sel, *cursor_post)
        text.write("")
        text.cursor = cursor_post
        utils.tag_modified(self)
        
        for func in self.delete_hooks:
            func()

        return {'FINISHED'}

    def execute(self, context):
        text = context.edit_text
        selected_text = text.selected_text
        endline, endcol = line, col = text.cursor_start

        if selected_text:
            return self.delete(text, (line, col))

        body = text.current_line.body
        if "PREVIOUS" in self.type:
            if line == col == 0:
                return {'CANCELLED'}
            endcol -= 1
            wrap = col is 0
            if "WORD" in self.type:
                endcol = col - find_word_boundary(body[:col], reverse=True)
                wrap = col == endcol

            else:
                # When the cursor is between empty bracket pairs, remove both
                pairs = dict(zip("([{\"\'", ")]}\"\'"))
                if pairs.get(body[col - 1: col]) == body[col: col + 1]:
                    col += 1

                # Deal with leading indentation (spaces only)
                ret = re.search(r"^([ \t]+)$", body[:col])
                if ret:
                    spaces = len(ret.group())
                    width = context.space_data.tab_width
                    if spaces >= width and text.indentation == 'SPACES':
                        endcol = col - ((spaces % width) or width)

            if wrap:
                endline -= 1
                endcol = len(text.lines[endline].body)

        elif "NEXT" in self.type:
            is_eol = col == len(body)
            if line == len(text.lines) - 1 and is_eol:
                return {'CANCELLED'}

            col += 1
            wrap = is_eol
            if "WORD" in self.type:
                col = endcol + find_word_boundary(body[endcol:], reverse=False)
                wrap = col == endcol

            if wrap:
                line += 1
                col = 0

        return self.delete(text, (endline, endcol), (line, col))

    @classmethod
    def register_keymaps(cls):
        kmi_args(cls, "Text", cls.bl_idname, 'PRESS')

        kmi_new('DEL', note="Delete Next Character", repeat=1)
        kmi_op_args(type='NEXT_CHARACTER')
        
        kmi_new('BACK_SPACE', note="Delete Previous Character", repeat=1)
        kmi_op_args(type='PREVIOUS_CHARACTER')

        kmi_new('DEL', note="Delete Next Word", ctrl=1, repeat=1)
        kmi_op_args(type='NEXT_WORD')

        kmi_new('BACK_SPACE', note="Delete Previous Word", ctrl=1, repeat=1)
        kmi_op_args(type='PREVIOUS_WORD')


class TEXTENSION_OT_drag_select(types.TextOperator):
    def modal(self, context, event):
        text = context.edit_text
        if event.type == 'MOUSEMOVE':
            mrx, mry = event.mouse_region_x, event.mouse_region_y
            _call('TEXT_OT_cursor_set', {}, {'x': mrx + 2, 'y': mry})
            _call('TEXTENSION_OT_select_word', {}, {}, 'INVOKE_DEFAULT')

            icurl, icurc, iselc = self.init_range
            curl, curc, sell, selc = text.cursor
            if (curc < icurc and curl == icurl) or curl < icurl:
                icurc, selc = iselc, curc
            text.select_set(icurl, icurc, sell, selc)

        elif event.type in {'LEFTMOUSE', 'RIGHTMOUSE', 'ESC'}:
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        # Skip if cursor is scrollbar region.
        if in_scroll(event, context.region):
            return {'PASS_THROUGH'}
        # Set initial selection range.
        _call('TEXTENSION_OT_select_word', {}, {})
        icurl, icurc, isell, iselc = context.edit_text.cursor
        self.init_range = icurl, icurc, iselc
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


class TEXTENSION_OT_unindent(types.TextOperator):
    def execute(self, context):
        text = context.edit_text
        ltop, ctop, lbot, cbot = text.cursor_sorted
        tab_width = context.space_data.tab_width
        
        set_indents = []
        lengths = []

        # Defer the unindenting until we know the lines *can* be unindented.
        for line in text.lines[ltop: lbot + 1]:
            indent = line.indent
            if not indent:
                lengths.append(0)
                continue

            # Remainder if the leading indent is unaligned
            i = indent % tab_width
            set_indents.append((line, line.indent_level - (not i)))
            lengths.append(i or tab_width)

        if not set_indents:
            return {'CANCELLED'}

        utils.push_undo(text)
        for line, level in set_indents:
            line.indent_level = level

        # Offset the cursor ends
        ctop -= lengths[0]
        cbot -= lengths[-1]

        if text.cursor_flipped:
            ltop, lbot = lbot, ltop
            ctop, cbot = cbot, ctop
        text.cursor = ltop, max(0, ctop), lbot, max(0, cbot)
        return {'FINISHED'}

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'TAB', 'PRESS', shift=True, repeat=True)


class TEXTENSION_OT_indent(types.TextOperator):
    def execute(self, context):
        text = context.edit_text
        utils.push_undo(text)
        tab_width = context.space_data.tab_width
        ltop, ctop, lbot, cbot = text.cursor_sorted

        length = 1
        writestr = "\t"
        if ltop == lbot:
            if text.indentation == 'SPACES':
                length = tab_width - (ctop % tab_width)
                writestr = " " * length
            text.write(writestr)
            text.cursor = ltop, ctop + length

        else:
            flipped = text.cursor_flipped
            line_strings = []
            lengths = []

            # Calculate appropriate indentation for each line
            for line in text.lines[ltop:lbot + 1]:
                body = line.body

                i = 0
                while body[i:i + 1] == " ":
                    i += 1

                if text.indentation == 'SPACES':
                    length = tab_width - (i % tab_width)
                    if length < tab_width:
                        length += tab_width
                    writestr = " " * length
                else:
                    length = 1
                    writestr = "\t"

                line_strings.append(f"{writestr}{body}")
                lengths.append(length)

            # Write the lines with new indentation
            text.cursor = ltop, 0, lbot, -1
            text.write("\n".join(line_strings))

            if flipped:
                ltop, lbot = lbot, ltop
                ctop, cbot = cbot, ctop
            text.cursor = ltop, ctop + lengths[0], lbot, cbot + lengths[-1]
        utils.tag_modified(self)
        return {'FINISHED'}

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'TAB', 'PRESS', repeat=True)


indent_kw = re.compile(r"^[ \t]*?\b(?:def|if|elif|else|try|except|"
                       r"finally|for|class|while|with|finally)\b")

dedent_kw = re.compile(r"^[ \t]*?\b(?:pass|break|continue|raise|return)\b")


class TEXTENSION_OT_line_break(types.TextOperator):
    hooks = []
    type: bpy.props.EnumProperty(items=(('DEFAULT', "Default", ""),
                                        ('JUMP', "Jump", ""),
                                        ('SMART', "Smart", "")),
                                 default='DEFAULT')

    def invoke(self, context, event):
        text = context.edit_text
        utils.push_undo(text)

        line, pos = text.cursor_start
        start_line = text.cursor_start_line
        full_body = start_line.body
        if self.type in {'JUMP', 'SMART'}:
            text.cursor = line, pos = line, len(start_line.body)
        indent_level = start_line.indent_level


        pre_cursor_line = full_body[:pos]
        post_cursor_line = full_body[pos:]

        prefix = ""

        indent = text.indent_string

        # Match the text up to the cursor for any open brackets.
        open_bracket = re.match(r"^.*?([([{])\s*$", pre_cursor_line)
        pairs  = {"(": ")", "[": "]", "{": "}"}
        postfix = ""

        # Test if the cursor is directly between a bracket pair.
        # If successful, append a newline + indentation.
        if open_bracket is not None:
            close_bracket = re.match(r"^\s?([)\]}])", post_cursor_line)
            if close_bracket is not None:
                if pairs[open_bracket.groups()[-1]] is close_bracket.groups()[0]:
                    postfix = f"\n{indent * indent_level}"

        # Match against keywords that would add an indent level.
        if (indent_kw.match(pre_cursor_line) or open_bracket) is not None:

            # Add missing parens or colon.
            if self.type == 'SMART':
                l = text.select_end_line
                tmp = l.body.strip()
                
                if not re.match(r"^.*:$", tmp) or re.match(r"^else$", tmp):
                    if tmp.startswith("def ") and not any(c in tmp for c in "()"):
                        prefix += "()"
                    prefix += ":"
            indent_level += 1
        
        # Dedent on keywords like pass, return, break, etc.
        elif dedent_kw.match(pre_cursor_line):
            indent_level -= 1

        indent *= indent_level

        text.write(f"{prefix}\n{indent}{postfix}")
        text.cursor = line + 1, len(indent)
        utils.tag_modified(self)
        for func in self.hooks:
            func()
        return {'FINISHED'}

    @classmethod
    def register_keymaps(cls):
        kmi_args(cls, "Text", cls.bl_idname, 'PRESS')
        kmi_new('RET').type = 'DEFAULT'
        kmi_new('RET', ctrl=1, note="Line Break Jump").type = 'JUMP'
        kmi_new('RET', shift=1, note="Line Break Context").type = 'SMART'
        kmi_new('NUMPAD_ENTER', note="HIDDEN")
        kmi_new('NUMPAD_ENTER', ctrl=1, note="HIDDEN").type = 'JUMP'


# TODO: Needs to support tabs.
class TEXTENSION_OT_move_toggle(types.TextOperator):
    """Toggle cursor position between line start and indent"""

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'HOME', 'PRESS')
        kmi_new(cls, "Text", cls.bl_idname, 'HOME', 'PRESS', shift=1,
                note="Move Toggle Select")

    def invoke(self, context, event):
        text = context.edit_text
        body = text.select_end_line.body
        indent = text.select_end_line.indent

        pos = bool(text.selc != indent and body.strip()) and indent
        text.cursor_set(text.sell, character=pos, select=event.shift)
        return {'FINISHED'}


# def _iter_expand_tokens(txt):
#     """This is a simplified strings and brackets parser that generates a set
#     of sequential and nested tokens for the bracket expansion operator. It
#     generally takes a string of python source, although any language that uses
#     same string and bracket conventions will work.
#     """
#     s = ml = False
#     lent = len(txt)
#     pos = 0
#     stack = []
#     unmatched = []

#     class Token:
#         tokens = []
#         _text = txt
#         end = None

#         def __init__(self, type, line, linepos):
#             self.children = set()
#             self.parent = None
#             self.tokens.append(self)
#             self.type = type
#             self.start = line, linepos
#             self.error = False

#             if stack:
#                 stack[-1].set_child(self)

#         def set_end(self, line, linepos):
#             self.end = line, linepos

#         def set_child(self, token):
#             self.children.add(token)
#             token.parent = self

#         def __contains__(self, cursor):
#             start = self.start
#             end = self.end or (0, 0)

#             if self.type in brackets.values() or self.type == "STRING":
#                 end = end[0], end[1] + 1

#             elif self.type == "MULTILINE_STRING":
#                 start = start[0], start[1] - 2
#                 end = end[0], end[1] + 3

#             return start <= cursor[:2] <= cursor[2:] < end

#         def discard(self):
#             if self in self.tokens:
#                 self.tokens.remove(self)

#             for c in self.children.copy():
#                 c.parent = self.parent
#             self.children.clear()

#             if self.parent:
#                 self.parent.children.discard(self)
#                 self.parent = None

#             if self in stack:
#                 stack.remove(self)

#     token_ml = None

#     brackets = {
#         "(": "PARENS", ")": "PARENS",
#         "[": "SQUARE", "]": "SQUARE",
#         "{": "CURLY", "}": "CURLY"
#     }
#     line = 0
#     end = 0

#     lines = txt.splitlines()

#     # Brackets inside strings are parsed separately
#     def parse_brackets(stoken):
#         l1, c1 = stoken.start
#         l2, c2 = stoken.end
#         sstack = []
#         for l1, line in enumerate(lines[l1:l2 + 1], l1):
#             for c1, c in enumerate(line[c1:c2], c1):
#                 if c in {"(", "[", "{"}:
#                     sstack.append(Token(brackets[c], l1, c1 + 1))
#                 # elif c in {")", "]", "}"} and sstack:
#                 #     if sstack[-1].type == brackets[c] and sstack[-1].s:
#                 #         sstack.pop().set_end(l1, c1)
#                 elif c in {")", "]", "}"} and sstack:
#                     if sstack[-1].type == brackets[c] and s:
#                         sstack.pop().set_end(l1, c1)

#         # Remove tokens that form uneven brackets (tokens with no end position)
#         for t in sstack:
#             if t.end is None:
#                 t.discard()

#     # Main tokenization
#     while pos < lent:
#         c = txt[pos]

#         # Comments (skip tokenize for now)
#         if c is "#" and not s and not ml:
#             # c_node = Token("COMMENT", line, pos - end)
#             try:
#                 # Move pos to the end of the line excluding the newline.
#                 pos = txt.index("\n", pos) - 1
#             except ValueError:
#                 # This happens when we're on the last line
#                 pos = lent
#             # c_node.set_end(line, pos - end)

#         elif c in {"\"", "\'"}:
#             sub = txt[pos:pos + 3]
#             if sub in {'"""', "'''"} and not s:
#                 if not ml:
#                     token_ml = Token("MULTILINE_STRING", line, pos - end + 3)
#                     ml = sub
#                     pos += 2
#                 elif sub == ml:
#                     token_ml.set_end(line, pos - end)
#                     ml = False
#                     pos += 2
#             elif not s:
#                 token_string = Token("STRING", line, pos - end + 1)
#                 s = c
#             elif s is c and txt[max(0, pos - 1)] is not "\\":
#                 token_string.set_end(line, pos - end)
#                 parse_brackets(token_string)
#                 s = False

#         # Non-string brackets
#         elif c in {"(", "[", "{"} and not s:
#             stack.append(Token(brackets[c], line, pos - end + 1))
#         elif c in {")", "]", "}"} and stack and not s:
#             if stack[-1].type == brackets[c]:
#                 stack.pop().set_end(line, pos - end)
#             else:
#                 unmatched.append((c, line, pos - end))

#         # Move to next line
#         elif c is "\n":
#             line += 1
#             end = pos + 1
#         pos += 1

#     # Although unmatched brackets are syntax errors, this attempts to support
#     # selecting brackets with bad content as long as they form a pair.
#     if unmatched and stack:
#         for c, line, col in unmatched:
#             for t in stack:
#                 if t.type == brackets[c]:
#                     if t.start < (line, col):
#                         t.end = line, col
#                         # t.error = True

#     # More of the same as above. Any unmatched brackets will be given an end
#     # if there are any bracket tokens later in the source.
#     for t in stack:
#         if t.end is None:
#             new_end = new_end_prev = int(1e9), int(1e9)
#             for t_ in Token.tokens:
#                 if t_.type == t.type and t_.end is not None:
#                     if t_.end >= t.start and t_.end <= new_end:
#                         new_end = t_.end
#             if new_end < new_end_prev:
#                 t.end = new_end
#                 t.error = True

#     bpy.t = Token.tokens
#     return Token.tokens


# @measure
def _iter_expand_tokens(txt: str):
    """
    Strings and brackets parser that generates a list of Pairs.
    """
    s = ml = False
    text_len = len(txt)
    stack = []
    unmatched = []
    tokens = []

    class Pair:
        error = False
        end = None
        parent = None

        def __init__(self, type, line, column, pos):
            self.children = set()
            self.type = type
            self.start = line, column
            self.pos = pos
            if stack:
                self.parent = stack[-1]
                self.parent.children.add(self)
            tokens.append(self)

        def __contains__(self, cursor):
            if len(cursor) == 2:
                # self.end can be None, in which case range is EOF.
                return self.start <= cursor <= (self.end or cursor)

            elif len(cursor) == 4:
                cursta, curend = sorted((cursor[:2], cursor[2:]))
                pairsta = self.start
                pairend = self.end or curend
                if self.type == "MULTILINE_STRING":
                    pairsta = pairsta[0], pairsta[1] - 2
                    pairend = pairend[0], pairend[1] + 3
                return pairsta <= cursta <= curend <= pairend
            else:
                raise ValueError("Expected sequence of 2 or 4 ints")

        def __repr__(self):
            return f"Pair {self.type}: {self.start} to {self.end}"
    pos = line = end = 0
    token_ml = None
    escape = False
    brackets = {"(": "PARENS", ")": "PARENS",
                "[": "SQUARE", "]": "SQUARE",
                "{": "CURLY",  "}": "CURLY"}

    # Remove brackets with no end position
    def clean(substack):
        for t in substack:
            if t.end is not None:
                continue
            if t in tokens:
                tokens.remove(t)
            while t.children:
                t.children.pop().parent = t.parent
            if t.parent:
                t.parent.children.discard(t)
                t.parent = None
            if t in stack:
                stack.remove(t)

    def parse_substring(string: str, start, end, line):
        stack: list[Pair] = []
        slen = len(string)
        offset = 0
        while offset < slen:
            c = string[offset]
            pos = start + offset
            if c in {"(", "[", "{"}:
                stack.append(Pair(brackets[c], line, pos - end + 1, pos + 1))
            elif c in {")", "]", "}"}:
                try:
                    if stack[-1].type == brackets[c]:
                        tmp = stack.pop()
                        tmp.end = line, pos - end
                except IndexError: pass
            # Strings spanning multiple lines are syntax errors,
            # but handle them anyway, because we can.
            elif c is "\n":
                line += 1
                end = pos + 1
            offset += 1
        clean(stack)


    # Tokenize
    while pos < text_len:
        c = txt[pos]

        if c is "#" and not s and not ml:
            start = pos
            try:  # Move pos to the last column excluding the newline character.
                pos = txt.index("\n", pos) - 1
            except ValueError:  # This happens when we're on the last line
                pos = text_len
            parse_substring(txt[start:pos + 1], start, end, line)

        elif c in {"\"", "\'"}:
            sub = txt[pos:pos + 3]
            if sub in {'"""', "'''"}:
                if not ml:
                    token_ml = Pair("MULTILINE_STRING", line, pos - end + 3, pos + 3)
                    ml = sub
                    pos += 2

                # Encountered ending multi-line quote
                elif sub == ml:
                    token_ml.end = line, pos - end
                    ml = False
                    pos += 2
                    # If s was active while leaving multi-line quote,
                    # then it probably wasn't a string token.
                    if s:
                        token_string.error = True
                        s = False
            # elif not ml:
            elif not s:
                token_string = Pair("STRING", line, pos - end + 1, pos + 1)
                s = c

            elif s is c and not escape:
                token_string.end = line, pos - end
                sline, spos = token_string.start
                parse_substring(txt[token_string.pos:pos], spos, 0, sline)
                s = False

        # Non-string brackets
        # elif c in {"(", "[", "{"} and not s:
        elif c in {"(", "[", "{"}:
            stack.append(Pair(brackets[c], line, pos - end + 1, pos + 1))
        # elif c in {")", "]", "}"} and stack and not s:
        elif c in {")", "]", "}"} and stack:
            if stack[-1].type == brackets[c]:
                tmp = stack.pop()
                tmp.end = line, pos - end
            else:
                unmatched.append((c, line, pos - end, pos))

        # Move to next line
        elif c is "\n":
            line += 1
            end = pos + 1
        if s:
            escape = not escape if c is "\\" else False
        pos += 1

    # Although unmatched brackets are syntax errors, this attempts to support
    # selecting brackets with bad content as long as they form a pair.
    if unmatched and stack:
        for c, line, col, raw in unmatched:
            for t in stack:
                if t.type == brackets[c]:
                    if t.start < (line, col):
                        t.end = line, col
                        # t.error = True

    # More of the same as above. Any unmatched brackets will be given an end
    # if there are any bracket tokens later in the source.
    for t in stack:
        if t.end is None:
            new_end = new_end_prev = int(1e9), int(1e9)
            for t_ in tokens:
                if t_.type == t.type and t_.end is not None:
                    if t_.end >= t.start and t_.end <= new_end:
                        new_end = t_.end
            if new_end < new_end_prev:
                t.end = new_end
                t.error = True

    while True:
        for index, token in enumerate(tokens):
            if token.error or token.end is None:
                del tokens[index]
                break
        else:
            break
    return tokens


class TEXTENSION_OT_expand_to_brackets(types.TextOperator):
    """Expand selection to closest brackets"""

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'A', 'PRESS', alt=1)

    def execute(self, context):
        text = context.edit_text
        cursor = text.cursor_sorted
        # Loop over the tokens and see if the cursor is contained within it.
        # Since a parent token (brackets within brackets) may contain the
        # cursor, keep looping until the refined, inner-most token is found.

        hit = None
        hits = []
        for t in _iter_expand_tokens(text.as_string()):
            if cursor in t and hit not in t.children:
                if not t.error:
                    hit = t
                else:
                    hits.append(t)
        if hit is None and hits:
            hit = hits[-1]

        # Expansion logic
        if hit is not None:
            l1, c1, l2, c2 = cursor
            nl1, nc1 = hit.start
            nl2, nc2 = hit.end

            if l1 == nl1 and l2 == nl2:
                # When current selection matches the hit range, expand by 1.
                if (l1, c1) == (nl1, nc1) and (l2, c2) == (nl2, nc2):
                    nc1 -= 1
                    nc2 += 1

                # When already expanded and distance between each ends matches,
                # expand by 1 relative to the existing selection.
                elif (l1 != l2 or c1 != c2) and \
                      nc1 - c1 == c2 - nc2  and \
                     (c1 <= nc1 and c2 >= nc2):
                        nc1 = c1 - 1
                        nc2 = c2 + 1
            text.cursor = nl1, nc1, nl2, nc2

        return {'CANCELLED'}


class TEXTENSION_OT_expand_to_path(types.TextOperator):
    """Expand selection to data path"""
    # bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'W', 'PRESS', alt=1)

    def execute(self, context):
        text = context.edit_text
        curl, curc, sell, selc = text.cursor_sorted

        # Multi-line is not supported.
        if curl != sell:
            return {'CANCELLED'}

        # Lexical separators excluding period, which is part of a data path.
        separator = {*" !\"#$%&\'()*+,-/:;<=>?@[\\]^`{|}~"}

        line = text.lines[sell].body
        strings = [line[selc:], line[:curc][::-1]]
        boundary = []

        while strings:
            index = 0
            for c in strings.pop():
                if c in separator:
                    break
                index += 1
            boundary.append(index)

        assert len(boundary) == 2

        text.curc -= boundary[0]
        text.selc += boundary[1]
        return {'FINISHED'}


class TEXTENSION_OT_search_with_selection(types.TextOperator):
    """Open search with selected text"""

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text Generic", cls.bl_idname, 'F', 'PRESS', ctrl=1)

    def execute(self, context):
        from .utils import tabs_to_spaces

        st = context.space_data
        text = st.text
        string = text.selected_text
        # Replace characters like \n \t.
        string = "".join(c if c.isprintable() else " " for c in
                         tabs_to_spaces(text.selected_text, st.tab_width))

        if string:
            st.use_find_wrap = True
            st.find_text = string

        bpy.ops.text.start_find('INVOKE_DEFAULT')
        return {'FINISHED'}




class TEXTENSION_OT_timeit_template(types.TextOperator):
    """Add a simple timeit template"""

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
        return is_spacetext(context.space_data)

    def execute(self, context):
        text = context.blend_data.texts.new("timeit.py")
        text.write(self.template.replace(" " * 8, ""))
        context.space_data.text = text
        context.area.tag_redraw()
        return {'FINISHED'}


class TEXTENSION_OT_move_cursor(types.TextOperator):
    type: bpy.props.EnumProperty(
        items=tuple((p.identifier, p.name, p.description)
            for p in _bpy.ops.get_rna_type("TEXT_OT_move").properties["type"].enum_items))

    def execute(self, context):
        dna = context.space_data.internal
        top = dna.top
        bpy.ops.text.move(type=self.type)
        dna.top = top
        ensure_cursor_view(context, action='lazy', smooth=False)
        return {'CANCELLED'}

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'LEFT_ARROW', 'PRESS', repeat=True).type = 'PREVIOUS_CHARACTER'
        kmi_new(cls, "Text", cls.bl_idname, 'RIGHT_ARROW', 'PRESS', repeat=True).type = 'NEXT_CHARACTER'
        kmi_new(cls, "Text", cls.bl_idname, 'UP_ARROW', 'PRESS', repeat=True).type = 'PREVIOUS_LINE'
        kmi_new(cls, "Text", cls.bl_idname, 'DOWN_ARROW', 'PRESS', repeat=True).type = 'NEXT_LINE'


class TEXTENSION_OT_scroll(types.TextOperator):
    """Smooth scroll Über operator"""

    @classmethod
    def register_keymaps(cls):
        kmi_args(cls, "Text", cls.bl_idname, 'PRESS')

        kmi_new('HOME', ctrl=1, note="Scroll to Top").type = 'TOP'
        kmi_new('HOME', ctrl=1, shift=1, note="Select to Top")
        kmi_op_args(type='TOP', select=True)

        kmi_new('END', ctrl=1, note="Scroll to Bottom").type = 'BOTTOM'
        kmi_new('END', ctrl=1, shift=1, note="Select to Bottom")
        kmi_op_args(type='BOTTOM', select=True)

        kmi_new('PAGE_UP', note="Scroll Page Up").type = 'PAGEUP'
        kmi_new('PAGE_UP', shift=1, note="Select Page Up")
        kmi_op_args(type='PAGEUP', select=True)

        kmi_new('PAGE_DOWN', note="Scroll Page Down").type = 'PAGEDN'
        kmi_new('PAGE_DOWN', shift=1, note="Select Page Down")
        kmi_op_args(type='PAGEDN', select=True)

        kmi_new("UP_ARROW", ctrl=1, note="Nudge Scroll Up")
        kmi_op_args(type='NUDGE', lines=-1)

        kmi_new("DOWN_ARROW", ctrl=1, note="Nudge Scroll Down")
        kmi_op_args(type='NUDGE', lines=1)

    _items = (('PAGEUP', "Page Up", "Scroll up one page"),
              ('PAGEDN', "Page Down", "Scroll down one page"),
              ('TOP', "Top", "Scroll to top"),
              ('BOTTOM', "Bottom", "Scroll to bottom"),
              ('CURSOR', "To Cursor", "Scroll to cursor"),
              ('JUMP', "Jump", "Jump to line"),
              ('NUDGE', "Nudge", "Move the view without setting cursor"))

    type: bpy.props.EnumProperty(
        default='PAGEDN', items=_items, options={'SKIP_SAVE'})
    lines: bpy.props.IntProperty(default=1, options={'SKIP_SAVE'})
    jump: bpy.props.IntProperty(default=0, options={'SKIP_SAVE'})
    char: bpy.props.IntProperty(default=0, options={'SKIP_SAVE'})
    history: bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'})
    use_smooth: bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'})
    select: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    move_cursor: bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'})

    def execute(self, context):
        text = context.edit_text
        st = context.space_data
        rw = context.area.regions[-1].width
        # Make visible lines divisible by 2 so cursor centering is fixed.
        viewl = context.region.height // st.runtime.lheight_px
        view_half = viewl // 2

        top = st.top
        sell, selc = text.cursor_position
        type = self.type

        if type == 'NUDGE':
            return scroll(lines=prefs().nudge_scroll_lines,
                          direction='DOWN' if self.lines > 0 else 'UP')

        elif type in {'PAGEUP', 'PAGEDN'}:
            offset = st.show_word_wrap
            offl = offl_by_col(st, sell, selc)
            numlines = -viewl + offl
            top_offl = offl - view_half + offset

            if type == 'PAGEDN':
                offset *= -1
                numlines = viewl + offl
                top_offl = offl + viewl + view_half + offset

            sell_dst, selc_dst = skip_lines(context, numlines, sell, selc)
            off_top = offl_get(st, rw, end=sell) + top_offl
            scroll_dst = clamp(off_top + sell - viewl)

        elif type == 'TOP':
            sell_dst = selc_dst = 0
            scroll_dst = -100  # Hack to deal with rounding errors.

        elif type == 'BOTTOM':
            sell_dst = len(text.lines) - 1
            selc_dst = 0
            scroll_dst = max(0, st.drawcache.total_lines - (st.visible_lines // 2))

        elif type in {'CURSOR', 'JUMP'}:
            sell_dst = sell
            selc_dst = selc

            if type == 'JUMP':
                sell_dst = self.jump
                selc_dst = 0

            off_top = offl_get(st, rw, end=sell_dst)
            scroll_dst = clamp(sell_dst + off_top - view_half)

        # Set cursor.
        if self.move_cursor:
            if type in {'PAGEUP', 'PAGEDN', 'TOP', 'BOTTOM', 'JUMP'}:
                text.cursor_set(sell_dst, character=selc_dst,
                                select=self.select)

        # Allow instant scrolling. Prefs setting?
        if not self.use_smooth:
            st.top = scroll_dst

        else:
            # If st.top is higher than destination, flip direction.
            if scroll_dst > top:
                direction = 'DOWN'
            else:
                direction = 'UP'

            lines = abs(scroll_dst - top)
            # Scroll when cursor is outside of view.
            if top not in range(clamp(scroll_dst - view_half),
                                clamp(scroll_dst + view_half)):

                # TODO: Make lines argument relative and drop direction.
                if lines:
                    scroll(lines=lines, direction=direction)

        # Append new cursor position to history.
        if self.history:
            textman[text].cursor.add()
        return {'FINISHED'}


class TEXTENSION_OT_find(types.TextOperator):
    @classmethod
    def register_keymaps(cls):
        kmi_args(cls, "Text", cls.bl_idname, 'PRESS', alt=1)
        kmi_new('F', note="Find Next").direction = 'NEXT'
        kmi_new('D', note="Find Previous").direction = 'PREV'

    case_sensitive: bpy.props.BoolProperty(default=False)
    direction: bpy.props.EnumProperty(
        items=(('NEXT', "Next", ""), ('PREV', "Previous", "")))

    def execute(self, context):
        t = context.edit_text
        string = t.selected_text
        _lines = t.lines

        if not string:
            return {'CANCELLED'}
        if not self.case_sensitive:
            string = string.lower()

        strlen = len(string)
        reverse = self.direction != 'NEXT'

        if reverse:
            lines = reversed((*enumerate(_lines[:t.curl + 1]),))
            find = str.rfind
        else:
            lines = enumerate(_lines[t.curl:], t.curl)
            find = str.find

        def _find(lines, reverse=reverse):
            for idx, line in lines:
                body = line.body

                if not self.case_sensitive:
                    body = line.body.lower()

                if string in body:
                    head = 0 if reverse or idx > t.curl else t.selc
                    tail = t.curc if reverse and idx == t.curl else len(body)
                    match = find(body, string, head, tail)
                    if match != -1:
                        t.cursor = idx, match, idx, match + strlen
                        textman[t].cursor.add()
                        return idx

        ret = _find(lines)
        if ret is None:
            if reverse:
                lines = reversed((*enumerate(_lines[t.curl:], t.curl),))
            else:
                lines = enumerate(_lines[:t.curl])
                find = str.find
            ret = _find(lines, reverse=not reverse)

        # Scroll to cursor.
        if ret is not None:
            st = context.space_data
            if ret not in range(st.top, st.top + st.visible_lines - 2):
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
        endl = bpy.context.edit_text.sell
        TEXTENSION_OT_goto.block = True
        TEXTENSION_OT_goto.kmi_prop().line = str(endl + 1)
        TEXTENSION_OT_goto.block = False


class TEXTENSION_OT_goto(types.TextOperator):
    """A more streamlined goto operator"""
    @classmethod
    def kmi_prop(cls):
        return cls._keymaps[0][1].properties

    def goto(self, context):
        if not getattr(__class__, "block", False):
            lenl = len(context.edit_text.lines)
            line = clamp(min(lenl, int(__class__.kmi_prop().line)), 1) - 1
            bpy.ops.textension.scroll(type='JUMP', jump=line)

    line: bpy.props.StringProperty(options={'HIDDEN'}, update=goto)

    def invoke(self, context, event):
        # Create popup somewhat centered.
        center_x = context.area.x + (context.area.width // 2)
        center_y = context.area.y - 20 + (context.area.height // 2)

        context.window.cursor_warp(center_x, center_y)
        bpy.ops.wm.call_panel(name="TEXTENSION_PT_goto", keep_open=False)
        context.window.cursor_warp(event.mouse_x, event.mouse_y)
        return {'FINISHED'}

    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text Generic", cls.bl_idname, 'J', 'PRESS', ctrl=1)


class TEXTENSION_OT_cursor_history(types.TextOperator):
    """Step through cursor history"""
    @classmethod
    def register_keymaps(cls):
        kmi_args(cls, "Text", cls.bl_idname, 'PRESS')
        kmi_new('BUTTON4MOUSE', note="Cursor History Back").dir_ = 'BACK'
        kmi_new('BUTTON5MOUSE', note="Cursor History Forward").dir_ = 'FORWARD'

    _items = (("FORWARD", "Forward", ""), ("BACK", "Back", ""))
    dir_: bpy.props.EnumProperty(items=_items)

    def execute(self, context):
        if cursor_history_step(context, self.dir_):
            return {'FINISHED'}
        return {'CANCELLED'}


class LeftClicks:
    """LeftClicks simply tracks left mouse button clicks. This is a separate
    class to avoid storing things on the operator class, which may lead to
    obscure id_property memory leaks.

    Usage:
    clicks = LeftClicks.track(context)

    The return value is the amount of consecutive clicks within the same area,
    and within a pre-defined double-click time limit. A threshold of 2 pixels
    (before factoring in DPI scaling) allows a bit of error margin.

    After 3 consecutive clicks have been made, the counter resets.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            self = cls._instance = super().__new__(cls)
            self.co = (-10000, -10000)
            self.clicks = self.click_time = 0
        return cls._instance

    @classmethod
    def track(cls, context):
        from time import monotonic
        newx, newy = mouse_region(context)
        instance = cls._instance or cls()
        click_time = monotonic()

        prevx, prevy = instance.co
        thresh = int(system.wu * 2 * 0.05)
        timeout = context.preferences.inputs.mouse_double_click_time

        # When the new coord is close to previous, and within the time limit,
        # count towards clicks. After the 3rd click, reset to 1.
        if prevx - thresh <= newx <= prevx + thresh and \
           prevy - thresh <= newy <= prevy + thresh and \
           click_time - (timeout / 1000) <= instance.click_time:
                result = (instance.clicks + 1) % 4 or 1
        else:
            result = 1
        # Store the new coords, clicks and click time
        instance.co = newx, newy
        instance.clicks = result
        instance.click_time = click_time
        return instance.clicks


def mouse_region(context: bpy.types.Context) -> tuple[int, int]:
    """Return a tuple of mouse coordinates in region space.
    
    This is a convenient way of getting mouse coords outside of events.
    """
    region = context.region
    x, y = context.window.mouse
    return x - region.x, y - region.y


class TEXTENSION_OT_cursor(types.TextOperator):
    """Umbrella operator delegating actions to other operators based on
    articulation and intersect testing.
    """
    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'LEFTMOUSE', 'PRESS', note="HIDDEN")
        kmi_new(cls, "Text", cls.bl_idname, 'LEFTMOUSE', 'PRESS', shift=True, note="HIDDEN")

        # Disable the default keymaps
        kmi_mute(cls, "Text", idname="text.selection_set")
        kmi_mute(cls, "Text", idname="text.selection_set", shift=True)
        kmi_mute(cls, "Text", idname="text.cursor_set")
        kmi_mute(cls, "Text", type='LEFTMOUSE', value='DOUBLE_CLICK')

    def execute(self, context):
        # If a previous hit result exists, we execute it.
        if TEXTENSION_OT_hit_test.hit is not None:
            TEXTENSION_OT_hit_test.hit()

        # Otherwise perform default behavior.
        else:
            clicks = LeftClicks.track(context)

            # Handle main text view cursor selections
            if clicks == 1:
                bpy.ops.textension.set_cursor('INVOKE_DEFAULT')

            elif clicks == 2:
                bpy.ops.textension.set_cursor()
                bpy.ops.textension.drag_select('INVOKE_DEFAULT')

            elif clicks == 3:
                bpy.ops.textension.select_line()
        return {'CANCELLED'}


class TEXTENSION_OT_set_cursor(types.TextOperator):

    """Internal operator"""
    def execute(self, context, **kw):
        if not kw:
            kw = dict(zip("xy", mouse_region(context)))
        return _call('TEXT_OT_cursor_set', None, kw, 'EXEC_DEFAULT', False)

    def modal(self, context, event):
        if self.is_finished:
            return {'CANCELLED'}
        elif event.type == 'MOUSEMOVE':
            self.execute(context, x=event.mouse_region_x, y=event.mouse_region_y)
            context.edit_text.cursor_anchor = self.anchor
        elif event.type not in {'TIMER', 'TIMER_REPORT', 'NONE', 'INBETWEEN_MOUSEMOVE'}:
            self.is_finished = True
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        text = context.edit_text
        anchor = text.cursor_anchor
        self.execute(context, x=event.mouse_region_x, y=event.mouse_region_y)
        # When shift is held down, the anchor is restored.
        if event.shift:
            text.cursor_anchor = anchor
        self.anchor = text.cursor_anchor
        self.is_finished = False
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


class TEXTENSION_OT_select_line(types.TextOperator):
    """Internal operator"""
    def execute(self, context):
        text = context.edit_text
        text.curl = text.sell
        text.curc = 0
        if text.sell < len(text.lines) - 1:
            text.sell += 1
            text.selc = 0
        else:
            text.selc = len(text.select_end_line.body)
        return {'CANCELLED'}


class TEXTENSION_OT_select_word(types.TextOperator):
    """Internal operator"""
    def execute(self, context):
        text = context.edit_text
        string = text.select_end_line.body
        col = text.selc
        if text.curl != text.sell:
            text.curl = text.sell
        text.selc += find_word_boundary(string[col:], reverse=False, strict=True)
        text.curc = col - find_word_boundary(string[:col], reverse=True, strict=True)
        return {'CANCELLED'}

# class TEXTENSION_OT_cursor(types.TextOperator):
#     @classmethod
#     def register_keymaps(cls):
#         kmi_args(cls, "Text", cls.bl_idname, 'PRESS')
#         kmi_new('LEFTMOUSE', note="Set Cursor")
#         kmi_new('MIDDLEMOUSE', note="Set Cursor2")
#         kmi_mute(cls, "Text", idname="text.selection_set")
#         kmi_mute(cls, "Text", idname="text.cursor_set")
#         kmi_mute(cls, "Text", type='LEFTMOUSE', value='DOUBLE_CLICK')

#     def execute(self, context):
#         if self.poll(context):
#             bpy.ops.textension.cursor('INVOKE_DEFAULT')
#             # return {'CAN'}
#         return {'CANCELLED'}

#     def modal(self, context, event, *, mt=imp.time.monotonic):
#         if self.end or event.type not in {'TIMER', 'TIMER_REPORT', 'MOUSEMOVE',
#                                           'INBETWEEN_MOUSEMOVE', 'NONE'}:
#             context.window_manager.event_timer_remove(self.timer)
#             return {'CANCELLED'}
#         if mt() > self.delay:
#             mpos = event.mouse_x, event.mouse_y
#             if utils.isect_check(context, mpos) not in ("UP", "DOWN"):
#                 self.end = True
#                 return {'RUNNING_MODAL'}
#             self.delay = mt() + 0.1
#             scroll(lines=self.lines, direction=self.direction)
#         return {'RUNNING_MODAL'}

#     def invoke(self, context, event, *, mt=imp.time.monotonic):
#         evt = event.type
#         mpos = event.mouse_x, event.mouse_y
#         ret = utils.isect_check(context, mpos)
#         if evt == 'MIDDLEMOUSE' and ret in {"UP", "DOWN", "THUMB"}:
#             return bpy.ops.textension.scrollbar('INVOKE_DEFAULT', jump=True)
#         window = context.window
#         wm = context.window_manager
#         if ret in {"LNUM", None}:
#             return bpy.ops.textension.cursor_internal('INVOKE_DEFAULT')
#         elif ret == "THUMB":
#             return bpy.ops.textension.scrollbar('INVOKE_DEFAULT')
#         elif ret == "AZONE":
#             window.cursor_set("DEFAULT")
#             return bpy.ops.screen.actionzone('INVOKE_DEFAULT')
#         elif ret in ("UP", "DOWN"):
#             st = context.space_data
#             self.lines = context.region.height // st.runtime.lheight_px
#             self.direction = ret
#             self.end = False
#             self.delay = mt() + 0.22
#             wm.modal_handler_add(self)
#             scroll(lines=self.lines, direction=self.direction)
#             self.timer = wm.event_timer_add(0.05, window=window)
#             return {'RUNNING_MODAL'}
#         return {'CANCELLED'}


# Unified operator for setting cursor and selection.
# - Allow scrolling while selecting.
# - Allow line selection from line numbers margin.
# - Allow double click drag selection
# - Allow triple click line selection
# class TEXTENSION_OT_cursor_internal(types.TextOperator):

#     _ignore = {'TIMER', 'TIMER_REPORT'}
#     _allow = {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'INBETWEEN_MOUSEMOVE',
#               'MOUSEMOVE', 'NONE', 'EVT_TWEAK_L', *_ignore, 'LEFTMOUSE'}

#     line_select: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})

#     def execute(self, context):
#         kwargs = self.as_keywords()
#         return bpy.ops.textension.cursor('INVOKE_DEFAULT', **kwargs)

#     def modal(self, context, event):

#         # Ignore timer events.
#         if event.type in self._ignore:
#             return {'RUNNING_MODAL'}

#         # Allow mouse wheel scrolling while selecting.
#         if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
#             direction = 'DOWN'
#             if event.type == 'WHEELUPMOUSE':
#                 direction = 'UP'
#             bpy.ops.textension.scroll2('INVOKE_DEFAULT', direction=direction)
#             return {'RUNNING_MODAL'}

#         # End.
#         if event.value == 'RELEASE' or event.type not in self._allow:
#             if event.type not in {'EVT_TWEAK_L'}:
#                 self.active(False)
#                 cursor_history_add(context)
#                 return {'FINISHED'}

#         # Update cursor.
#         self.cursor_set(event, line_select=self.line_select)
#         cursor = self.cursor_edge(event)
#         if cursor:
#             self.scroll(cursor)
#         return {'RUNNING_MODAL'}

#     def select_line(self, context):
#         text = context.edit_text
#         text.curc = 0
#         if text.sell != len(text.lines) - 1:
#             text.selc = 0
#             text.sell += 1
#         else:
#             text.selc = -1
#         return {'FINISHED'}

#     @classmethod
#     def active(cls, state=None):
#         if state is None:
#             return setdefault(cls, "_active", False)
#         cls._active = state

#     @classmethod
#     def mxy(cls, val=None):
#         if val is None:
#             return setdefault(cls, "_mxy", (-1, -1))
#         cls._mxy = val

#     @classmethod
#     def clicks(cls, val=None, iadd=None):
#         if iadd is not None:
#             return iadd_default(cls, "_clicks", 0, iadd)
#         elif val is None:
#             return setdefault(cls, "_clicks", 0)
#         cls._clicks = val

#     @classmethod
#     def click_time(cls, val=None):
#         if val is None:
#             return setdefault(cls, "_click_time", -1)
#         cls._click_time = val

#     def isclose_vec2(self, vec2a, vec2b, tol=3):
#         x1, y1 = vec2a
#         x2, y2 = vec2b
#         if abs(x1 - x2) > tol or abs(y1 - y2) > tol:
#             return False
#         return True

#     def count_clicks(self, event, *, mt=imp.time.monotonic):
#         mxy = event.mouse_region_x, event.mouse_region_y
#         # Allow an error margin of 3 pixels.
#         if not self.isclose_vec2(self.mxy(), mxy):
#             self.mxy(mxy)
#             self.clicks(1)
#             return 1
#         if self.clicks() == 1:
#             self.mxy(mxy)
#         if mxy == self.mxy():
#             if mt() < self.click_time() + 0.7:
#                 if self.clicks() > 2:
#                     self.clicks(0)
#                     return 3
#                 self.clicks(iadd=1)
#                 return self.clicks()

#         self.clicks(1)
#         self.mxy(mxy)
#         self.click_time(mt())
#         return self.clicks()

#     def invoke(self, context, event, *, mt=imp.time.monotonic):
#         region = context.region
#         # Skip if cursor is scrollbar region.
#         if in_scroll(event, region):
#             return {'PASS_THROUGH'}

#         cursor_history_add(context)
#         st = context.space_data
#         rh = region.height
#         x2 = lnum_margin_width_get(st)
#         clicks = self.count_clicks(event)
#         prefs_ = prefs()

#         if clicks == 1:
#             self.click_time(mt())

#         # Don't count towards clicks if cursor in line number margin.
#         in_marg = st.show_line_numbers and cursor_isect_xy(event, 0, x2, 0, rh)
#         do_line_sel = prefs_.use_line_number_select and in_marg

#         if not do_line_sel:
#             if clicks == 2:
#                 return bpy.ops.textension.drag_select('INVOKE_DEFAULT')
#             if clicks == 3:
#                 self.clicks(0)
#                 if prefs_.triple_click == 'LINE':
#                     return self.select_line(context)
#                 return bpy.ops.textension.expand_to_path()

#         self.line_select = do_line_sel
#         self.click_time(mt())
#         self.active(True)
#         lh = st.runtime.lheight_px
#         text = st.text
#         lnlen = st.drawcache.nlines
#         rw = region.width

#         # Approximate cursor position (line).
#         def cursor_pos_y(event):
#             return st.top + int((rh - event.mouse_region_y - 3) / lh)

#         # Initial line during line selection. Keep track so the cursor end can
#         # be moved up or down based on its relative position.
#         self.init_line = cursor_pos_y(event)

#         # Actual scroll function
#         def scroll(val):
#             _max = self.scroll_max
#             val = min(5, max(-5, val))
#             if val > 0 and st.top < _max or val < 0 and st.top > 0:
#                 st.top += val
#                 st.top = min(_max, clamp(st.top))

#         # Determine if cursor is near region top/bottom.
#         def cursor_edge(event=event):
#             mry = event.mouse_region_y
#             pos = None
#             if mry > rh - lh:
#                 pos = (rh - lh - mry) // 10
#             elif mry < lh:
#                 pos = (lh - mry) // 10
#             return pos and min(5, max(-5, pos))

#         # Cursor set function.
#         def cursor_set(event, select=True, line_select=False):
#             x = event.mouse_region_x
#             y = min(rh, max(lh, event.mouse_region_y))
#             _call('TEXT_OT_cursor_set', {}, {'x': x, 'y': y})
#             if select:
#                 text.curl = curl
#                 text.curc = curc
#             if line_select:
#                 curr_line = text.sell
#                 if cursor_pos_y(event) >= self.init_line:
#                     if curr_line < lnlen - 1:
#                         text.sell += 1
#                     else:
#                         # No more lines below. Select to last character.
#                         text.selc = len(text.select_end_line.body)
#                 else:
#                     text.curc = len(text.current_line.body)

#         self.cursor_set = cursor_set
#         self.scroll = scroll
#         self.cursor_edge = cursor_edge
#         self.scroll_max = (lnlen - st.visible_lines // 2)
#         self.scroll_lines = prefs_.wheel_scroll_lines
#         if st.show_word_wrap:
#             self.scroll_max += offl_get(st, rw)

#         context.window_manager.modal_handler_add(self)
#         cursor_set(event, select=False, line_select=self.line_select)
#         curl = text.curl
#         curc = text.curc
#         return {'RUNNING_MODAL'}


class TEXTENSION_OT_select_all(types.TextOperator):
    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'A', 'PRESS', ctrl=1)

    def execute(self, context):
        context.space_data.text.select_set(0, 0, -1, -1)
        return {'CANCELLED'}


class TEXTENSION_OT_toggle_header(types.TextOperator):
    @classmethod
    def register_keymaps(cls):
        kmi_args(cls, "Text Generic", cls.bl_idname, "PRESS")
        kmi_new('LEFT_ALT')
        kmi_new('RIGHT_ALT')

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


class TEXTENSION_OT_hit_test(types.TextOperator):
    # Hit test hooks per region
    hooks = {
        'WINDOW': [],
        'HEADER': [],
    }
    fail_hooks = []
    hit = None

    @classmethod
    def register_keymaps(cls):
        args = cls, "Screen Editing", cls.bl_idname
        kmi_new(*args, 'MOUSEMOVE', 'ANY', note="HIDDEN")

    @classmethod
    def hit_test(cls, context):
        if cls.poll(context):
            return cls.hit
        return None

    @classmethod
    def poll(cls, context, *, data=types.HitTestData(), hooks=hooks):
        if not is_spacetext(st := context.space_data):
            return False  # TODO: Call hit test fail hooks

        region = context.region
        region_type = region.type

        if region_type in hooks:

            # Update HitTestData members
            data.space_data = st
            data.region = region

            x, y = context.window.event.pos
            x -= region.x
            y -= region.y
            data.pos = x, y
            for hook in hooks[region_type]:
                if hit := hook(data):
                    cls.hit = hit
                    return True
            else:
                while cls.fail_hooks:
                    cls.fail_hooks.pop()()
                
        cls.hit = None
        return False

    @staticmethod
    def get_data(context, *, data=poll.__func__.__kwdefaults__["data"]):
        data.space_data = context.space_data
        data.region = context.region
        return data

    def invoke(self, context, event):
        return {'CANCELLED'}


def get_actual_line_from_offset_line(st, offset_idx):
    """
    Given a wrapped line index, return the real line index.
    """
    if not st.show_word_wrap:
        return offset_idx

    # region = utils.region_from_space_data(st)
    c_max = get_wrap_width(st)

    offset = 0
    for idx, line in enumerate(st.text.lines):
        if idx + offset > offset_idx:
            return idx - 1
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
    return idx


def get_line_number_from_cursor(context):
    st = context.space_data
    region = context.region
    lh = st.runtime.lheight_px
    rh = region.height

    # Approximate cursor position (line).
    mry = context.window.mouse[1] - region.y
    line = st.top + int((rh - mry - 3) / lh)
    return max(0, get_actual_line_from_offset_line(st, line))


def click_line_numbers():
    line = get_line_number_from_cursor(bpy.context)
    bpy.ops.textension.line_number_select('INVOKE_DEFAULT', line=line)

def test_line_numbers(data: types.HitTestData):
    # context, st, mrx, mry, _, rh = data
    st = data.space_data
    mrx = data.region.mouse_x
    mry = data.region.mouse_y
    rh = data.region.height
    if st.show_line_numbers and prefs().use_line_number_select:
        if mrx <= st.runtime.lpad_px - st.runtime.cwidth_px:
            if mrx >= 1 and mry <= rh and mry >= 0:
                # Inside line numbers.
                data.context.window.cursor_set("DEFAULT")
                return click_line_numbers

def test_vanilla_scrollbar(data: types.HitTestData):
    # context, st, mrx, mry, rw, rh = data
    rw = data.region.width
    mrx = data.region.mouse_x
    context = data.context

    scrollbar_edge_x = rw - (0.6 * context.preferences.system.wu)
    if mrx >= scrollbar_edge_x:
        # There's a dead-zone 2 pixels beyond the vanilla scrollbar where
        # the cursor goes back to an i-beam. We can't activate the operator
        # in this area, but we can at least fix the cursor.
        if mrx < rw - 2:
            context.window.cursor_set("DEFAULT")
            return lambda: bpy.ops.text.scroll_bar('INVOKE_DEFAULT')

TEXTENSION_OT_hit_test.hooks['WINDOW'].append(test_line_numbers)
TEXTENSION_OT_hit_test.hooks['WINDOW'].append(test_vanilla_scrollbar)


class TEXTENSION_OT_line_number_select(types.TextOperator):
    line: bpy.props.IntProperty()

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        self.start_line = self.line
        text = context.edit_text
        self.max_line = len(text.lines) - 1
        self.max_column = len(text.lines[self.max_line])
        self.cursor_orig = text.cursor

        # Select the initial line
        text.cursor_anchor = self.start_line, 0
        if self.start_line == self.max_line:
            position = self.start_line, self.max_column
        else:
            position = self.start_line + 1, 0
        text.cursor_position = position
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        # TODO: Write scroll threshold for when mouse is close to top/bottom.
        text = context.edit_text

        if event.type in {'ESC', 'RIGHTMOUSE'}:
            # Restore cursor to before operator
            text.cursor = self.cursor_orig
            return {'CANCELLED'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            return {'CANCELLED'}

        curr_line = get_line_number_from_cursor(bpy.context)

        anchor = self.start_line, 0
        position = curr_line + 1, 0

        if curr_line < self.start_line:
            position = curr_line, 0
            anchor = self.start_line + 1, 0
            if self.start_line == self.max_line:
                anchor = self.start_line, self.max_column

        elif curr_line == self.max_line:
            position = curr_line, self.max_column

        text.cursor_anchor = anchor
        text.cursor_position = position
        return {'RUNNING_MODAL'}

# Mouse move/intersect testing and change mouse cursor accordingly. Modal due
# to how the text cursor is enforced otherwise.
# class TEXTENSION_OT_intersect(types.TextOperator):
#     @classmethod
#     def register_keymaps(cls):
#         args = cls, "Screen Editing", cls.bl_idname
#         kmi_new(*args, 'MOUSEMOVE', 'ANY', note="HIDDEN")

#     poll = classmethod(utils.isect_poll_factory())

#     def invoke(self, context, event, *, mt=imp.time.monotonic):
#         ttl = mt() + 0.01
#         window = context.window
#         wm = context.window_manager
#         timer = wm.event_timer_add(0.1, window=window)
#         isect_check = utils.isect_check
#         az = False
#         end = False

#         def exit(restore=False):
#             wm.event_timer_remove(timer)
#             if restore:
#                 window.cursor_modal_restore()
#             return {'CANCELLED'}

#         def inner(event):
#             nonlocal ttl, end, az
#             if end:
#                 return exit()
#             evt = event.type
#             if evt == 'MOUSEMOVE':  # Extend time-to-live.
#                 ttl = mt() + 0.1

#             isect = isect_check(context, window.mouse)
#             if isect and context.area.type == 'TEXT_EDITOR':
#                 # Pass events when modal is running.
#                 if evt in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
#                     return {'PASS_THROUGH'}

#                 elif evt == 'LEFTMOUSE' and event.value == 'PRESS':
#                     bpy.ops.textension.cursor()
#                     return exit()

#                 # Set cursor to 'MOVE_X' if hovering action zone.
#                 elif isect == "AZONE":
#                     if not az:
#                         az = True
#                         window.cursor_modal_set("MOVE_X")
#                 elif az:
#                     az = False
#                     window.cursor_modal_set("DEFAULT")

#                 # Timeout when mouse has stopped.
#                 if evt == 'TIMER' and mt() > ttl:
#                     return exit()
#                 # Pass events not related to isect interaction, but otherwise
#                 # consume them in order to keep cursor type ('DEFAULT').
#                 elif evt not in {'LEFTMOUSE', 'MIDDLEMOUSE', 'MOUSEMOVE',
#                                  'INBETWEEN_MOUSEMOVE', 'TIMER', 'NONE',
#                                  'WHEELDOWNMOUSE', 'WHEELUPMOUSE'}:
#                     return {'PASS_THROUGH'}
#                 return {'RUNNING_MODAL'}

#             elif evt in ('LEFTMOUSE', 'WHEELDOWNMOUSE', 'WHEELUPMOUSE'):
#                 end = True
#                 return {'PASS_THROUGH'}
#             return exit(True)

#         self.inner = inner
#         wm.modal_handler_add(self)
#         window.cursor_modal_set("DEFAULT")
#         return {'RUNNING_MODAL'}

#     def modal(self, context, event):
#         return self.inner(event)


class Scroller:
    def __init__(self, context):
        st = context.space_data
        scroll_max = max(0, st.drawcache.total_lines - (st.visible_lines // 2))

        if st.top == 0 and scroll_max == 0:
            self.scroll_px = self.snap_nearest = lambda *_, **__: None
            return

        lheight = st.runtime.lheight_px
        offsets = st.offsets
        st.flags |= 0x1
        r = c_utils.ARegion(context.region)

        RGN_DRAWING = 8

        accum = 0
        clamp = False

        def offset_clamp():
            nonlocal clamp
            clamp = True
            offsets.y = 0
            r.do_draw = RGN_DRAWING

        def scroll_px(value):
            nonlocal clamp
            if st.top < 1:
                if offsets.y + value < 0 and not clamp:
                    return offset_clamp()
                elif value > 0:
                    clamp = False

            elif st.top >= scroll_max:
                if value > 0 and not clamp:
                    # Clamp when top is past scroll_max.
                    if scroll_max > 1:
                        st.top = scroll_max
                    return offset_clamp()
                elif value < 0:
                    clamp = False

            if value and not clamp:
                nonlocal accum
                lines = 0
                px = round(value + accum)
                accum = (value + accum) - px
                v = offsets.y
                px_tot = v + px

                if px_tot < 0:
                    lines = int((-lheight + px_tot) / lheight)
                    px_tot += lheight * abs(lines)

                elif px_tot > 0:
                    lines = int(px_tot / lheight)
                    px_tot -= lheight * abs(lines)

                if lines:
                    st.top += lines

                px_tot -= v
                if px_tot:
                    offsets.y += px_tot
                    if not lines:
                        r.do_draw = RGN_DRAWING

        def snap_nearest():
            """
            Snap the scroller to the closest text line.
            """
            px = -offsets.y
            scroll_px(px + (lheight * (px <= (lheight // -2))))

        self.scroll_px = scroll_px
        self.snap_nearest = snap_nearest


class TEXTENSION_OT_scroll_continuous(types.TextOperator):
    """
    Web browser-like continuous scrolling
    """
    @classmethod
    def register_keymaps(cls):
        kmi_new(cls, "Text", cls.bl_idname, 'MIDDLEMOUSE', 'PRESS')

    def invoke(self, context, event):

        # Do nothing if hit testing returns something.
        if TEXTENSION_OT_hit_test.hit is not None:
            return {'PASS_THROUGH'}

        from time import perf_counter
        region = context.region
        if in_scroll(event, region):
            return {'PASS_THROUGH'}
        start_y = region.mouse_y
        timer = context.window_manager.event_timer_add(1e-5, window=context.window)
        t_next = perf_counter()

        # Frame pacing to ensure smooth drawing.
        def in_sync(t_step=1 / 240):
            nonlocal t_next
            t_now = perf_counter()
            if t_now > t_next:
                t_next = t_now + t_step
                return True
            return False

        ps = Scroller(context)

        def inner_modal(event):
            if event.value == 'RELEASE':
                # Snap to closest line.
                ps.snap_nearest()
                context.window_manager.event_timer_remove(timer)
                context.window.cursor_modal_restore()
                region.tag_redraw()
                return {'FINISHED'}

            elif event.type != 'TIMER' or not in_sync():
                return {'RUNNING_MODAL'}

            # 30 px dead-zone.
            delta_y = start_y - event.mouse_region_y
            if -15 < delta_y < 15:
                return {'RUNNING_MODAL'}

            # Gradually increase scroll by distance.
            px = abs(delta_y ** 1.75) / 12 / 6 / 4 / 1.5 * (-(delta_y < 0) or 1)
            px = -200 if px < -200 else 200 if px > 200 else px

            ps.scroll_px(px)
            return {'RUNNING_MODAL'}

        self.inner_modal = inner_modal
        context.window.cursor_modal_set("SCROLL_Y")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    # Handle modal in closed function.
    def modal(self, context, event):
        return self.inner_modal(event)



class TEXTENSION_OT_scroll2(types.TextOperator):
    # @classmethod
    # def register_keymaps(cls):
    #     kmi_args(cls, "Text", cls.bl_idname, 'PRESS')
    #     kmi_new('WHEELDOWNMOUSE', note="Mouse Scroll Up").direction = 'DOWN'
    #     kmi_new('WHEELUPMOUSE', note="Mouse Scroll Down").direction = 'UP'

    jobs = types.classproperty("jobs", 0)
    timer = types.classproperty("timer")
    direction: bpy.props.EnumProperty(
        default='DOWN',
        name="Direction",
        description="Scroll direction",
        items=(('UP', "Up", "Scroll up"),
               ('DOWN', 'Down', "Scroll down")),
    )
    lines: bpy.props.IntProperty(
        default=0,
        name="Lines",
        description="Lines to scroll when called by script",
        options={'SKIP_SAVE'},
    )
    def set_job(self, context, state):
        if not iadd_default(self, "jobs", 0, 1 if state else -1):
            context.window_manager.event_timer_remove(self.timer)
            del self.timer

    def modal(self, context, event):
        return self.inner_modal(context, event)

    def invoke(self, context, event):
        up = self.direction == 'UP'
        prefs_ = prefs()
        # Scroll by external call.
        if self.lines != 0:
            lines = self.lines * (-1 if up else 1)
            frames = min(30, max(20, int(abs(lines) / 4)))
        # Mouse wheel scroll.
        else:
            lines = prefs_.wheel_scroll_lines * (-1 if up else 1)
            frames = max(6, int(75 / prefs_.scroll_speed) - self.jobs)

        region = context.region
        finished = False
        st = context.space_data
        lh = st.runtime.lheight_px

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

        ps = Scroller(context)
        data = iter(offsets)

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
                        ps.snap_nearest()
                    finished = True
                    return False
                ps.scroll_px(value)
                t_next = t_now + t_step
                return True
            return False

        def inner_modal(context, event):
            if region is None or finished:
                self.set_job(context, False)
                return {'FINISHED'}

            sync()
            return {'PASS_THROUGH'}

        self.inner_modal = inner_modal
        wm = context.window_manager
        wm.modal_handler_add(self)
        if not self.jobs:
            # Single timer for all ops that ends with the last job.
            self.timer = wm.event_timer_add(1e-5, window=context.window)
        self.set_job(context, True)
        return {'RUNNING_MODAL'}


class ScrollAccumulator:
    """
    Scroll accumulator callback
    """

    st: bpy.types.SpaceTextEditor
    region: bpy.types.Region
    line_offset = 0
    prev_lines = 0
    owner = None
    _instances = {}      # Accumulator instances, 1 per space data
    _data = {"depth": 0, "timer": None}

    def set(self, operator, lines):
        """
        Set the line delta (float) for the current operator.
        Returns whether clamp in effect.
        """

        if self.clamp_direction is not None:
            if lines < 0:
                if self.clamp_direction == 1:
                    return False
            elif self.clamp_direction == 2:
                return False
        self.accum_buffer[operator] = lines
        self.region_dna.do_draw = 1
        return True

    # Scrolling is application-driven via the draw callback. Blender doesn't
    # have a way to provide continuous redraws, so we still need to tag redraw
    # via a timer.
    def on_redraw(self, *, floor=float.__floor__, int=float.__int__) -> None:
        linesf = sum(self.accum_buffer.values()) + self.line_offset
        top = self.st_dna.top

        offset_factor = 0.0
        dest_top = (linesf - self.prev_lines) + top

        if dest_top < 0:
            if top != 0:
                self.st_dna.top = 0
            self.clamp_direction = 1

        elif dest_top >= self.scroll_max:
            if top != self.scroll_max:
                self.st_dna.top = self.scroll_max
            self.clamp_direction = 2
        else:
            lines = floor(linesf)
            offset_factor = linesf - lines
            lines_delta = lines - self.prev_lines
            self.prev_lines = lines
            self.st_dna.top += lines_delta
            self.clamp_direction = None

        self.runtime._offs_px[1] = int(offset_factor * self.line_height)

    # def 

    def __new__(cls, operator, st: bpy.types.SpaceTextEditor):
        try:
            self = cls._instances[st]
        except KeyError:
            self = cls._instances[st] = super().__new__(cls)
            self.region_dna = utils.region_from_space_data(st).internal
            self.st = st
            self.accum_buffer = {}
            self.st_dna = st.internal
            self.runtime = st.runtime
            self.clamp_direction = None

        self.scroll_max = max(0, st.drawcache.total_lines - (st.visible_lines // 2))

        data = self._data
        if data["timer"] is None:
            data["timer"] = _context.window_manager.event_timer_add(1e-3, window=_context.window)
        # 0x1 Enable pixel offsets (ST_SCROLL_SELECT in DNA_space_types.h)
        st.flags |= 0x1
        accum_buffer = self.accum_buffer
        if operator not in accum_buffer:
            if not accum_buffer:
                data["depth"] += 1
                self.owner = st.draw_handler_add(self.on_redraw, (), "WINDOW", "POST_PIXEL")
                self.line_height = st.runtime.lheight_px

            self.accum_buffer[operator] = 0.0
        return self

    def unregister(self, operator):
        data = self._data
        accum_buffer = self.accum_buffer
        self.line_offset += self.accum_buffer.pop(operator)

        if not accum_buffer:
            self.st.draw_handler_remove(self.owner, "WINDOW")
            self.prev_lines = 0
            self.line_offset = 0
            self.clamp_direction = None
            accum_buffer.clear()
            data["depth"] -= 1
        assert data["depth"] >= 0

        if data["depth"] == 0:
            _context.window_manager.event_timer_remove(data["timer"])
            data["timer"] = None
        self.region_dna.do_draw = 1


# def custom(v):
#     return 1.0 - ((1.0 - v) ** 1.5)

# Interpolate a smooth scroll curve. v is a value between 0 and 1.
def custom(v):
    # mul: Steepness of in-curve: Higher is faster.
    mul = 0.25
    vm = 1 - v
    vm2 = v ** 1.5 * vm
    return mul * v * vm ** 2 + 2.5 * vm2 + 0.5 * vm2 + v ** 3


class TEXTENSION_OT_scroll_lines(types.TextOperator):
    
    @classmethod
    def register_keymaps(cls):
        kmi_args(cls, "Text", cls.bl_idname, 'PRESS')
        kmi_new('WHEELDOWNMOUSE', note="Mouse Scroll Up").lines = 3
        kmi_new('WHEELUPMOUSE', note="Mouse Scroll Down").lines = -3

    lines: bpy.props.IntProperty(
        default=3,
        name="Lines",
        description="Lines to scroll, can be negative",
        options={'SKIP_SAVE'},
    )

    # When True, prevent running again until scrolling has finished.
    exclusive: bpy.props.BoolProperty(default=False)
    _block = False

    def invoke(self, context, _):
        if self.lines == 0 or self._block:
            return {'CANCELLED'}
        
        # When 'exclusive' is set, block other instances of this operator.
        if self.exclusive:
            __class__._block = True

        st = context.space_data
        self.accum = ScrollAccumulator(self, st)

        self.do_exit = False
        self.coeff = 1 / 0.125
        self.start = perf_counter()

        wm = context.window_manager
        self.region = context.region
        wm.modal_handler_add(self)
        self.modal(context, _)
        return {'RUNNING_MODAL'}

    def modal(self, _, event):
        if self.do_exit:
            self.accum.unregister(self)
            if self.exclusive:
                __class__._block = False
            return {'CANCELLED'}

        elif event.type == 'TIMER':
            f = (perf_counter() - self.start) * self.coeff
            if f > 1.0:
                f = 1.0
                self.do_exit = True
            
            if not self.accum.set(self, self.lines * custom(f)):
                self.do_exit = True
        return {'PASS_THROUGH'}


class TEXTENSION_OT_rename_text(types.TextOperator):
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Rename text"

    name: bpy.props.StringProperty(name="Name")

    def execute(self, context):
        if self.name:
            context.edit_text.name = self.name
            context.window.screen = context.window.screen
            return {'FINISHED'}
        return {'CANCELLED'}

    def draw(self, context):
        layout = self.layout
        layout.activate_init = True
        layout.prop(self, "name")

    def invoke(self, context, event):
        self.name = context.edit_text.name
        return context.window_manager.invoke_props_popup(self, event)

    @classmethod
    def register_keymaps(cls):
        kmi_args(cls, "Screen Editing", cls.bl_idname, 'PRESS')
        kmi_new('F2', note="Rename Text")


def classes(cached=[]):
    if not cached:
        for cls in globals().values():
            if hasattr(cls, "bl_rna") and cls.__module__ == __name__:
                cached.append(cls)
    return cached


# Resizing a view with word wrapping can cause the text body to disappear
# upwards. This callback clamps the view.
def _clamp_viewrange(context=_context):
    st = context.space_data
    dna = st.internal
    max_top = max(0, st.drawcache.total_lines - (dna.runtime.viewlines // 2))
    if dna.top > max_top:
        dna.top = max_top
        # XXX: This is a hack, but we NEED to redraw now, because waiting
        # until the next frame would cause a rubberband visual effect.
        utils._redraw_now()

def _get_text_id(self):
    while True:
        try:
            if int(id := self["id"]).bit_length() != 31:
                raise ValueError
            return id
        except:
            from os import urandom
            import ctypes
            used_ids = {t.get("id", 0) for t in bpy.data.texts}
            while True:
                new_id = ctypes.c_int.from_buffer_copy(urandom(4)).value
                if new_id not in used_ids:
                    self["id"] = new_id
                    break



# Assume keymaps aren't ready, defer registration.
def register(ready=False):
    version = bpy.app.version
    if version < (2, 83):
        raise Exception("\nMinimum Blender version 2.83 required, found %s\n" % version)

    if not ready:
        keymaps = "Text", "Text Generic", "View2D", "Screen Editing"
        from .km_utils import keymaps_ensure
        return keymaps_ensure(register, keymaps)

    # Initialize C-related utilities and API extensions.
    from .c_utils import _initialize
    _initialize()

    # Keymaps are ready, register.
    utils.register_class(utils.TextensionPreferences)

    # Register operators.
    utils.register_class_iter(classes())

    # Add New Text operator to right-click
    set_text_context_menu(True)
    utils.add_draw_hook(_clamp_viewrange, bpy.types.SpaceTextEditor)

    bpy.types.Text.id = bpy.props.IntProperty(get=_get_text_id)

def unregister():
    assert utils.remove_draw_hook(_clamp_viewrange)
    # Unregister operators.
    utils.unregister_class_iter(classes())
    utils.unregister_class(utils.TextensionPreferences)

    set_text_context_menu(False)

    # Remove API extensions
    from .c_utils import _uninitialize
    _uninitialize()

    # Remove RNA subscriptions.
    bpy.msgbus.clear_by_owner(utils.SUBSCRIBE_OWNER)
    from . import gl
    gl.clear_caches()

    del bpy.types.Text.id

def get_plugins() -> dict:
    """
    Scan and return a list of plugin submodules that can be enabled.

    A sub-module/package is considered a plugin if it has the attributes
    "enable" and "disable".

    Returns a dictionary of plugin name and module pairs.
    """

    import pkgutil
    import os

    plugins_path = os.path.join(__path__[0], ".\\plugins")

    if not os.path.isdir(plugins_path):
        print("Textension: missing 'plugins' directory.")
        return

    plugins = {}
    py_path = f"{__package__}.plugins"

    for info in pkgutil.iter_modules([plugins_path]):
        m = __import__(f"{py_path}.{info.name}", fromlist=(info.name,))

        if hasattr(m, "enable") and hasattr(m, "disable"):
            plugins[info.name] = m

    return plugins


loaded = True


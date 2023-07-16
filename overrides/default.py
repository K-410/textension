# This module implements default text operator overrides.

from textension.btypes.defs import OPERATOR_CANCELLED, OPERATOR_FINISHED, OPERATOR_PASS_THROUGH, OPERATOR_RUNNING_MODAL
from textension.btypes import wmWindowManager, event_type_to_string
from textension.core import test_line_numbers, iter_brackets, ensure_cursor_view, copy_selection
from textension.utils import _context, add_keymap, _call, cm, tag_text_dirty, unsuppress, classproperty, starchain
from textension.ui import get_mouse_region
from textension.operators import find_word_boundary

from typing import Callable
from . import OpOverride
import re


# TODO: Integrate into Default._post_hooks
delete_hooks = []
insert_hooks = []


class Default(OpOverride):
    @classproperty
    def operators(cls):
        for c in Default.__subclasses__():
            if "_OT_" in c.__name__:
                yield c

    def __init_subclass__(cls):
        super().__init_subclass__()
        cls._pre_hooks  = []  # Run before the operator, potentially blocking.
        cls._post_hooks = []  # Run after the operator.

    @classmethod
    def add_pre(cls, hook: Callable, is_global: bool = False):
        cls._pre_hooks.insert(0, (is_global, _context.space_data, hook))

    @classmethod
    def add_post(cls, hook: Callable, is_global: bool = False):
        cls._post_hooks.insert(0, (is_global, _context.space_data, hook))

    @classmethod
    def remove_pre(cls, ref_hook: Callable):
        return cls._remove_hook(cls._pre_hooks, ref_hook)

    @classmethod
    def remove_post(cls, ref_hook: Callable):
        return cls._remove_hook(cls._post_hooks, ref_hook)

    @classmethod
    def run_pre_hooks(cls, *args, **kw):
        return cls._run_hooks(cls._pre_hooks, *args, **kw)

    @classmethod
    def run_post_hooks(cls, *args, **kw):
        return cls._run_hooks(cls._post_hooks, *args, **kw)

    @classmethod
    def _remove_hook(cls, hooks, ref_hook):
        for index, (is_global, space_data, hook) in enumerate(hooks):
            if hook == ref_hook:
                return hooks.pop(index)
        raise Exception(f"Hook '{ref_hook}' not registered.\n{hooks}" )

    @staticmethod
    def _run_hooks(hooks, *args, **kw):
        for is_global, space, hook in hooks:
            if is_global or space == _context.space_data:
                if hook(*args, **kw):
                    return True
        return False


def _in_string(text):
    sell, selc = text.cursor_focus
    line_format = text.lines[sell].format

    if b"l" in line_format and selc < len(line_format):
        return line_format[selc] == 108

    line, col = text.cursor_sorted[:2]
    for type, start, end in iter_brackets(text.as_string(), strict=False):
        if start < (line, col) < end and type > 0:  # Single or triple quoted string.
            return True
    return False


class TEXT_OT_insert(Default):
    def invoke(self):

        typed = self.event.utf8_buf.decode()

        if self.run_pre_hooks(typed):
            return OPERATOR_CANCELLED

        # Pass on when mouse hovers the line numbers margin.
        # XXX: test_line_numbers isn't even working.
        # if test_line_numbers(*get_mouse_region()):
        #     return OPERATOR_PASS_THROUGH

        # Pass on zero-length text inputs.
        if not typed:
            return OPERATOR_PASS_THROUGH
        ColumnRetainer.clear()

        # Some keys generate more than 1 character.
        advance = len(typed)

        text = _context.edit_text

        anchor = curl, curc = text.cursor_anchor
        focus  = sell, selc = text.cursor_focus

        if anchor > focus:
            body = text.current_line.body
            line = sell
            col  = selc
        else:
            body = text.select_end_line.body
            line = curl
            col  = curc

        try:
            next_char = body[curc]
        except IndexError:
            # Cursor was at EOF
            next_char = ""

        last_format = text.format_from_indices(curl, max(0, curc - 1), sell, selc)

        if anchor != focus and typed in {'"', "'", '(', '[', '{'}:
            from textension.utils import starchain
            selected_text = text.string_from_indices(*starchain(sorted((anchor, focus))))
            bracket = typed
            opposite = dict(zip("([{\"\'", ")]}\"\'"))

            # Inline: increment both columns
            # Multi-line: increment the top column
            selc += 1 - (curl < sell)
            curc += 1 - (curl > sell)

            text.write(f"{bracket}{selected_text}{opposite[bracket]}")
            text.cursor = curl, curc, sell, selc

        else:
            closed = {")", "]", "}"}
            pairs  = {"(": ")", "[": "]", "{": "}"}

            # If the character is a quote...
            if typed in {'"', "'"}:

                # .. then check whether we're inside a string
                # to figure out if we should add a closing quote.
                in_string = _in_string(text)

                # If the character forms a multi-line bracket, close.
                if (ml := body[curc - 2: curc] + typed) in {'"""', "'''"} and next_char != typed and \
                    body[curc - 3: curc - 2] != "\\":
                        typed += ml

                # If the character forms a pair, advance.
                elif next_char == typed:
                    typed = ""

                # If we're not inside a string or on a word boundary, close.
                elif next_char in {" ", ""} | closed and not in_string:
                    typed += typed

            # If the character opens a bracket on a word boundary, close.
            elif typed in pairs and next_char in {" ", ""} | closed:
                typed += pairs[typed]

            # If the character is closed, advance.
            elif typed in closed and next_char == typed:
                typed = ""

            text.write(typed)
            text.select_set(line, col + advance, line, col + advance)

            for func in insert_hooks:
                func(line, col + advance, last_format)

        ensure_cursor_view()
        return OPERATOR_FINISHED


class TEXT_OT_line_number(Default):
    def invoke(self):
        ColumnRetainer.clear()
        dna = _context.space_data.internal
        top = dna.top
        ret = self.default()
        if ret == OPERATOR_FINISHED:
            dna.top = top
            ensure_cursor_view(action="center")
        return ret


class TEXT_OT_copy(Default):
    def exec(self):
        if self.run_pre_hooks():
            return OPERATOR_CANCELLED

        if copy_selection(_context.edit_text):
            return OPERATOR_FINISHED
        return OPERATOR_CANCELLED


class TEXT_OT_cut(Default):
    def exec(self):
        if self.run_pre_hooks():
            return OPERATOR_CANCELLED

        text = _context.edit_text
        if copy_selection(text) is None:
            return OPERATOR_CANCELLED
        ColumnRetainer.clear()

        line, pos = cursor = text.cursor_start

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
        return OPERATOR_FINISHED


def get_enum_type(override: Default, fallback=None):
    from operator import attrgetter
    from itertools import chain

    names = ("shift", "ctrl", "alt", "oskey")
    get_kmis = attrgetter("keymap_items")
    get_modifiers = attrgetter(*names)

    keymaps = _context.window_manager.keyconfigs.active.keymaps
    kms = filter(None, map(keymaps.get, ("Text", "Text Generic")))

    event = override.event
    type = event_type_to_string(event.type)
    modifier = int.from_bytes(event.modifier, "little")
    modifiers = tuple(bool(modifier & (1 << b)) for b in (0, 1, 2, 3))

    idname = override.bl_idname
    for kmi in starchain(map(get_kmis, kms)):
        if kmi.idname == idname and kmi.type == type and kmi.active:
            if kmi.any or get_modifiers(kmi) == modifiers:
                return kmi.properties.type

    return fallback


class TEXT_OT_delete(Default):
    def exec(self):
        delete_type = get_enum_type(self, fallback='PREVIOUS_CHARACTER')

        if self.run_pre_hooks(delete_type):
            return OPERATOR_CANCELLED
        ColumnRetainer.clear()

        text = _context.edit_text
        selected_text = text.selected_text
        endline, endcol = line, col = text.cursor_start

        curl, curc, sell, selc = text.cursor
        last_format = text.format_from_indices(curl, max(0, curc - 1), sell, selc)

        if selected_text:
            cursor_post = (line, col)
            cursor_sel = None

        else:

            body = text.current_line.body
            if "PREVIOUS" in delete_type:
                if line == col == 0:
                    return OPERATOR_CANCELLED
                endcol -= 1
                wrap = col is 0
                if "WORD" in delete_type:
                    endcol = col - find_word_boundary(body[:col][::-1])
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
                        width = _context.space_data.tab_width
                        if spaces >= width and text.indentation == 'SPACES':
                            endcol = col - ((spaces % width) or width)

                if wrap:
                    endline -= 1
                    endcol = len(text.lines[endline].body)

            elif "NEXT" in delete_type:
                is_eol = col == len(body)
                if line == len(text.lines) - 1 and is_eol:
                    return OPERATOR_CANCELLED

                col += 1
                wrap = is_eol
                if "WORD" in delete_type:
                    col = endcol + find_word_boundary(body[endcol:])
                    wrap = col == endcol

                if wrap:
                    line += 1
                    col = 0

            cursor_post = (endline, endcol)
            cursor_sel = (line, col)

        # cursor_post: Where the cursor is set after deleting
        # cursor_sel:  What the cursor extends (selects) before deleting
        if cursor_sel is not None:
            text.cursor = (*cursor_sel, *cursor_post)
        text.write("")
        text.cursor = cursor_post
        
        for func in delete_hooks:
            func(*cursor_post, last_format)

        return OPERATOR_FINISHED


class TEXT_OT_indent(Default):
    def exec(self):
        ColumnRetainer.clear()
        text = _context.edit_text
        tab_width = _context.space_data.tab_width
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

                line_strings += f"{writestr}{body}",
                lengths += length,

            # Write the lines with new indentation
            text.cursor = ltop, 0, lbot, -1
            text.write("\n".join(line_strings))

            if flipped:
                ltop, lbot = lbot, ltop
                ctop, cbot = cbot, ctop
            text.cursor = ltop, ctop + lengths[0], lbot, cbot + lengths[-1]
        ensure_cursor_view()
        return OPERATOR_FINISHED


class TEXT_OT_open(Default):
    def exec(self):
        ret = self.default()
        if ret == OPERATOR_FINISHED:
            wm = wmWindowManager(_context.window_manager)
            wm.file_saved = 0
            # if not _context.blend_data.is_dirty:
            #     bpy.ops.ed.undo_push(message="Open Text")
            return OPERATOR_CANCELLED
        return ret

    def invoke(self):
        ret = self.default()
        if ret == OPERATOR_FINISHED:
            wm = wmWindowManager(_context.window_manager)
            wm.file_saved = 0
            # if not _context.blend_data.is_dirty:
            #     bpy.ops.ed.undo_push(message="Open Text")
            return OPERATOR_CANCELLED
        return ret


class TEXT_OT_paste(Default):
    def exec(self):
        if self.run_pre_hooks():
            return OPERATOR_CANCELLED

        data = _context.window_manager.clipboard.replace("\r", "")

        if not data:
            return OPERATOR_CANCELLED
        ColumnRetainer.clear()

        text = _context.edit_text
        line, column = text.cursor_start
        endline, endcol = line, column

        lines = data.count("\n")
        col_move = len(data.splitlines()[-1])
        endcol += col_move

        if lines > 0:
            endline += lines
            endcol = col_move

        if not text.selected_text:
            if data.count("\n") == 1 and data[-1:] == "\n":
                text.cursor = line, 0
                endline = line + 1
                endcol = column

        text.write(data)
        text.cursor = endline, endcol
        ensure_cursor_view()
        return OPERATOR_FINISHED


class TEXT_OT_unindent(Default):
    def exec(self):
        ColumnRetainer.clear()
        text = _context.edit_text
        ltop, ctop, lbot, cbot = text.cursor_sorted
        tab_width = _context.space_data.tab_width
        
        set_indents = []
        lengths = []

        # Defer the unindenting until we know the lines *can* be unindented.
        for line in text.lines[ltop: lbot + 1]:
            indent = line.indent
            if not indent:
                lengths += 0,
                continue

            # Remainder if the leading indent is unaligned
            i = indent % tab_width
            set_indents += (line, line.indent_level - (not i)),
            lengths += i or tab_width,

        if not set_indents:
            return OPERATOR_CANCELLED

        for line, level in set_indents:
            line.indent_level = level

        # Offset the cursor ends
        ctop -= lengths[0]
        cbot -= lengths[-1]

        if text.cursor_flipped:
            ltop, lbot = lbot, ltop
            ctop, cbot = cbot, ctop
        text.cursor = ltop, max(0, ctop), lbot, max(0, cbot)
        ensure_cursor_view()
        return OPERATOR_FINISHED


# Patterns for incrementing/decrementing indentation.
block_start = re.compile(
    r"^[ \t]*?\b(?:def|if|for|class|else|elif|with"
    r"|while|try|except|finally|match|case)\b.*?\:")

block_end = re.compile(
    r"^[ \t]*?\b(?:pass|break|continue|raise|return)\b")


def calc_next_indent(st):
    text = st.text
    line, column = text.cursor.start
    line_obj = text.lines[line]
    leading_init = line_obj.body[:column]

    indent, units = get_indent_type_and_units(st)

    # If the current line's leading body only has whitespace, we just
    # return the same indent.
    if not (block_start.match(leading_init) or block_end.match(leading_init)):
        if not leading_init.strip():
            return leading_init

    # Skip lines upwards that start with a comment.
    while line >= 0:
        line_obj = text.lines[line]
        if line_obj.body.strip() and line_obj.format[0:1] != b"#":
            column = len(line_obj.body)
            break
        line -= 1

    leading = line_obj.body[:column]
    level = len(leading) - len(leading.lstrip())
    # Deduce the next indent based on the line contents.
    if block_start.match(leading):
        level += units
    elif block_end.match(leading):
        level -= units

    return indent * level


def get_indent_type_and_units(st):
    if st.text.indentation == 'SPACES':
        return " ", st.tab_width
    else:
        # Always 1 for tabs.
        return "\t", 1


def get_closing_bracket(string):
    if (match := re.match(r"^.*?([\[\(\{])\s*?$", string) or \
                 re.match(r"^.*?(?:[^\"\']([\"\']{3}))\s*?$", string)):
        bracket = match.group(1)
        return {"(": ")", "[": "]", "{": "}"}.get(bracket, bracket)
    return None


class TEXT_OT_line_break(Default):
    def invoke(self):
        if self.run_pre_hooks():
            return OPERATOR_CANCELLED
        ColumnRetainer.clear()

        text = _context.edit_text
        st = _context.space_data

        line, col = text.cursor_start
        body = text.lines[line].body

        prefix = ""
        # Ctrl is pressed, skip the line break.
        if self.event.ctrl:
            text.cursor_set(line, character=len(body))

        else:
            leading = body[:col]
            if closing := get_closing_bracket(leading):

                # If the cursor is between brackets, format the line break so
                # that the end bracket is moved to the second line
                if body[col:].lstrip().startswith(closing):
                    level = len(leading) - len(leading.lstrip())
                    indent, units = get_indent_type_and_units(st)
                    prefix = "\n" + (indent * (level + units))
                else:
                    pass  # XXX: Could add param/arg formatting here.

        # The initial indent leading up to the cursor.
        to_write = prefix + "\n" + calc_next_indent(st)

        # Start the line as a comment if that's what we're breaking.
        if text.lines[line].format[col:col + 1] == b"#":
            to_write += "# "

        text.write(to_write)
        text.cursor_set(line + 1, character=len(to_write) - 1)
        ensure_cursor_view()
        return OPERATOR_FINISHED

    @classmethod
    def apply_override(cls):
        super().apply_override()
        cls.keymaps = [
            add_keymap("Text", "text.line_break", 'RET', 'PRESS', ctrl=True),
            add_keymap("Text", "text.line_break", 'RET', 'PRESS', shift=True),
            add_keymap("Text", "text.line_break", 'NUMPAD_ENTER', 'PRESS', ctrl=True),
            add_keymap("Text", "text.line_break", 'NUMPAD_ENTER', 'PRESS', shift=True),
        ]

    @classmethod
    def remove_override(cls):
        super().remove_override()
        for km, kmi in cls.keymaps:
            km.keymap_items.remove(kmi)


def toggle_comment():
    st = _context.space_data
    text = _context.edit_text
    l1, c1, l2, c2 = text.cursor

    lines = text.lines
    do_comment = False

    # Store the lengths to adjust the column positions after toggling.
    l1_len = len(lines[l1].body)
    l2_len = len(lines[l2].body)

    to_process = []
    level = 1 << 31
    s1, s2 = sorted((l1, l2))

    # 1. Find the smallest indentation.
    # 2. Decide whether to comment.
    # 3. Skip empty lines for now.
    for line_obj in lines[s1:s2 + 1]:
        body = line_obj.body
        if body.strip():
            to_process += line_obj,

            lstripped = body.lstrip()
            level = min(level, len(body) - len(lstripped))

            if not lstripped.startswith("#"):
                do_comment = True

    # The line range has no content. Comment all lines except the last
    # line, unless there's just a single line then comment that.
    if not to_process:
        to_process = lines[s1:max(s1 + 1, s2)]
        do_comment = True
        level = 0

    indent, units = get_indent_type_and_units(st)
    # The common leading indentation before the comment sign.
    leading_indent = indent * level

    # The toggle pass.
    for line_obj in to_process:
        if do_comment:
            result = leading_indent + "# "
            result += line_obj.body.removeprefix(leading_indent)
        else:
            body = line_obj.body
            # The whitespace-stripped contents after ``#``.
            post_body = body.lstrip()[1:].lstrip()

            # Subtract remainders not divisible by ``units``.
            # Fixes Blender's non- PEP 8-compliant comments.
            level = len(body) - len(post_body) - 1
            level = level - (level % units)
            result = (indent * level) + post_body
        line_obj.body = result

    # Restore cursor with the new column offsets.
    o1 = len(lines[l1].body) - l1_len
    o2 = len(lines[l2].body) - l2_len
    text.cursor = l1, c1 + o1, l2, c2 + o2


# This override makes toggling comments PEP 8-compliant.
class TEXT_OT_comment_toggle(Default):
    def exec(self):
        ColumnRetainer.clear()
        toggle_comment()

        # Modifying ``TextLine.body`` which toggle_comment does somehow won't
        # tag the text dirty and Blender gladly reuses the old compiled module
        # the next time a script runs, so we do it ourselves.
        tag_text_dirty(_context.edit_text)
        ensure_cursor_view()
        return OPERATOR_FINISHED


def move_toggle(select: bool):
    text = _context.edit_text
    body = text.select_end_line.body
    line, column = text.cursor_focus

    indent = len(body) - len(body.lstrip())
    column = 0 if column == indent else indent
    text.cursor_set(line, character=column, select=select)


@cm
def restore_view():
    dna = _context.space_data.internal
    top = dna.top
    with (ctx := restore_offset()):
        yield ctx
    dna.top = top


@cm
def restore_offset():
    offsets = _context.space_data.runtime._offs_px
    offset = offsets[1]
    yield offset
    offsets[1] = offset


class ColumnRetainer:
    spaces = {}

    def __init__(self, text):
        self.up_column = text.cursor.start_column
        self.down_column = text.cursor.end_column

    def from_type(self, type: str):
        if type == 'PREVIOUS_LINE':
            return self.up_column
        return self.down_column

    @classmethod
    def clear(cls):
        for key in tuple(cls.spaces):
            space_data = key[0]
            if space_data == _context.space_data:
                del cls.spaces[key]
                break


class TEXT_OT_move(Default):
    def exec(self):
        # Accessing operator properties via C isn't trivial. We need to use
        # the window's eventstate.
        type = get_enum_type(self)
        if self.run_pre_hooks(type, select=False):
            return OPERATOR_CANCELLED

        if ret := retention_handled(self, type):
            return ret

        # Override HOME to toggle between line start and indent.
        if type == 'LINE_BEGIN':
            move_toggle(select=False)
            return OPERATOR_FINISHED


        with restore_view():
            ret = self.default()
        ensure_cursor_view()
        return ret


def retention_handled(self, type):
    key = _context.space_data, self.bl_idname

    if type in {'PREVIOUS_LINE', 'NEXT_LINE'}:
        spaces = ColumnRetainer.spaces
        if key not in spaces:
            spaces[key] = ColumnRetainer(_context.edit_text)

        line_pre = _context.edit_text.select_end_line_index

        with restore_view():
            ret = self.default()

        # Moving cursor beyond top or end of text moves only the column.
        # Don't retain the column in this case.
        line_post = _context.edit_text.select_end_line_index
        if line_pre == line_post:
            ColumnRetainer.spaces.pop(key, None)
            return

        cursor = _context.edit_text.cursor
        line = cursor.focus[0]
        column = spaces[key].from_type(type)

        # No selection.
        if key[1] == "text.move":
            cursor.set(line, column)
        else:
            cursor.set_focus(line, column)

        ensure_cursor_view()
        return ret

    else:
        ColumnRetainer.spaces.pop(key, None)
        return None


class TEXT_OT_move_select(Default):
    def exec(self):
        type = get_enum_type(self)
        if self.run_pre_hooks(type, select=True):
            return OPERATOR_CANCELLED

        if ret := retention_handled(self, type):
            return ret

        if type == 'LINE_BEGIN':
            move_toggle(select=True)
            return OPERATOR_FINISHED

        with restore_view():
            ret = self.default()
        ensure_cursor_view()
        return ret


class TEXT_OT_cursor_set(Default):
    def invoke(self):
        ColumnRetainer.clear()
        _call("TEXTENSION_OT_set_cursor", None, {}, 'INVOKE_DEFAULT')
        return OPERATOR_CANCELLED

    def exec(self):
        ColumnRetainer.clear()
        with restore_view():

            # Blender divides by zero on startup before the view has been
            # drawn. This is a cheap workaround.
            try:
                return self.default()
            except OSError:
                return OPERATOR_CANCELLED


class TEXT_OT_selection_set(Default):
    def invoke(self):
        ColumnRetainer.clear()
        with (ctx := restore_view()):
            self.event.mvaly -= ctx.result.result
            return self.default()

    # Same as invoke.
    modal = invoke


class TEXT_OT_select_word(Default):
    def invoke(self):
        _call("TEXTENSION_OT_set_cursor", None, {}, 'INVOKE_DEFAULT')
        return OPERATOR_CANCELLED


class TEXT_OT_scroll(Default):
    def invoke(self):
        event_type = event_type_to_string(self.event.type)
        if event_type == 'WHEELUPMOUSE':
            _call("TEXTENSION_OT_scroll_lines", None, {"lines": -3}, 'INVOKE_DEFAULT')

        elif event_type == 'WHEELDOWNMOUSE':
            _call("TEXTENSION_OT_scroll_lines", None, {"lines": 3}, 'INVOKE_DEFAULT')

        elif event_type == 'MIDDLEMOUSE':
            _call("TEXTENSION_OT_scroll_continuous", None, {}, 'INVOKE_DEFAULT')
        else:
            return self.default()
        return OPERATOR_CANCELLED


class TEXT_OT_select_all(Default):
    def exec(self):
        if self.run_pre_hooks():
            return OPERATOR_CANCELLED
        ColumnRetainer.clear()

        with restore_view():
            return self.default()


class TEXT_OT_new(Default):
    def exec(self):
        self.default()
        self.run_pre_hooks()
        return OPERATOR_CANCELLED


class TEXT_OT_unlink(Default):
    def exec(self):
        self.run_pre_hooks()
        self.default()
        return OPERATOR_CANCELLED


class TEXT_OT_save(Default):
    def invoke(self):
        ret = self.default()
        self.run_post_hooks(ret)
        return ret

    # Same as invoke.
    exec = invoke


class TEXT_OT_save_as(Default):
    def exec(self):
        ret = self.default()
        self.run_post_hooks(ret)
        return ret


class TEXT_OT_resolve_conflict(Default):
    def exec(self):
        ret = self.default()
        self.run_post_hooks()
        return ret


def dispatch_hooks_safe(hooks):
    try:
        for hook in hooks:
            hook()
    except Exception:
        pass


@cm
@unsuppress
def run_sync_hooks(instance: "UndoOverride"):
    # Print any tracebacks, but don't halt execution.
    dispatch_hooks_safe(instance._sync_pre_hooks)
    yield
    dispatch_hooks_safe(instance._sync_post_hooks)


class UndoOverride(Default):
    def __init_subclass__(cls):
        super().__init_subclass__()
        cls._poll_hooks = []
        # cls._exec_hooks = []
        cls._sync_pre_hooks = []
        cls._sync_post_hooks = []

    @classmethod
    def add_poll(cls, hook):
        cls._poll_hooks.insert(0, hook)

    @classmethod
    def remove_poll(cls, hook):
        cls._poll_hooks.remove(hook)

    def poll(self):
        for hook in self._poll_hooks:
            if result := hook():
                return result

        default_poll = self.default()

        # We must sync the texts.
        if default_poll:
            pass
        return default_poll

    def exec(self):
        if self.run_pre_hooks():
            # A hook handled it. bpy.ops.ed.undo/redo should do nothing.
            return OPERATOR_CANCELLED

        with run_sync_hooks(self):
            return self.default()


class ED_OT_undo(UndoOverride):
    pass


class ED_OT_redo(UndoOverride):
    pass


def apply_default_overrides():
    for cls in Default.operators:
        cls.apply_override()

    ED_OT_undo.apply_override()
    ED_OT_redo.apply_override()


def remove_default_overrides():
    for cls in Default.operators:
        cls.remove_override()

    ED_OT_undo.remove_override()
    ED_OT_redo.remove_override()

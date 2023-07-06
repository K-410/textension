from .utils import _system, _call, _context, factory

from types import ModuleType
import ctypes
import bpy
import sys
import re


@factory
def iter_brackets():

    # Anything not related to strings, brackets or comments.
    junk = '\t\n\r\x0b\x0c!$%&*+,-./:;<=>?@^_`|~ ' + \
           '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'  + \
           'abcdefghijklmnopqrstuvwxyz'

    from builtins import len, enumerate

    lstrip = str.lstrip
    rstrip = str.rstrip
    index = str.index
    split = str.split

    opener   = {")": "(", "]": "[", "}": "{"}

    def find_end(line, sub, end):
        while True:
            end = index(line, sub, end + 1)
            if line[end - 1] is "\\":
                a = line[:end]
                if (len(a) - len(rstrip(a, "\\"))) % 2:
                    continue
            return end + len(sub)
        

    PARENS  = 0
    SINGLE  = 1
    TRIPLE  = 2
    COMMENT = 3

    def iter_brackets(txt: str, strict=True):
        stack = []
        ml = False
        ml_start = (-1, -1)  # Start of a multi-line string

        for li, line in enumerate(split(txt, "\n")):
            end_pos = len(line)

            if ml:
                if ml not in line:
                    continue
                end = index(line, ml) + 3  # + 3 include bracket.
                yield (TRIPLE, ml_start, (li, end))
                ml = False
                pos = end
            else:
                pos = end_pos - len(lstrip(line, junk))

            while pos < end_pos:
                c = line[pos]
                if c in junk:
                    pos = end_pos - len(lstrip(line[pos:], junk))
                    continue
                elif c in "([{":
                    stack += [(li, pos, c)]
                elif c in ")]}" and stack and (b := stack[-1])[2] is opener[c]:
                    yield (PARENS, b[:2], (li, pos + 1))
                    del stack[-1]

                elif c in "\"\'\\":
                    try:
                        if line[pos + 2] is c is line[pos + 1]:
                            sub = line[pos:pos + 3]
                            tail = line[pos + 3:]
                            if sub not in tail:  # Start of multi-line string.
                                ml = sub
                                ml_start = li, pos
                                break  # Don't process line any further.

                            end = pos + index(tail, sub) + 6
                            yield (TRIPLE, (li, pos), (li, end))
                            pos = end
                            continue
                    except:  # IndexError but skip the lookup.
                        pass

                    try:
                        end = find_end(line, c, pos)

                    # End of string not found. Don't process line any further.
                    except ValueError:
                        if strict:
                            break
                        # Useful for detecting if we're in a string while typing.
                        end = len(line) + 1

                    yield (SINGLE, (li, pos), (li, end))
                    pos = end
                    continue

                elif c is "#":
                    yield (COMMENT, (li, pos), (li, pos + len(line[pos:]) + 1))
                    break  # Rest is comments.
                pos += 1

        if stack:
            pass
    def wrapper(text: str, strict=True):
        try:
            yield from iter_brackets(text, strict=strict)
        # The interpreter garbage collects the generator before it closes.
        # Possibly a quirk from being invoked from C code.
        except RuntimeError:
            pass
        return None

    return wrapper


def draw_syntax_footer(self, context):
    text = context.edit_text
    if not text:
        return None

    layout = self.layout
    row = layout.row(align=True)
    row.alignment = 'RIGHT'

    # Dynamically add spacers based on the region width, since each
    # spacer occupies some fixed real estate. 
    separator_spacer = row.separator_spacer
    for i in range(int(context.region.width // 300 * _system.wu * 0.05)):
        separator_spacer()

    stat = (f"Ln {text.select_end_line_index + 1}   "
            f"Col {text.select_end_character + 1}")

    lenseltext = len(text.selected_text)
    if lenseltext:
        text = "%s selected    " % lenseltext
        row.label(text=text)

    row.label(text=stat)
    # row.operator("textension.goto", text=stat, emboss=False)


def ensure_cursor_view(action: str = "lazy", smooth=True, threshold=2, speed=1.0):
    """Ensure that the text cursor is in view in the editor.

    ``lazy``     just enough to make the cursor visible.
    ``center``   centers the view on the cursor.
    ``smooth``   uses smooth scrolling.
    """

    if action not in {"center", "lazy"}:
        raise ValueError("Expected action to be 'lazy' or 'center'")

    text: bpy.types.Text = getattr(_context, "edit_text", None)

    if not isinstance(text, bpy.types.Text):
        return

    line, column = text.cursor_focus
    st = _context.space_data

    line_offset = st.runtime._offs_px[1] / st.line_height
    top = st.internal.top + line_offset
    rel_bottom = st.visible_lines - threshold

    from .operators import ScrollAccumulator
    if st in ScrollAccumulator.pool:
        top = ScrollAccumulator.pool[st].target_top

    y = st.region_location_from_cursor(0, 0)[1]
    y -= st.region_location_from_cursor(line, column)[1]
    cursor_line = (y // st.line_height)

    bottom = top + rel_bottom

    if action == "center":
        if line < top:
            cursor_line -= (st.visible_lines // 2) - 2
        else:
            cursor_line += (st.visible_lines // 2) - 2

    # Scroll up.
    if max(0, cursor_line - threshold) < top:
        lines = cursor_line - top - 2

    # Scroll down.
    elif cursor_line + threshold  > bottom:
        lines = cursor_line - bottom + 2
    else:
        return

    if smooth:
        # print("lines", lines)
        bpy.ops.textension.scroll_lines('INVOKE_DEFAULT', lines=lines, speed=speed)
    else:
        st.top += lines


def find_word_boundary(string, strict=False) -> int:
    """Find the first word boundary of a string.
    If ``strict`` is True, separators are considered boundaries.
    """
    pattern = r"\s{2,}|\s?\w+|\s?[^a-zA-Z0-9_ ]*"
    if strict:
        pattern = r"\s{2,}|\w+|[^a-zA-Z0-9_]"

    import re
    if ret := re.match(pattern, string):
        return ret.span()[1]
    return 0


def test_line_numbers(x, y):
    return
    # context, st, mrx, mry, _, rh = data
    st = _context.space_data
    rh = _context.region.height

    # XXX: It's nasty that we need this to support hot reloads.
    from .utils import prefs

    # TODO: This is making area resizing impossible.
    # if st.show_line_numbers and prefs().use_line_number_select:
    #     if x <= st.runtime.lpad_px - st.runtime.cwidth_px:
    #         if x >= 1 and y <= rh and y >= 0:
    #             # Inside line numbers.
    #             _context.window.cursor_set("DEFAULT")

    #             from .ui import _hit_test_widget
    #             _hit_test_widget.on_activate = click_line_numbers
    #             return _hit_test_widget


# Resizing a view with word wrapping can cause the text body to disappear
# upwards. This callback clamps the view.
def _clamp_viewrange(context=_context):
    st = context.space_data
    dna = st.internal
    max_top = max(0, st.drawcache.total_lines - (dna.runtime.viewlines // 2))
    if dna.top > max_top:
        dna.top = max_top

        # Call the text draw function to redraw immediately.
        from .utils import redraw_text
        redraw_text()


def get_line_number_from_cursor():
    st = _context.space_data
    region = _context.region
    lh = st.runtime.lheight_px
    rh = region.height

    # Approximate cursor position (line).
    mry = _context.window.mouse[1] - region.y
    line = st.top + int((rh - mry - 3) / lh)
    return max(0, get_actual_line_from_offset_line(st, line))


def click_line_numbers():
    line = get_line_number_from_cursor()
    bpy.ops.textension.line_number_select('INVOKE_DEFAULT', line=line)



def get_actual_line_from_offset_line(st, offset_idx):
    """
    Given a wrapped line index, return the real line index.
    """
    if not st.show_word_wrap:
        return offset_idx

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

    index = 0
    offset = 0
    for index, line in enumerate(lines[start:end], start):
        body = line.body
        if len(body) < c_max:
            continue
        c_start = 0
        c_end = c_max
        for c_pos, c in enumerate(body):
            if c_pos - c_start >= c_max:
                offset += 1
                if offset + index > end:
                    return index
                c_start = c_end
                c_end += c_max
            elif c in " -":
                c_end = c_pos + 1
    return index




# Get the absolute line/col from skipping screen space lines.
def skip_lines(context, numlines, line, col):
    st = context.space_data
    lines = st.text.lines
    if not st.show_word_wrap:
        return clamp(min(len(st.text.lines) - 1, line + numlines)), 0

    from collections import deque
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
    ret = (st.region.width - _system.wu - x) // cwidth_px
    if ret > 8:
        return ret
    return 8

def clamp(val, limit=0) -> int:
    return limit if val < limit else val


# More performant way of calling bpy.ops.textenison.scroll2.
def scroll(*args, **kwargs):
    return _call("TEXTENSION_OT_scroll2", {}, kwargs, 'INVOKE_DEFAULT')


def test_vanilla_scrollbar(x, y):
    return
    # context, st, mrx, mry, rw, rh = data
    rw = _context.region.width

    scrollbar_edge_x = rw - (0.6 * system.wu)
    if x >= scrollbar_edge_x:
        # There's a dead-zone 2 pixels beyond the vanilla scrollbar where
        # the cursor goes back to an i-beam. We can't activate the operator
        # in this area, but we can at least fix the cursor.
        if x < rw - 2:
            _context.window.cursor_set("DEFAULT")
            
            # TODO: Fix this. Bad design. Just needs to work for now.
            from .ui import _hit_test_widget
            _hit_test_widget.on_activate = lambda: bpy.ops.text.scroll_bar('INVOKE_DEFAULT')
            return _hit_test_widget


def scroll_to_cursor(action: str = "lazy"):
    st = _context.space_data
    region = _context.region
    rh = region.height
    line, column = st.text.cursor_focus
    y = st.region_location_from_cursor(line, column)[1]
    y += 4  # some weird pixel offset
    lh_px = st.runtime.lheight_px
    y += lh_px
    view_lines_f = rh / st.runtime.lheight_px

    middle = int(view_lines_f * 0.5)
    # top = st.top
    rel_lines = round(view_lines_f - (y / lh_px))

    if rel_lines < 0 or rel_lines >= int(view_lines_f):
        bpy.ops.textension.scroll_lines('INVOKE_DEFAULT', lines=rel_lines - middle, exclusive=True)

    # st.top += rel_lines


# def cursor_history_add(context, *args, **kwargs):
#     textman(context.edit_text).cursor.add(*args, **kwargs)


# def cursor_history_poll(context, forward=False):
#     cursor = textman(context.edit_text).cursor
#     if forward:
#         return cursor.head
#     return len(cursor.history) - 1 > cursor.head


# def cursor_history_step(context, direction):
#     tm = textman(context.edit_text)
#     if direction == 'FORWARD':
#         return tm.cursor.step_forward()
#     return tm.cursor.step_back()


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
        event, rw - (_system.wu * 0.6), rw, 0, region.height)


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
    # row.operator("textension.cursor_history",
    #              text="Next Cursor").dir_ = 'FORWARD'
    # row.active = cursor_history_poll(context, forward=True)
    # row = layout.row()
    # row.operator(
    #     "textension.cursor_history", text="Previous Cursor").dir_ = 'BACK'
    # row.active = cursor_history_poll(context)


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

def _make_indent(st, level):
    if st.text.indentation == 'SPACES':
        return " " * (st.tab_width * level)
    return "\t" * level


def _get_indent_level(string):
    st = _context.space_data
    text = st.text
    if text.indentation == 'SPACES':
        level = (len(string) - len(string.lstrip(" "))) // st.tab_width
    else:
        level = 0
        while string[level:level + 1] == "\t":
            level += 1
    return level


def calc_indent_str():
    st = _context.space_data
    text = st.text
    
    line = text.cursor_start_line
    leading = text.get_leading_body()
    level = _get_indent_level(leading)
    import re
    if re.match(r"^[ \t]*?\b(?:def|if|for|class|else|elif"
                r"|with|while|try|except|finally)\b.*?\:", leading):
        level += 1
    elif re.match(r"^[ \t]*?\b(?:pass|break|continue|raise|return)\b", leading):
        level -= 1
    else:
        return " " * line.indent
    return text.indent_string * level



def _compute_indent_change(string: str) -> int | None:
    if re.compile(
        r"^[ \t]*?\b(?:def|if|for|class|else|elif"
        r"|with|while|try|except|finally)\b.*?\:").match(string):
            return 1
    elif re.compile(
        r"^[ \t]*?\b(?:pass|break|continue|raise|return)\b").match(string):
            return -1
    return 0

dedent_kw = re.compile(r"^[ \t]*?\b(?:pass|break|continue|raise|return)\b")



def to_bpy_float(value):
    return ctypes.c_float(value).value


def serialize_factory():
    """Store kmi data as string."""
    from json import dumps, loads
    from operator import attrgetter
    getattrs = attrgetter("type", "value", "alt", "ctrl", "shift",
                          "any", "key_modifier", "oskey", "active")

    return (lambda kmi: dumps(getattrs(kmi)),
            lambda string: tuple(loads(string)))


def iadd_default(obj, key, default, value):
    """In-place addition with a default value."""
    ivalue = getattr(obj, key, default) + value
    setattr(obj, key, ivalue)
    return ivalue


def setdefault(obj, key, value):
    """Simple utility to set a default attribute on any object."""
    try:
        value = getattr(obj, key)
    except:
        setattr(obj, key, value)
    return value


def tabs_to_spaces(string, tab_width):
    while "\t" in string:
        tmp = " " * (tab_width - string.find("\t") % tab_width)
        string = string.replace("\t", tmp, 1)
    return string


def setdef(obj, attr, val):
    currval = getattr(obj, attr, Ellipsis)
    if currval is Ellipsis:
        setattr(obj, attr, val)
        return val
    return currval



def window_from_region(region: bpy.types.Region):
    for win in _context.window_manager.windows:
        if win.screen == region.id_data:
            return win


def this_module() -> ModuleType:
    """Returns the module of the caller."""
    return sys.modules.get(sys._getframe(1).f_globals["__name__"])


def clamp(val, a, b):
    if val < a:
        return a
    elif val > b:
        return b
    return val


# Linear value to srgb. Assumes a 0-1 range.
def lin2srgb(lin):
    if lin > 0.0031308:
        return 1.055 * (lin ** (1.0 / 2.4)) - 0.055
    return 12.92 * lin


def renum(iterable, len=len, zip=zip, range=range, reversed=reversed):
    """
    Reversed enumerator starting at the size of the iterable and decreasing.
    """
    lenit = len(iterable)
    start = lenit - 1
    return zip(range(start, start - lenit, -1), reversed(iterable))


# Default argument is bound when TextensionPreferences is registered.
def prefs(preferences=None) -> bpy.types.AddonPreferences:
    return preferences



# Cache dict with its __missing__ set to "func". "args" is used by "func" and
# is changed by calling "params_set", which invalidates the cache.
# def defcache(func, *args, fwd=False, unpack=False):
#     all_caches = setdef(defcache, "all_caches", [])
#     if unpack:
#         def fallback(self, key, args=args):
#             self[key] = func(*key, *args)
#             return self[key]
#     else:
#         def fallback(self, key, args=args):
#             self[key] = func(key, *args)
#             return self[key]

#     class Cache(dict):
#         __missing__ = fallback
#         @staticmethod
#         def params_set(*args):
#             clear()
#             fallback.__defaults__ = (args,)
#     cache = Cache()
#     clear = cache.clear

#     if fwd:
#         if not isinstance(args, tuple):
#             args = (args,)
#         args += (cache,)

#     cache.params_set(*args)
#     all_caches.append(cache)
#     return cache


def tag_modified(operator: bpy.types.Operator):
    """
    Calling this ensures that Textension operators that use TextUndo tags
    the blend file as dirty.
    """
    if not bpy.data.is_dirty:
        bpy.ops.ed.undo_push(message=operator.bl_label)


# def _redraw_now(type='DRAW') -> None:
#     """
#     Redraw immediately. Only use when the alternative isn't acceptable.
#     """
#     stdout = sys.stdout
#     import io
#     sys.stdout = io.StringIO()
#     try:
#         bpy.ops.wm.redraw_timer(type=type, iterations=1)
#     finally:
#         sys.stdout = stdout
#     return None


def get_normalized_units() -> float:
    return _system.wu * 0.05

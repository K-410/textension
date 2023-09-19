"""This module implements various operators."""

from .utils import TextOperator, km_def, _call, _system, _context, text_poll
from .core import iter_brackets, find_word_boundary
from .ui import get_mouse_region
from time import perf_counter
from .btypes.defs import ST_SCROLL_SELECT
from . import utils
from . import prefs

import bpy


def find_brackets(string, left, right):
    for type, start, end in iter_brackets(string):
         if start < left and right < end:
              return type, *start, *end
    return None, -1, -1, -1, -1


class TEXTENSION_OT_expand_to_brackets(TextOperator):
    """Expand selection to closest brackets"""
    km_def("Text", 'A', 'PRESS', alt=True, repeat=True)
    poll = text_poll

    def execute(self, context):
        SINGLE  = 1
        TRIPLE  = 2
        COMMENT = 3
        text = context.edit_text
        cur = text.cursor

        l1, c1 = cur.start
        l2, c2 = cur.end
        type, t1, k1, t2, k2 = find_brackets(
             text.as_string(), (l1, c1), (l2, c2))

        pad = 3 if type is TRIPLE else 1

        # For performance reasons ``iter_brackets`` doesn't parse brackets
        # within in strings and comments, so apply refinement here.
        while type in (SINGLE, TRIPLE, COMMENT):
            last = type, t1, k1, t2, k2

            string = text.string_from_indices(t1, 0, t2, k2 - pad)
            string = (" " * (k1 + pad)) + string[k1 + pad:]

            if type == COMMENT:
                 string = string.replace("#", " ")

            type, j1, k1, j2, k2 = find_brackets(
                 string, (l1 - t1, c1), (l2 - t1, c2))
            if type is not None:
                t2 = t1 + j2
                t1 = t1 + j1
                pad = 3 if type is TRIPLE else 1
            else:
                type, t1, k1, t2, k2 = last
                break

        if type in (COMMENT, None):
             return {'CANCELLED'}

        # Subtract the brackets themselves from the range.
        k1 += pad
        k2 -= pad

        # Get the highest overstep above zero.
        overstep = max((k1 - c1) * int(l1 == t1),
                       (c2 - k2) * int(l2 == t2), 0)

        # Expand when the selection is symmetric.
        if (l1 == t1 and l2 == t2) and (k1 - c1 == c2 - k2) and \
           (c1 <= k1 and c2 >= k2):
               overstep += 1

        text.select_set(t1, k1 - overstep, t2, k2 + overstep)
        return {'CANCELLED'}


def cursor_isect_xy(event, x1, x2, y1, y2):
    mrx = event.mouse_region_x
    mry = event.mouse_region_y
    if x1 < mrx:
        if mrx <= x2:
            if y1 < mry:
                return mry < y2
    return False


def in_scroll():
    if region := _context.region:
        x, y = get_mouse_region()
        rw = region.width
        if 0 <= x - (rw - (_system.wu * 0.6)) <= rw:
            return 0 <= y - rw <= rw
    return False


def hit_test_cursor(x: int, y: int):
    text = _context.edit_text
    old = text.cursor.copy()
    set_cursor(x, y)
    focus = text.cursor_focus
    text.cursor = old
    return focus


def set_cursor(x, y):
    _call('TEXT_OT_cursor_set', {}, {'x': x, 'y': y}, 'EXEC_DEFAULT')


# Called internally by the overridden TEXT_OT_select_word.
class TEXTENSION_OT_snap_select(TextOperator):
    bl_options = {'INTERNAL'}
    poll = text_poll

    def modal(self, context, event):
        text = context.edit_text
        if event.type in {'MOUSEMOVE', 'TIMER'}:
            rt = context.space_data.runtime
            x, y = get_mouse_region()

            # Support unlocked offsets.
            y -= rt.scroll_ofs_px[1]

            # Forward selection needs a character's width offset.
            if hit_test_cursor(x, y) >= self.init_focus:
                x -= rt.cwidth_px

            set_cursor(x, y)
            _call('TEXTENSION_OT_select_word', {}, {}, 'INVOKE_DEFAULT')

            icurl, icurc, iselc = self.init_range
            curl, curc, sell, selc = text.cursor
            if (curc < icurc and curl == icurl) or curl < icurl:
                icurc, selc = iselc, curc
            text.select_set(icurl, icurc, sell, selc)
        elif event.type in {'LEFTMOUSE', 'RIGHTMOUSE', 'ESC'}:
            context.window_manager.event_timer_remove(self.timer)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        # Skip if cursor is scrollbar region.
        if in_scroll():
            return {'PASS_THROUGH'}

        # Set initial selection range.
        _call('TEXTENSION_OT_select_word', {}, {})
        icurl, icurc, isell, iselc = context.edit_text.cursor2
        self.init_range = icurl, icurc, iselc
        self.init_focus = isell, iselc
        context.window_manager.modal_handler_add(self)
        self.timer = context.window_manager.event_timer_add(1e-3, window=context.window)
        return {'RUNNING_MODAL'}


class TEXTENSION_OT_line_select(TextOperator):
    bl_options = {'INTERNAL'}
    poll = text_poll

    def invoke(self, context, event):
        cursor = context.edit_text.cursor
        self.start_line = cursor.focus_line
        cursor.focus = self.start_line + 1, 0
        cursor.anchor = self.start_line, 0
        context.window_manager.modal_handler_add(self)
        self.timer = context.window_manager.event_timer_add(1e-3, window=context.window)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'LEFTMOUSE', 'RIGHTMOUSE', 'ESC', 'WINDOW_DEACTIVATE'}:
            context.window_manager.event_timer_remove(self.timer)
            return {'CANCELLED'}

        elif event.type in {'MOUSEMOVE', 'TIMER'}:
            x, y = get_mouse_region()
            offset = context.space_data.runtime.scroll_ofs_px[1]

            _call("TEXT_OT_cursor_set", {}, {"x": x, "y": y - offset}, 'EXEC_DEFAULT')

            text = context.edit_text

            cursor = text.cursor
            if cursor.focus_line >= self.start_line:
                column = 0

                # Focus is on the last line, so select the body since we can't
                # actually select anything lower.
                if text.select_end_line == text.lines[-1]:
                    column = len(text.select_end_line.body)

                cursor.anchor = self.start_line, 0
                cursor.focus = cursor.focus_line + 1, column

            else:
                cursor.anchor = self.start_line + 1, 0
                cursor.focus = cursor.focus_line, 0
        return {'RUNNING_MODAL'}


class TEXTENSION_OT_select_word(TextOperator):
    bl_options = {'INTERNAL'}
    poll = text_poll

    def execute(self, context):
        text = context.edit_text
        string = text.select_end_line.body
        col = text.selc

        text.curl = text.sell

        forward = string[col:]
        i = find_word_boundary(forward, strict=True)
        text.selc += i

        # Not correct for multi-byte, but we don't really care here.
        backward = string[:col + i][::-1]
        text.curc = col - find_word_boundary(backward, strict=True) + i
        return {'CANCELLED'}


def is_scrolling():
    return _context.space_data in ScrollAccumulator.pool


class ScrollAccumulator:
    line_offset: int
    prev_lines:  int

    start_top:  int
    target_top: int
    max_top:    int
    buffer:     dict
    clamp:      int

    rel_lines: int

    timer = None
    pool  = {}      # Accumulator instances, 1 per space data

    def __new__(cls, job, st: bpy.types.SpaceTextEditor, lines):
        if st not in cls.pool:
            instance = super().__new__(cls)

            if not cls.pool:
                cls.timer = _context.window_manager.event_timer_add(1e-3, window=_context.window)
                utils.add_draw_hook(ScrollAccumulator.on_redraw)

            instance.st_dna = st.internal
            instance.st_rt = st.runtime
            instance.region_internal = _context.region.internal
            instance.buffer = {}
            instance.rel_lines = 0
            # The starting offset into next line.
            instance.line_offset = instance.st_rt.scroll_ofs_px[1] / st.runtime.lheight_px
            instance.prev_lines = 0
            instance.clamp = 0
            instance.start_top = st.internal.top
            cls.pool[st] = instance
        return cls.pool[st]

    def __init__(self, job, st: bpy.types.SpaceTextEditor, lines):
        st.flags |= ST_SCROLL_SELECT
        self.buffer[job] = 0.0
        self.rel_lines += lines
        self.target_top = self.rel_lines + self.start_top

    def set(self, job, lines):
        if self.clamp > 0 and (self.clamp == 2 or lines < 0):
            job.finished = True
        else:
            self.buffer[job] = lines
            self.region_internal.do_draw = 1

    # Scrolling is application-driven via the draw callback, but the redraw
    # loop still needs a timer.
    def on_redraw(self=None):
        if self := ScrollAccumulator.pool.get(st := _context.space_data):
            linesf = sum(self.buffer.values()) + self.line_offset
            offset_factor = 0.0
            top = self.st_dna.top
            dest_top = (linesf - self.prev_lines) + top

            # Compute max top in case lines were added while scrolling.
            max_top = calc_max_top(st)
            if dest_top < 0:
                if top != 0:
                    self.st_dna.top = 0
                self.clamp = 1

            elif dest_top > max_top:
                if top != int(max_top):
                    self.st_dna.top = int(max_top)
                offset_factor = max_top % 1.0
                self.clamp = 2
            else:
                lines = linesf.__floor__()
                self.st_dna.top += lines - self.prev_lines
                self.prev_lines = lines
                self.clamp = 0
                offset_factor = linesf - lines
            self.st_rt.scroll_ofs_px[1] = int(offset_factor * self.st_rt.lheight_px)

    def unregister(self, job):
        self.line_offset += self.buffer.pop(job)
        if not self.buffer:
            del self.pool[next(k for k, v in self.pool.items() if v is self)]
        if not self.pool:
            utils.remove_draw_hook(ScrollAccumulator.on_redraw)
            _context.window_manager.event_timer_remove(self.timer)
            self.region_internal.do_draw = 1


class TEXTENSION_OT_scroll_lines(TextOperator):
    km_def("Text", 'WHEELUPMOUSE', 'PRESS', alt=True)
    km_def("Text", 'WHEELDOWNMOUSE', 'PRESS', alt=True)

    bl_options = {'INTERNAL'}
    poll = text_poll

    lines: bpy.props.FloatProperty()
    speed: bpy.props.FloatProperty(default=1.0, min=0.1)

    def invoke(self, context, event):
        lines = self.lines

        if lines == 0:
            lines = prefs.num_scroll_lines

            if event.type == 'WHEELUPMOUSE':
                lines *= -1

            # We have no idea which direction to scroll.
            elif event.type != 'WHEELDOWNMOUSE':
                return {'CANCELLED'}

        if event.alt and prefs.use_alt_scroll_multiplier:
            lines *= 3

        self.lines = lines
        self.accum = ScrollAccumulator(self, context.space_data, self.lines)

        SCROLL_TIME = 0.1 / self.speed

        self.finished = False
        self.coeff = 1.0 / SCROLL_TIME
        self.start = perf_counter()
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        # Generate mouse move for selection operators.
        context.window.cursor_warp(event.mouse_x, event.mouse_y)

        if self.finished:
            # End modal on a timer so we don't eat the input event.
            if event.type == 'TIMER':
                self.accum.unregister(self)
                return {'CANCELLED'}

        elif event.type == 'TIMER':
            f = (perf_counter() - self.start) * self.coeff

            if f < 1.0:
                f = f * f * (3 - 2 * f)
            else:
                f = 1.0
                self.finished = True

            self.accum.set(self, self.lines * f)
        return {'PASS_THROUGH'}


def calc_max_top(st):
    view_half = _context.region.height / st.runtime.lheight_px * 0.5
    max_top = st.drawcache.total_lines - view_half
    if max_top < 0:
        return 0
    return max_top


class TEXTENSION_OT_scroll_continuous(TextOperator):
    bl_options = {'INTERNAL'}
    poll = text_poll
    def invoke(self, context, event):
        if in_scroll():
            return {'PASS_THROUGH'}

        y = get_mouse_region()[1]
        st = context.space_data
        offsets = st.runtime.scroll_ofs_px
        internal = context.region.internal

        RGN_DRAW = 1
        speed_mult = 30

        st.flags |= ST_SCROLL_SELECT
        line_height = st.runtime.lheight_px
        line_factor = 1 / line_height

        y_remainder = 0.0
        t = perf_counter()
        max_top = calc_max_top(st)

        def scroll(px):
            nonlocal t, y_remainder
            d = perf_counter()
            base = px * line_factor * (d - t) * speed_mult
            y_offset_ratio = offsets[1] * line_factor
            result = base + y_offset_ratio
            lines = int(result)
            y = ((result - lines) * line_height) + y_remainder

            if base < 0.0:
                if y <= 0:
                    if st.top == 0:
                        offsets[1] = 0
                        return
                    y %= line_height
                    lines -= 1
            elif st.top >= max_top:
                offsets[1] = 0
                return
            y_remainder = y % 1.0
            offsets[1] = int(y)
            if lines:
                st.top += lines
            t = d

        def inner_modal(event):
            if event.value == 'RELEASE':
                # Snap to closest line.
                context.window_manager.event_timer_remove(timer)
                context.window.cursor_modal_restore()
                context.region.tag_redraw()
                return {'FINISHED'}

            elif event.type == 'TIMER':
                delta = y - event.mouse_region_y
                if delta < -15:
                    delta += 10
                elif delta > 15:
                    delta -= 10
                else:
                    return {'RUNNING_MODAL'}

                # Gradually increase scroll by distance.
                scroll(delta * (abs(delta) ** 1.1) * 0.05 * 0.1 * 0.2)
            internal.do_draw = RGN_DRAW
            return {'RUNNING_MODAL'}

        self.inner_modal = inner_modal
        context.window.cursor_modal_set("SCROLL_Y")
        context.window_manager.modal_handler_add(self)
        timer = context.window_manager.event_timer_add(1e-5, window=context.window)
        return {'RUNNING_MODAL'}

    # Handle modal in closed function.
    def modal(self, context, event):
        return self.inner_modal(event)

class clicks_counter:
    click_time = 0
    clicks     = 0
    xy         = (-10000, -10000)

    def get_and_track(self):
        from time import monotonic

        result = 1
        margin = int(_system.wu * 2 * 0.05)

        px, py = self.xy
        x, y   = get_mouse_region()

        ms = _context.preferences.inputs.mouse_double_click_time

        if px - margin <= x <= px + margin and \
           py - margin <= y <= py + margin and \
           monotonic() - (ms / 1000) <= self.click_time:
                result = max(1, self.clicks + 1)

        self.xy = x, y
        self.clicks = result
        self.click_time = monotonic()
        return self.clicks

    def get(self):
        return self.clicks

    def reset(self):
        self.click_time = 0

clicks = clicks_counter()


class TEXTENSION_OT_set_cursor(TextOperator):
    bl_options = {'INTERNAL'}
    poll = text_poll
    def invoke(self, context, event):
        count = clicks.get_and_track()
        from textension.overrides.default import restore_offset

        if count == 1:
            with (ctx := restore_offset()):
                x, y = get_mouse_region()
                set_cursor(x, y - ctx.result)
            _call("TEXT_OT_selection_set", None, {}, 'INVOKE_DEFAULT')
        elif count == 2:
            _call("TEXTENSION_OT_snap_select", None, {}, 'INVOKE_DEFAULT')
        elif count == 3:
            _call("TEXTENSION_OT_line_select", None, {}, 'INVOKE_DEFAULT')
        elif count == 4:
            _call("TEXT_OT_select_all", None, {}, 'EXEC_DEFAULT')

        return {'CANCELLED'}



classes = (
    TEXTENSION_OT_expand_to_brackets,
    TEXTENSION_OT_line_select,
    TEXTENSION_OT_scroll_continuous,
    TEXTENSION_OT_scroll_lines,
    TEXTENSION_OT_select_word,
    TEXTENSION_OT_set_cursor,
    TEXTENSION_OT_snap_select,
)
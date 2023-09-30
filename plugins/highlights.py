"""This module implements highlights in the text editor."""

import bpy
import gpu
from textension.utils import _context, _system, map_contains, namespace
from gpu.types import GPUVertBuf, GPUBatch, GPUVertFormat
from itertools import repeat, islice, compress, count
from textension import ui, utils
from functools import partial
from operator import mul, floordiv, sub, add
from sys import maxsize as int_max

prefs: "TEXTENSION_PG_highlights" = None


vert = """
uniform mat4 ModelViewProjectionMatrix;
in vec2 pos;

void main() {
    gl_Position = ModelViewProjectionMatrix * vec4(pos, 0.0, 1.0);
}
"""

frag = """
uniform vec4 color = vec4(1.0, 1.0, 1.0, 0.2);
uniform float clip_left = 0.0;
out vec4 fragColor;

void main() {
    if (gl_FragCoord.x < clip_left) {
        discard;
        return;
    }
    fragColor = color;
}
"""

runtime = namespace(shader=None, fmt=None, bind=None, upload=None)


@utils.inline
def map_mul(sequence1, sequence2):
    return partial(map, mul)


@utils.inline
def map_sub(sequence1, sequence2):
    return partial(map, sub)


@utils.inline
def map_floordiv(sequence1, sequence2):
    return partial(map, floordiv)


@utils.inline
def map_add(sequence1, sequence2):
    return partial(map, add)


def to_batch_and_draw(points):
    tris = []
    for x1, x2, y1, y2 in points:
        p1 = x1, y2
        p3 = x2, y1
        tris += p1, (x1, y1), p3, p3, p1, (x2, y2)

    vbo = GPUVertBuf(runtime.fmt, len(tris))
    vbo.attr_fill("pos", tris)
    GPUBatch(type="TRIS", buf=vbo).draw(runtime.shader)


# Calculate true top when word wrap is turned on
def calc_top(lines, line_height, region_height, wrap_offset, max_width):
    if max_width < 8:
        max_width = 8

    for idx, line in enumerate(lines):
        wrap_offset -= line_height

        if wrap_offset < region_height:
            return idx

        if len(line) < max_width:
            continue

        pos = 0
        start = 0
        end = max_width
        for pos, c in enumerate(line):
            if pos - start >= max_width:
                wrap_offset -= line_height
                if wrap_offset < region_height:
                    return idx
                start = end
                end += max_width
            elif c in " -":
                end = pos + 1
    return 0


def get_scrollbar_points(st, substr, wu, vspan_px, rw, rh, lineh, strings):
    x1, x2 = utils.get_scrollbar_x_offsets(rw)

    # TODO: These offsets are for vanilla scrollbar.
    top_margin = int(0.4 * wu)
    pxavail = rh - top_margin * 2
    wrh = wrhorg = float((vspan_px // lineh) + 1)  # wrap lines
    scrolltop = rh - (top_margin + 2)

    vispan = st.top + st.visible_lines
    blank_lines = st.visible_lines // 2
    if wrh + blank_lines < vispan:
        blank_lines = vispan - wrh

    j = 2.0 + wrhorg / len(strings) * pxavail
    y_points = compress(count(1), map_contains(strings, repeat(substr)))
    y_points = map_mul(repeat(j), y_points)
    y_points = map_floordiv(y_points, repeat(wrh + blank_lines))
    y_points = map_sub(repeat(scrolltop), y_points)
    y_points = set(y_points)
    return zip(repeat(x1), repeat(x2), y_points, map_add(y_points, repeat(2)))


def get_match_points(st, substr, start, end):
    is_wrapped = st.show_word_wrap
    line_height = st.runtime.lheight_px
    drawcache = st.drawcache
    wunits = _system.wu
    points = []
    scrollpts = []
    text = st.text

    src = text.as_string()
    if not prefs.case_sensitive:
        src = src.lower()

    lines = src.splitlines()

    loc = st.region_location_from_cursor
    first_y = loc(0, 0)[1]
    x_offset = cw = drawcache.cwidth_px[0]

    if st.show_line_numbers:
        x_offset += cw * (len(repr(len(lines))) + 2)

    base_x_offset = x_offset
    region = _context.region
    rh, rw = region.height, region.width

    max_width = int_max

    top = st.top
    y_offset = first_y - (top * line_height) - (rh - line_height)

    vspan_px = (drawcache.total_lines * line_height) - line_height

    if is_wrapped:
        # Maximum displayable characters in editor
        max_width = (rw - wunits - x_offset) // cw
        if max_width < 8:
            max_width = 8

        top = calc_top(lines, line_height, rh, first_y + y_offset, max_width)

    elif st.left:
        x_offset -= st.left * cw

    y_offset = st.offsets.y - y_offset

    # Screen coord tables for fast lookup of match positions
    x_table = range(0, cw * max_width, cw)
    y_top = loc(top, 0)[1]
    y_bottom = -line_height
    y_table = range(y_top, min(0, y_top - vspan_px), y_bottom)

    y = 0
    total_lines = 0
    wrap_offset = 0
    wrap_count  = 0

    # Generate points for scrollbar highlights
    if prefs.show_in_scrollbar:
        scrollpts = get_scrollbar_points(st, substr, wunits, vspan_px, rw, rh, line_height, lines)

    bottom = top + st.visible_lines + 4
    curl = text.current_line_index
    if top <= curl < bottom:
        body = lines[curl]
        body = body[:start] + ("\x00" * (end - start)) + body[end:]
        lines[text.current_line_index] = body

    strlen = len(substr)
    width = cw * strlen

    # Generate points for text highlights
    for line in islice(lines, top, bottom):
        wrap_count = 0
        wrap_indices = []
        linelen = len(line)

        # Nothing to wrap.
        if linelen <= max_width:
            wrap_end = linelen

        else:
            wrap_start = 0
            wrap_end = max_width
            for idx, char in enumerate(line):
                if idx - wrap_start >= max_width:
                    wrap_indices += zip(range(wrap_end - wrap_start), repeat(wrap_count))
                    wrap_count += 1
                    wrap_start = wrap_end
                    wrap_end += max_width
                elif char in " -":
                    wrap_end = idx + 1
            wrap_end = linelen - wrap_start

        wrap_indices += zip(range(wrap_end), repeat(wrap_count))

        # Find matches.
        if substr in line and linelen < 65536:
            i = line.index(substr, 0)

            while i is not -1:
                # Region coords for wrapped char/line by match index
                wrap_char, wrap_line = wrap_indices[i]
                y = y_table[wrap_line] - wrap_offset

                if y <= y_bottom:
                    break

                x = x_table[wrap_char]
                x2 = x + width
                wrap_index = strlen + i

                if wrap_line != wrap_indices[wrap_index - 1][1]:
                    for wrap_char, wrap_line in islice(wrap_indices, i, wrap_index):
                        next_y = y_table[wrap_line] - wrap_offset
                        if next_y != y:
                            x2 = x_table[wrap_char - 1] + cw
                            points += (x, x2, y, y + line_height),
                            x = x_table[wrap_char]
                            y = next_y
                    x2 = x_table[wrap_char] + cw

                points += (x, x2, y, y + line_height),
                i = line.find(substr, wrap_index)

        total_lines += wrap_count + 1
        wrap_offset = line_height * total_lines

    rep_x = repeat(x_offset)
    rep_y = repeat(y_offset)
    points = map(map_add, points, zip(rep_x, rep_x, rep_y, rep_y))
    return base_x_offset, points, scrollpts


@utils.inline
def draw_match():

    @utils.inline
    def set_alpha_blend():
        return partial(gpu.state.blend_set, 'ALPHA')

    @utils.inline
    def set_additive_blend():
        return partial(gpu.state.blend_set, 'ADDITIVE')

    def draw_match():
        st = _context.space_data
        text = st.text

        if not text or text.current_line != text.select_end_line:
            return

        start, end = text.cursor_columns
        if start > end:
            start, end = end, start

        string = text.selected_text

        if not string.strip() or len(string) < prefs.minimum_length:
            return

        if not prefs.case_sensitive:
            string = string.lower()

        clip_left, points, scroll_points = get_match_points(st, string, start, end)

        runtime.bind()
        set_alpha_blend()

        # Draw highlights in scrollbar.
        if prefs.show_in_scrollbar:
            runtime.upload("color", prefs.color_scroll)
            to_batch_and_draw(scroll_points)

        set_additive_blend()

        # Draw highlights in text view.
        runtime.upload("clip_left", clip_left)
        runtime.upload("color", prefs.color_background)
        to_batch_and_draw(points)
    return draw_match


@utils.tag_userdef_modified_wrapper
def update_colors(self, _):
    col_attrs = "color_background", "color_line", 'color_scroll'
    if self.color_preset != 'CUSTOM':
        for source, target in zip(self.colors[self.color_preset], col_attrs):
            setattr(self, target, source)


class TEXTENSION_PG_highlights(bpy.types.PropertyGroup):
    # Color presets for highlights.
    colors = {
        "BLUE": ((0.2, 0.3, 0.4, 0.5), (0.2, 0.4, 0.6, 0.5), (0.1, 0.6, 1.0, 0.5)),
        "YELLOW": ((0.3, 0.25, 0.0, 1.0), (0.5, 0.5, 0.0, 1.0), (1.0, 0.8, 0.1, 0.4)),
        "GREEN": ((0.01, 0.21, 0.01, 1.0), (0.2, 0.5, 0.2, 1.0), (0.1, 1.0, 0.0, 0.4)),
        "RED": ((0.33, 0.08, 0.08, 1.0), (0.6, 0.3, 0.3, 1.0), (1.0, 0.2, 0.2, 0.5))}

    show_in_scrollbar: bpy.props.BoolProperty(
        description="Show match highlights in scrollbar",
        name="Show in Scrollbar",
        default=True,
        update=utils.tag_userdef_modified,
    )
    minimum_length: bpy.props.IntProperty(
        description="Don't trigger highlights below this",
        name='Minimum Length',
        default=2,
        min=1,
        max=4,
        update=utils.tag_userdef_modified,
    )
    case_sensitive: bpy.props.BoolProperty(
        description='Case Sensitive',
        name='Use Case Sensitive',
        default=False,
        update=utils.tag_userdef_modified,
    )
    color_prop_kw = {
        'subtype': 'COLOR',
        'size': 4,
        'min': 0,
        'max': 1,
        'update': utils.tag_userdef_modified
    }
    color_background: bpy.props.FloatVectorProperty(
        name='Background Color',
        description='Background color',
        default=colors['BLUE'][0],
        **color_prop_kw
    )
    color_scroll: bpy.props.FloatVectorProperty(
        name="Scrollbar Color",
        description="Scroll highlight opacity",
        default=colors['BLUE'][2],
        **color_prop_kw
    )
    color_preset: bpy.props.EnumProperty(
        description="Highlight color presets", name="Presets", default="BLUE",
        update=update_colors,
        items=(("BLUE", "Blue", "", 1),
               ("YELLOW", "Yellow", "", 2),
               ("GREEN", "Green", "", 3),
               ("RED", "Red", "", 4),
               ("CUSTOM", "Custom", "", 5)))


def draw_settings(prefs, context, layout):
    self = prefs.highlights
    layout.use_property_split = True
    layout.use_property_decorate = False

    layout.prop(self, "case_sensitive")
    layout.prop(self, "show_in_scrollbar")

    layout.separator()

    col = layout.column(align=True)
    col.prop(self, "minimum_length")

    layout.separator()

    layout.prop(self, "color_preset")

    layout.separator()

    if self.color_preset == 'CUSTOM':
        col = layout.column(align=True)
        col.prop(self, "color_background")
        col.prop(self, "color_scroll")


def add_runtime():
    shader = gpu.types.GPUShader(vert, frag)
    fmt = GPUVertFormat()
    runtime.update(
        shader=shader,
        fmt=fmt,
        bind=shader.bind,
        upload=shader.uniform_float,
    )
    fmt.attr_add(id="pos", comp_type="F32", len=2, fetch_mode="FLOAT")
    del shader, fmt


def enable():
    from textension.prefs import add_settings

    utils.register_class(TEXTENSION_PG_highlights)

    global prefs
    prefs = add_settings(TEXTENSION_PG_highlights)

    add_runtime()
    # The new editor scrollbar uses draw index 10. This draws on top.
    ui.add_draw_hook(draw_match, draw_index=11)


def disable():
    from textension.prefs import remove_settings

    utils.unregister_class(TEXTENSION_PG_highlights)
    remove_settings(TEXTENSION_PG_highlights)
    
    global prefs
    prefs = None
    ui.remove_draw_hook(draw_match)
    runtime.reset()

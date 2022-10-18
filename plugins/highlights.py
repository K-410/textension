import bpy
from gpu.shader import from_builtin
from mathutils import Vector
from itertools import chain
from collections import deque
import gpu
from gpu.types import GPUVertBuf, GPUBatch
from .. import utils
from ..utils import prefs as p


iterchain = chain.from_iterable
prefs = None


def _to_batch():
    shader = from_builtin('2D_UNIFORM_COLOR')
    sh_fmt = shader.format_calc()
    uniform_float = shader.uniform_float
    shader_bind = shader.bind

    def to_batch_and_draw(type, coords):
        vbo = GPUVertBuf(sh_fmt, len(coords))
        vbo.attr_fill("pos", coords)
        GPUBatch(type=type, buf=vbo).draw(shader)
    return shader_bind, uniform_float, to_batch_and_draw


def get_matches_curl(substr, strlen, find, selr):
    """Get the indices of matches on current line, excluding selection."""
    match_indices = []
    idx = find(substr, 0)
    exclude = range(*selr)

    while idx is not -1:
        span = idx + strlen
        if idx in exclude or span in exclude:
            idx = find(substr, idx + 1)
            continue
        match_indices.append(idx)
        idx = find(substr, span)
    return match_indices


def get_matches(substr, strlen, find):
    match_indices = []
    chr_idx = find(substr, 0)

    while chr_idx is not -1:
        match_indices.append(chr_idx)
        chr_idx = find(substr, chr_idx + strlen)

    return match_indices


def to_tris(lineh, pts, y_ofs):
    y1 = Vector((0, y_ofs))
    y2 = Vector((0, lineh))
    return (*iterchain(
        [(a, b, by, a, by, ay) for a, b, by, ay in
            [(a + y1, b + y1, b + y1 + y2, a + y1 + y2) for a, b, _ in pts]]),)


def to_scroll(lineh, pts, y_ofs):
    y1 = Vector((-1, y_ofs))
    y2 = Vector((0, y_ofs))
    return (*iterchain(
        [(a, b, by, a, by, ay) for a, b, by, ay in
            [(a + y1, b + y1, b + y1 + y2, a + y1 + y2) for a, b in pts]]),)


def to_lines(lineh, pts, y_ofs):
    y = Vector((0, y_ofs + (prefs.line_thickness // 2)))
    return (*iterchain([(i + y, j + y) for i, j, _ in pts]),)


def to_frames(lineh, pts, y_ofs):
    y1 = Vector((0, y_ofs))
    y2 = Vector((0, lineh + y_ofs - 1))
    return (*iterchain(
        [(a, b, ay, by + Vector((1, 0)), ay, a, by, b) for a, b, ay, by in
            [(a + y1, b + y1, a + y2, b + y2) for a, b, _ in pts]]),)


# Find all occurrences and generate points to draw rects
def get_non_wrapped_pts(context, st, substr, selr, lineh, wunits):
    pts = []
    scrollpts = []

    text = st.text
    top = st.top
    lines = text.lines
    curl = text.current_line
    strlen = len(substr)
    loc = st.region_location_from_cursor

    region = context.region
    rw, rh = region.width, region.height
    lh = st.runtime.lheight_px

    # Distance in px from document top
    first_y = loc(0, 0)[1]
    x_offset = cw = st.drawcache.cwidth_px[0]

    # Some arbitrary offset because they broke loc()
    y_offset = first_y - (top * lh) - (rh - lh)

    if st.show_line_numbers:
        x_offset += cw * (len(repr(len(lines))) + 2)

    # Vertical span in pixels
    lenl = len(st.text.lines)
    vspan_px = lineh
    if lenl > 1:
        vspan_px = abs(first_y - loc(lenl - 1, len(lines[-1].body))[1])

    str_span_px = cw * strlen
    hor_max_px = rw - (wunits // 2)
    if prefs.show_in_scrollbar:
        args = st, substr, wunits, vspan_px, rw, rh, lineh
        scrollpts = scrollpts_get(*args)

    case = prefs.case_sensitive
    for idx, line in enumerate(lines[top:top + st.visible_lines + 2], top):
        body = line.body
        find = body.lower().find if not case else body.find
        if line == curl:
            match_indices = get_matches_curl(substr, strlen, find, selr)
        else:
            match_indices = get_matches(substr, strlen, find)

        if len(match_indices) > 1000:
            return pts, scrollpts, y_offset

        for match_idx in match_indices:
            x1, y1 = loc(idx, match_idx)
            x2 = x1 + str_span_px
            if x1 > hor_max_px or x2 <= x_offset:
                continue

            char_offset = (x_offset - x1) // cw if x1 < x_offset else 0
            end_idx = match_idx + strlen
            end_idx -= 1 + (x2 - hor_max_px) // cw if x2 > hor_max_px else 0

            pts.append((Vector((x1 + cw * char_offset, y1)),
                        Vector((x2, y1)),
                        body[match_idx + char_offset:end_idx]))

    return pts, scrollpts, y_offset


# Calculate true top and pixel span when word wrap is turned on
def calc_top(lines, maxy, lineh, rh, yoffs, char_max):
    top = 0
    found = False
    wrap_offset = maxy + yoffs
    wrap_span_px = -lineh

    if char_max < 8:
        char_max = 8

    for idx, line in enumerate(lines):
        wrap_span_px += lineh
        if wrap_offset < rh:
            if not found:
                found = True
                top = idx
        wrap_offset -= lineh

        if len(line.body) < char_max:
            continue
        pos = start = 0
        end = char_max

        for pos, c in enumerate(line.body):
            if pos - start >= char_max:
                wrap_span_px += lineh
                if wrap_offset < rh:
                    if not found:
                        found = True
                        top = idx
                wrap_offset -= lineh
                start = end
                end += char_max
            elif c in " -":
                end = pos + 1
    return top, wrap_span_px


# def get_scrollbar_x_points(region_width, widget_unit):
#     sx_2 = int(region_width - 0.2 * widget_unit)
#     sx_1 = sx_2 - int(0.4 * widget_unit) + 2
#     return sx_1, sx_2

# Find all occurrences on scrollbar
def scrollpts_get(st, substr, wu, vspan_px, rw, rh, lineh):
    scrollpts = []
    append = scrollpts.append
    top_margin = int(0.4 * wu)

    # if p().scrollbar.show_scrollbar:
    #     sx_2 = rw + 1
    #     sx_1 = rw - int(wu - (wu * 0.05)) - 1
    # else:
    #     # x offset for scrollbar widget start
    # sx_2 = int(rw - 0.2 * wu)
    # sx_1 = sx_2 - top_margin + 2
    sx_1, sx_2 = utils.get_scrollbar_x_points(rw)
    # sx_1, sx_2 = get_scrollbar_x_points(rw, wu)

    pxavail = rh - top_margin * 2
    wrh = wrhorg = (vspan_px // lineh) + 1  # wrap lines
    scrolltop = rh - (top_margin + 2)

    vispan = st.top + st.visible_lines
    blank_lines = st.visible_lines // 2
    if wrh + blank_lines < vispan:
        blank_lines = vispan - wrh

    wrh += blank_lines
    j = 2 + wrhorg / len(st.text.lines) * pxavail
    for i, line in enumerate(st.text.lines, 1):
        body = line.body.lower() if not prefs.case_sensitive else line.body
        if substr in body:
            y = scrolltop - i * j // wrh
            append((Vector((sx_1, y)), Vector((sx_2, y))))
    return scrollpts


def get_wrapped_pts(context, st, substr, selr, lineh, wunits):
    pts = []
    scrollpts = []
    text = st.text
    lines = text.lines
    curl = text.current_line

    loc = st.region_location_from_cursor
    first_y = loc(0, 0)[1]
    x_offset = cw = st.drawcache.cwidth_px[0]

    if st.show_line_numbers:
        x_offset += cw * (len(repr(len(lines))) + 2)

    region = context.region
    rh, rw = region.height, region.width
    # Maximum displayable characters in editor
    char_max = (rw - wunits - x_offset) // cw
    if char_max < 8:
        char_max = 8

    # TODO duplicate
    lh = st.runtime.lheight_px
    y_offset = first_y - (st.top * lh) - (rh - lh)
    top, vspan_px = calc_top(lines, first_y, lineh, rh, y_offset, char_max)
    strlen = len(substr)

    # Screen coord tables for fast lookup of match positions
    x_table = range(0, cw * char_max, cw)
    y_top = loc(top, 0)[1]
    y_table = range(y_top, min(0, y_top - vspan_px), -lineh)
    y_table_size = len(y_table)

    wrap_total = w_count = wrap_offset = 0

    # Generate points for scrollbar highlights
    if prefs.show_in_scrollbar:
        args = st, substr, wunits, vspan_px, rw, rh, lineh
        scrollpts = scrollpts_get(*args)

    # Generate points for text highlights
    for l_idx, line in enumerate(lines[top:top + st.visible_lines + 4], top):
        body = line.body
        find = body.lower().find if not prefs.case_sensitive else body.find

        if line == curl:
            # Selected line is processed separately
            match_indices = get_matches_curl(substr, strlen, find, selr)
        else:
            match_indices = get_matches(substr, strlen, find)

        # Hard max for match finding
        if len(match_indices) > 1000:
            return pts, scrollpts, y_offset

        # Wraps
        w_list = []
        w_start = 0
        w_end = char_max
        w_count = -1
        coords = deque()

        # Simulate word wrapping for displayed text and store
        # local text coordinates and wrap indices for each line.
        for idx, char in enumerate(body):
            if idx - w_start >= char_max:
                w_list.append(body[w_start:w_end])
                w_count += 1
                coords.extend([(i, w_count) for i in range(w_end - w_start)])
                w_start = w_end
                w_end += char_max
            elif char in " -":
                w_end = idx + 1

        w_list.append(body[w_start:])
        w_end = w_start + (len(body) - w_start)
        w_count += 1
        coords.extend([(i, w_count) for i in range(w_end - w_start)])
        w_indices = [i for i, _ in enumerate(w_list) for _ in _]

        # Region coords for wrapped char/line by match index
        for match_idx in match_indices:
            mspan = match_idx + strlen

            w_char, w_line = coords[match_idx]
            w_char_end, w_line_end = coords[mspan - 1]

            # in edge cases where a single wrapped line has
            # several thousands of matches, skip and continue
            if w_line > y_table_size or w_line_end > y_table_size:
                continue

            matchy = y_table[w_line] - wrap_offset
            if matchy > rh or matchy < -lineh:
                continue

            co_1 = Vector((x_offset + x_table[w_char], matchy))

            if w_line != w_line_end:
                start = match_idx
                end = wrap_idx = 0

                for midx in range(strlen):
                    widx = match_idx + midx
                    w_char, w_line = coords[widx]
                    matchy = y_table[w_line] - wrap_offset

                    if matchy != co_1.y:
                        co_2 = Vector((x_table[w_char - 1] + cw + x_offset,
                                       y_table[w_line - 1] - wrap_offset))

                        if wrap_idx:
                            text = w_list[w_indices[widx - 1]]
                        else:
                            text = body[start:widx]
                        pts.append((co_1, co_2, text))
                        co_1 = Vector((x_offset + x_table[w_char], matchy))
                        end = midx
                        start += end
                        wrap_idx += 1
                        continue
                text = body[match_idx:mspan][end:]
                co_2 = Vector((x_offset + x_table[w_char] + cw, matchy))
                pts.append((co_1, co_2, text))

            else:
                text = body[match_idx:mspan]
                co_2 = co_1.copy()
                co_2.x += cw * strlen
                pts.append((co_1, co_2, text))

        wrap_total += w_count + 1
        wrap_offset = lineh * wrap_total
    return pts, scrollpts, y_offset


def coords_get(context, st, *args):
    if st.show_word_wrap:
        return get_wrapped_pts(context, st, *args)
    return get_non_wrapped_pts(context, st, *args)


# TODO Store batches for reuse and translate gpu.matrix instead
def draw_match(context, system):
    st = context.space_data
    text = st.text

    # Nothing to draw.
    if not text:
        return

    selr = sorted((text.current_character, text.select_end_character))
    curl = text.current_line
    substr = curl.body[slice(*selr)]

    # Nothing to find.
    if not substr.strip():
        return

    if not prefs.case_sensitive:
        substr = substr.lower()

    if len(substr) >= prefs.minimum_length and curl == text.select_end_line:
        scroll_ofs = st.offsets.y
        wunits = system.wu
        lheight = st.runtime.lheight_px

        pts, scrollpts, offset = coords_get(context, st, substr, selr, lheight, wunits)

        gpu.state.blend_set('ADDITIVE')
        shader_bind()

        # Draw scroll highlights.
        if prefs.show_in_scrollbar:
            uniform_float("color", tuple(prefs.color_scroll))
            to_batch_and_draw("TRIS", to_scroll(lheight, scrollpts, 2))

        # Draw solid background.
        if prefs.show_background:
            uniform_float("color", prefs.color_background)
            to_batch_and_draw("TRIS", to_tris(lheight, pts, -offset + scroll_ofs))

        # Draw outline.
        if prefs.show_outline:
            gpu.state.line_width_set(prefs.outline_thickness)
            uniform_float("color", tuple(prefs.color_line))
            to_batch_and_draw("LINES", to_frames(lheight, pts, -offset + scroll_ofs))

        # Draw underlines.
        elif prefs.show_underline:
            gpu.state.line_width_set(prefs.line_thickness)
            uniform_float("color", tuple(prefs.color_line))
            to_batch_and_draw("LINES", to_lines(lheight, pts, -offset + scroll_ofs))


def update_highlight(state):
    from .. import utils

    active = utils.ud.is_registered("matches")
    redraw = active | state

    if state and not active:
        system = utils._context.preferences.system
        utils.ud.add("matches", draw_match, (utils._context, system))
    elif not state and active:
        utils.ud.remove("matches")
    if redraw:
        utils.redraw_editors()


# When outline is enabled, disable underline and vice versa.
def update_lines(self, context, prop_idx):
    update_lines.block = vars(update_lines).setdefault("block", False)
    if not update_lines.block:
        update_lines.block = True
        if prop_idx == 0:
            if self.show_outline:
                self.show_underline = False
        elif prop_idx == 1:
            if self.show_underline:
                self.show_outline = False
        update_lines.block = False


def update_colors(self, context):
    col_attrs = "color_background", "color_line", 'color_scroll'
    if self.color_preset != 'CUSTOM':
        for source, target in zip(self.colors[self.color_preset], col_attrs):
            setattr(self, target, source)


class HighlightPrefs(bpy.types.PropertyGroup):
    # Color presets for highlights.
    colors = {
        "BLUE": ((0.2, 0.3, 0.4, 0.5), (0.2, 0.4, 0.6, 0.5), (0.1, 0.6, 1.0, 0.5)),
        "YELLOW": ((0.3, 0.25, 0.0, 1.0), (0.5, 0.5, 0.0, 1.0), (1.0, 0.8, 0.1, 0.4)),
        "GREEN": ((0.01, 0.21, 0.01, 1.0), (0.2, 0.5, 0.2, 1.0), (0.1, 1.0, 0.0, 0.4)),
        "RED": ((0.33, 0.08, 0.08, 1.0), (0.6, 0.3, 0.3, 1.0), (1.0, 0.2, 0.2, 0.5))}

    line_thickness: bpy.props.IntProperty(
        description="Underline thickness in pixels",
        name="Underline Thickness",
        default=2,
        min=1,
        max=4,
        update=utils.prefs_modify_cb,
    )
    outline_thickness: bpy.props.IntProperty(
        description="Frame thickness in pixels",
        name="Frame Thickness",
        default=1,
        min=1,
        max=4,
        update=utils.prefs_modify_cb,
    )
    show_in_scrollbar: bpy.props.BoolProperty(
        description="Show match highlights in scrollbar",
        name="Show in Scrollbar",
        default=True,
        update=utils.prefs_modify_cb,
    )
    minimum_length: bpy.props.IntProperty(
        description="Don't trigger highlights below this",
        name='Minimum Length',
        default=2,
        min=1,
        max=4,
        update=utils.prefs_modify_cb,
    )
    case_sensitive: bpy.props.BoolProperty(
        description='Case Sensitive',
        name='Use Case Sensitive',
        default=False,
        update=utils.prefs_modify_cb,
    )
    show_background: bpy.props.BoolProperty(
        description="Show background color",
        name="Show Background",
        default=True,
        update=utils.prefs_modify_cb,
    )
    show_outline: bpy.props.BoolProperty(
        description="Show outline",
        name="Show Outline",
        default=False,
        update=utils.pmodify_wrap(lambda s, c: update_lines(s, c, 0)),
    )
    show_underline: bpy.props.BoolProperty(
        description="Show underline color",
        name="Show Underline",
        default=False,
        update=utils.pmodify_wrap(lambda s, c: update_lines(s, c, 1)),
    )

    color_prop_kw = {
        'subtype': 'COLOR',
        'size': 4,
        'min': 0,
        'max': 1,
        'update': utils.prefs_modify_cb
    }

    color_background: bpy.props.FloatVectorProperty(
        name='Background Color',
        description='Background color',
        default=colors['BLUE'][0],
        **color_prop_kw
    )
    color_line: bpy.props.FloatVectorProperty(
        name='Line Color',
        description='Line / outline color',
        default=colors['BLUE'][1],
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
        update=utils.pmodify_wrap(update_colors),
        items=(("BLUE", "Blue", "", 1),
               ("YELLOW", "Yellow", "", 2),
               ("GREEN", "Green", "", 3),
               ("RED", "Red", "", 4),
               ("CUSTOM", "Custom", "", 5)))

    del color_prop_kw

    @classmethod
    def setup(cls):
        global shader_bind, uniform_float, to_batch_and_draw
        shader_bind, uniform_float, to_batch_and_draw = _to_batch()

        global prefs
        prefs = p().highlights
        bpy.types.TEXT_MT_view.append(draw_highlight_occurrences_menu)
        update_highlight(True)

    @classmethod
    def remove(cls):
        update_highlight(False)
        bpy.types.TEXT_MT_view.remove(draw_highlight_occurrences_menu)
        global shader_bind, uniform_float, to_batch_and_draw
        del shader_bind, uniform_float, to_batch_and_draw


def draw_settings(prefs, context, layout):
    self = prefs.highlights

    layout.prop(self, "case_sensitive")
    layout.prop(self, "minimum_length")
    layout.prop(self, "outline_thickness")
    layout.prop(self, "line_thickness")

    layout.separator()

    layout.prop(self, "show_background")
    layout.prop(self, "show_outline")
    layout.prop(self, "show_underline")
    layout.prop(self, "show_in_scrollbar")

    layout.separator()

    col = layout.column()
    split = col.split(factor=0.4)
    row = split.row()
    row.alignment = 'RIGHT'
    row.label(text="Color")

    grid = split.grid_flow(align=True, row_major=True)
    grid.scale_x = 2.0
    grid.alignment = 'CENTER'
    grid.use_property_split = False

    layout.separator()

    for prop in 'RED', 'GREEN', 'BLUE', 'YELLOW', 'CUSTOM':
        grid.prop_enum(self, "color_preset", value=prop)

    if self.color_preset == 'CUSTOM':
        layout.prop(self, "color_background")
        layout.prop(self, "color_line")
        layout.prop(self, "color_scroll")



def draw_highlight_occurrences_menu(self, context):
    self.layout.prop(prefs, "enable")

def enable():
    from ..utils import register_class
    register_class(HighlightPrefs)
    prefs = p()
    type(prefs).highlights = bpy.props.PointerProperty(type=HighlightPrefs)
    HighlightPrefs.setup()

def disable():
    prefs = p()
    from ..utils import unregister_class
    unregister_class(type(prefs.highlights))
    
    # Unregister occurrence highlights.
    if prefs.highlights is not None:
        type(prefs.highlights).remove()

    del type(prefs).highlights

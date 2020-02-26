import bpy
from gpu.shader import from_builtin
from mathutils import Vector, Color
from itertools import chain
from collections import deque
from bgl import glLineWidth, glEnable, glDisable, glBlendFunc, \
    GL_BLEND, GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_SRC_ALPHA_SATURATE
import blf
from gpu.types import GPUVertBuf, GPUBatch


iterchain = chain.from_iterable
prefs = None


def _to_batch():
    shader = from_builtin('2D_UNIFORM_COLOR')
    sh_fmt = shader.format_calc()
    uniform_float = shader.uniform_float
    shader_bind = shader.bind

    def to_batch(type, coords):
        vbo = GPUVertBuf(sh_fmt, len(coords))
        vbo.attr_fill("pos", coords)
        batch = GPUBatch(type=type, buf=vbo)
        batch.program_set(shader)
        return batch
    return shader_bind, uniform_float, to_batch


shader_bind, uniform_float, to_batch = _to_batch()


def get_matches_curl(substr, strlen, find, selr):
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
    y1, y2 = Vector((0, y_ofs)), Vector((0, lineh))
    return (*iterchain(
        [(a, b, by, a, by, ay) for a, b, by, ay in
            [(a + y1, b + y1, b + y1 + y2, a + y1 + y2) for a, b, _ in pts]]),)


def to_scroll(lineh, pts, y_ofs):
    y1, y2 = Vector((-1, y_ofs)), Vector((0, y_ofs))
    return (*iterchain(
        [(a, b, by, a, by, ay) for a, b, by, ay in
            [(a + y1, b + y1, b + y1 + y2, a + y1 + y2) for a, b in pts]]),)


def to_lines(lineh, pts, y_ofs):
    y = Vector((0, y_ofs + (prefs.line_thickness // 2)))
    return (*iterchain([(i + y, j + y) for i, j, _ in pts]),)


def to_frames(lineh, pts, y_ofs):
    y1, y2 = Vector((0, y_ofs)), Vector((0, lineh + y_ofs - 1))
    return (*iterchain(
        [(a, b, ay, by + Vector((1, 0)), ay, a, by, b) for a, b, ay, by in
            [(a + y1, b + y1, a + y2, b + y2) for a, b, _ in pts]]),)


# Find character width
def get_cw(loc, firstx, lines):
    for idx, line in enumerate(lines):
        if len(line.body) > 1:
            return loc(idx, 1)[0] - firstx


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
    lheight_px = int(wunits * st.font_size) // 20
    lh = int(1.3 * lheight_px)

    # Distance in px from document top
    first_x, first_y = loc(0, 0)
    x_offset = cw = get_cw(loc, first_x, lines)

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
    if prefs.show_in_scroll:
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
        pos = start = 0
        end = char_max

        body = line.body
        if len(body) < char_max:
            continue

        for c in body:
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
            pos += 1
    return top, wrap_span_px


# Find all occurrences on scrollbar
def scrollpts_get(st, substr, wu, vspan_px, rw, rh, lineh):
    scrollpts = []
    append = scrollpts.append
    top_margin = int(0.4 * wu)

    # x offset for scrollbar widget start
    sx_2 = int(rw - 0.2 * wu)
    sx_1 = sx_2 - top_margin + 2
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
    first_x, first_y = loc(0, 0)
    x_offset = cw = get_cw(loc, first_x, lines)

    if st.show_line_numbers:
        x_offset += cw * (len(repr(len(lines))) + 2)

    region = context.region
    rh, rw = region.height, region.width
    # Maximum displayable characters in editor
    char_max = (rw - wunits - x_offset) // cw
    if char_max < 8:
        char_max = 8

    # TODO duplicate
    lheight_px = int(wunits * st.font_size) // 20
    lh = int(1.3 * lheight_px)
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
    if prefs.show_in_scroll:
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


# for calculating offsets and max displayable characters
# source/blender/windowmanager/intern/wm_window.c$515
def get_widget_unit(context):
    system = context.preferences.system
    p = system.pixel_size
    dpi = system.dpi
    pd = p * dpi
    wu = int((pd * 20 + 36) / 72 + (2 * (p - pd // 72)))
    return wu


def cwidth_get(st, wunits):
    for idx, line in enumerate(st.text.lines):
        if line.body:
            loc = st.region_location_from_cursor
            return loc(idx, 1)[0] - loc(idx, 0)[0]

    # Approx fallback width
    blf.size(1, st.font_size, 72)
    return round(blf.dimensions(1, "W")[0] * (wunits * 0.05))


def coords_get(context, st, *args):
    if st.show_word_wrap:
        return get_wrapped_pts(context, st, *args)
    return get_non_wrapped_pts(context, st, *args)


# TODO Store batches for reuse and translate gpu.matrix instead
def draw_highlights(context):
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

    if len(substr) >= prefs.min_str_len and curl == text.select_end_line:
        wunits = get_widget_unit(context)
        lheight = int(1.3 * (int(wunits * st.font_size) // 20))

        args = context, st, substr, selr, lheight, wunits
        pts, scrollpts, offset = coords_get(*args)
        cw = cwidth_get(st, wunits)

        args = lheight, pts, -offset
        glEnable(GL_BLEND)
        shader_bind()

        # Draw scroll highlights.
        if prefs.show_in_scroll:
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            uniform_float("color", tuple(prefs.color_scroll))
            to_batch("TRIS", to_scroll(lheight, scrollpts, 2)).draw()

        # Draw solid background.
        if prefs.show_background:
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            colors = tuple(prefs.color_background)
            if not prefs.use_overlay:
                glBlendFunc(GL_SRC_ALPHA_SATURATE, GL_SRC_ALPHA)
                color = Color(colors[:3])
                color.v = max(0, color.v * 0.55)
                color.s = min(1, color.s * 2)
                colors = color[:] + (1,)

            uniform_float("color", colors)
            to_batch("TRIS", to_tris(*args)).draw()

        # Draw frames.
        if prefs.show_frames:
            glLineWidth(prefs.frame_thickness)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            uniform_float("color", tuple(prefs.color_line))
            to_batch("LINES", to_frames(*args)).draw()

        # Draw underlines.
        elif prefs.show_underline:
            glLineWidth(prefs.line_thickness)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            uniform_float("color", tuple(prefs.color_line))
            to_batch("LINES", to_lines(*args)).draw()

        # Draw highlight text
        if prefs.use_overlay:
            y_offset = (wunits * cw) // wunits  # correct for fonts
            blf.size(1, int(st.font_size * (wunits * 0.05)), 72)
            blf.color(1, *prefs.color_text)
            for co, _, substring in pts:
                co.y += y_offset
                blf.position(1, *co, 1)
                blf.draw(1, substring)
        glDisable(GL_BLEND)


def is_restrict_context():
    from bpy_restrict_state import _RestrictContext
    return isinstance(bpy.context, _RestrictContext)


def update_highlight(enable=False):
    cls = HighlightOccurrencesPrefs
    if enable:
        # Unlikely, but why not.
        if hasattr(cls, "_handle"):
            return

        # Try again.
        if is_restrict_context():
            return bpy.app.timers.register(
                lambda: update_highlight(enable))

        handle = bpy.types.SpaceTextEditor.draw_handler_add(
            draw_highlights, (bpy.context,), 'WINDOW', 'POST_PIXEL')
        cls._handle = handle
    else:
        if hasattr(cls, "_handle"):
            bpy.types.SpaceTextEditor.draw_handler_remove(
                cls._handle, 'WINDOW')
            del cls._handle
    redraw_text_editors()


# Force toggle off xor.
def update_lines(self, context, prop_idx):
    update_lines.block = vars(update_lines).setdefault("block", False)
    if not update_lines.block:
        update_lines.block = True
        if prop_idx == 0:
            if self.show_frames:
                self.show_underline = False
        elif prop_idx == 1:
            if self.show_underline:
                self.show_frames = False
        update_lines.block = False


def update_colors(self, context):
    col_attrs = "color_background", "color_text", "color_line", 'color_scroll'
    if self.color_preset != 'CUSTOM':
        for source, target in zip(self.colors[self.color_preset], col_attrs):
            setattr(self, target, source)


class HighlightOccurrencesPrefs(bpy.types.PropertyGroup):
    # Color presets for highlights.
    colors = {
        "BLUE": (
            (0.2, 0.3, 0.4, 1.0),
            (1.0, 1.0, 1.0, 1.0),
            (0.2, 0.4, 0.6, 1.0),
            (0.1, 0.6, 1.0, 0.5)),
        "YELLOW": (
            (0.4, 0.4, 0.1, 1.0),
            (1.0, 1.0, 1.0, 1.0),
            (0.5, 0.5, 0.0, 1.0),
            (1.0, 0.8, 0.1, 0.4)),
        "GREEN": (
            (0.2, 0.4, 0.3, 1.0),
            (1.0, 1.0, 1.0, 1.0),
            (0.2, 0.5, 0.2, 1.0),
            (0.1, 1.0, 0.0, 0.4)),
        "RED": (
            (0.6, 0.2, 0.2, 1.0),
            (1.0, 1.0, 1.0, 1.0),
            (0.6, 0.3, 0.3, 1.0),
            (1.0, 0.2, 0.2, 0.5))}

    enable: bpy.props.BoolProperty(
        description="Enable highlighting",
        name="Highlight Occurrences",
        default=True,
        update=lambda self, context: update_highlight(self.enable))

    line_thickness: bpy.props.IntProperty(
        description="Underline thickness in pixels",
        name="Underline Thickness",
        default=2,
        min=1,
        max=4)

    frame_thickness: bpy.props.IntProperty(
        description="Frame thickness in pixels",
        default=1,
        name="Frame Thickness",
        min=1,
        max=4)

    show_in_scroll: bpy.props.BoolProperty(
        description="Show match highlights in scrollbar",
        name="Scrollbar Highlights",
        default=True)

    min_str_len: bpy.props.IntProperty(
        description="Don't search below this",
        name='Minimum Search Length',
        default=2,
        min=1,
        max=4)

    case_sensitive: bpy.props.BoolProperty(
        description='Case Sensitive Matching',
        name='Case Sensitive',
        default=False)

    show_background: bpy.props.BoolProperty(
        description="Show background color",
        name="Background",
        default=True)

    show_frames: bpy.props.BoolProperty(
        description="Show frames color",
        name="Frames",
        default=False,
        update=lambda self, context: update_lines(self, context, 0))

    show_underline: bpy.props.BoolProperty(
        description="Show underline color",
        name="Underline",
        default=False,
        update=lambda self, context: update_lines(self, context, 1))

    use_overlay: bpy.props.BoolProperty(
        description="Use normal blending technique and font \n"
                    "overlay which allows darker backgrounds",
        name="Font Overlay",
        default=False)

    color_background: bpy.props.FloatVectorProperty(
        description='Background color',
        name='Background Color',
        default=colors['BLUE'][0],
        subtype='COLOR_GAMMA',
        size=4,
        min=0,
        max=1)

    color_line: bpy.props.FloatVectorProperty(
        description='Line and frame color',
        name='Line / Frame Color',
        default=colors['BLUE'][2],
        subtype='COLOR_GAMMA',
        size=4,
        min=0,
        max=1)

    color_text: bpy.props.FloatVectorProperty(
        description='Overlay text color',
        name='Text Color',
        default=colors['BLUE'][1],
        size=4,
        min=0,
        subtype='COLOR_GAMMA',
        max=1)

    color_scroll: bpy.props.FloatVectorProperty(
        description="Scroll highlight opacity",
        name="Scrollbar",
        default=colors['BLUE'][3],
        size=4,
        min=0,
        max=1,
        subtype='COLOR_GAMMA')

    color_preset: bpy.props.EnumProperty(
        description="Highlight color presets",
        name="Presets",
        default="BLUE",
        update=update_colors,
        items=(("BLUE", "Blue", "", 1),
               ("YELLOW", "Yellow", "", 2),
               ("GREEN", "Green", "", 3),
               ("RED", "Red", "", 4),
               ("CUSTOM", "Custom", "", 5)))


def draw_hlite_prefs(_, self, context, layout):
    row = layout.row()
    row.alignment = 'CENTER'
    row.column().label()
    col = row.column(align=True)
    row = col.row(align=True)
    row.alignment = 'LEFT'

    color_text = "color_text" if self.use_overlay else ""
    colors = ("color_background", color_text, "color_line", "color_line",
              "color_scroll")
    display = ("show_background", "use_overlay", "show_frames",
               "show_underline", "show_in_scroll")

    for prop in 'CUSTOM', 'BLUE', 'RED', 'YELLOW', 'GREEN':
        row.prop_enum(self, "color_preset", value=prop)

    row = layout.row()
    row.alignment = 'CENTER'

    col = row.column()
    for prop in ("case_sensitive", "min_str_len", "frame_thickness",
                 "line_thickness", ""):
        col.prop(self, prop) if prop else col.separator()

    col = row.column()
    for prop in display:
        col.prop(self, prop) if prop else col.label()

    col = row.column()
    if self.color_preset == 'CUSTOM':
        for prop, color in zip(display, colors):
            if color and getattr(self, prop):
                col.prop(self, color, text="")
            else:
                col.label()
    else:
        col.column().label()
    layout.separator()


def draw_highlight_occurrences_menu(self, context):
    self.layout.prop(prefs, "enable")


def redraw_text_editors():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'TEXT_EDITOR':
                area.tag_redraw()


def register():
    global prefs
    addon = bpy.context.preferences.addons[__package__]
    prefs = addon.preferences.highlights
    bpy.types.TEXT_MT_view.append(draw_highlight_occurrences_menu)
    update_highlight(prefs.enable)


def unregister():
    update_highlight(False)
    bpy.types.TEXT_MT_view.remove(draw_highlight_occurrences_menu)

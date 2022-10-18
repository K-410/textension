from ..gl import GLRoundedRect, GLPlainRect
from ..utils import clamp_factory
from .. import utils, types
import bpy


system = bpy.utils._preferences.system
_context = utils._context

gutter = GLRoundedRect(0.15, 0.15, 0.15, 1.0)
thumb = GLRoundedRect(0.3, 0.3, 0.3, 1.0)
cursor = GLPlainRect(0.4, 0.4, 0.4, 1.0)
selection = GLPlainRect(0.7, 0.13, 0.135, 0.3)
mask = GLPlainRect()



def thumb_vpos_calc(rh, st) -> tuple[int, int]:
    """
    Calculate the vertical position of the scrollbar thumb.
    Return value is y-position and height in pixels.
    """

    not_lh_px = st.runtime._lheight_px
    # Was accessed before drawing. In this case the value doesn't matter.
    if not_lh_px == 0:
        not_lh_px = 10

    lh_px = int(not_lh_px * 1.3)
    view_lines = rh / lh_px
    view_lines_half = view_lines / 2

    corr = int((rh + not_lh_px - 1) / lh_px) // 2 - view_lines_half
    span = st.drawcache.total_lines + view_lines_half - corr
    top = st.top + (st.offsets.y / lh_px)

    min_r = 30 / rh
    size_r = rh / lh_px / span
    if min_r < size_r:
        y = rh * (1 - (top + view_lines) / span)
        h = (rh * (1 - top / span)) - y

    else:
        ymax = rh - int((rh * (1 - (min_r - size_r))) * top / span)
        y = int(ymax - rh * min_r)
        h = ymax - int(ymax - rh * min_r)

    return max(0, y), h + 1



def draw(context):
    """
    Draw routine for the scrollbar.
    """
    st = context.space_data
    text = st.text
    if text:
        region = context.region
        rh = region.height
        rw = region.width
        wu = system.wu
        wu_norm = wu * 0.05

        y, h = thumb_vpos_calc(rh, st)
        
        # TODO: Currently calculate this on the fly. Should store the previous
        # value and change only when widget unit changes.
        w = int(wu - wu_norm) + 2
        x = rw - w

        # Hide the vanilla scrollbar.
        mask(x, 0, w, rh)

        # Don't draw the scrollbar when thumb exceeds region height.
        if h > rh:
            return

        p_scrollbar = utils.prefs().scrollbar
        roundness = max(1.0, (w / 2.0 * p_scrollbar.roundness))

        gutter.set_roundness(roundness)
        thumb.set_roundness(roundness)
        gutter(x, 0, w, rh)
        thumb(x, y, w + 1, h)

        # Draw selection and cursor in the scrollbar
        y, h = st.scroll_select_y
        h -= y

        curl = text.current_line_index
        sell = text.select_end_line_index
        if curl != sell and p_scrollbar.show_selection:
            selection(x, y, w, h)

        if p_scrollbar.show_cursor:
            if curl > sell:
                y += h
            cursor(x, y, w, (2 * wu_norm))


class TEXTENSION_OT_scrollbar(types.TextOperator):
    jump: bpy.props.BoolProperty(
        description="Jump to mouse position",
        name="Jump",
        default=False,
        options={'SKIP_SAVE'}
    )

    @classmethod
    def register_keymaps(cls):
        from ..km_utils import kmi_new
        kmi_new(cls, "Text", cls.bl_idname, 'MIDDLEMOUSE', 'PRESS').jump = True

    def invoke(self, context, event):
        # Jumping generally requires the cursor to be somewhere on the scrollbar.
        if self.jump:
            if utils.hit_test(context) not in {exec_thumb, exec_up, exec_down}:
                return {'PASS_THROUGH'}

        st = context.space_data
        self.top = self.value = last = top = st.top
        tag_redraw = context.region.tag_redraw
        offsets = st.offsets
        ofs_prev = 0

        def top_set(top, ofs, update=False):
            nonlocal last, ofs_prev
            if ofs != ofs_prev:
                ofs_prev = ofs
                offsets.y = int(lh_px * ofs)
                update = True
            if top != last:
                st.top = last = top
                update = False
            if update:
                tag_redraw()
            return top

        st.flags |= 0x1
        totl = st.drawcache.total_lines
        span = totl - int(st.visible_lines // 2)
        scroll_clamp = clamp_factory(0, span)

        interp_clamp = clamp_factory(-10, 10)

        rh = context.region.height
        y, h = thumb_vpos_calc(rh, st)
        tmb = h
        lh = st.runtime._lheight_px
        lh_px = int(lh * 1.3)
        # Amount of lines to scroll per pixel.
        # [Total lines] minus [half view] divided by [[region height] minus [thumb]]
        f = (totl - (int((rh + lh - 1) / lh_px) // 2)) / (max(1, rh - tmb))
        start = top

        def top_calc(my, my_init=event.mouse_y):
            nonlocal start
            end = scroll_clamp(top - (f * (my - my_init)))
            start = interp_clamp(start - end) + end
            start += 1 * (end - start) * 0.9 * 0.7 * 0.7
            if abs((start - end)) <= 0.1:
                start = round(end, 1)
            return start

        if self.jump:
            # Round top on initial jump - avoids the visual line offset when
            # not dragging the thumb.
            topf = scroll_clamp(span - f * (event.mouse_region_y - tmb // 2))
            self.top = self.value = top = last = round(topf)
            # Add hysteresis to prevent snap-back when jump is under one line.
            if abs(start - top) <= 1.0:
                start = top

        self.x = event.mouse_x
        self.top_set = top_set
        self.top_calc = top_calc
        self.dist_max = 180 * context.preferences.system.wu * 0.05
        context.window_manager.modal_handler_add(self)
        context.window.cursor_modal_set('DEFAULT')
        self.t = context.window_manager.event_timer_add(1 / 500, window=context.window)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        evt = event.type
        if evt in ('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'TIMER'):

            # Snap to scroll start when cursor is far from the thumb.
            if abs(event.mouse_x - self.x) > self.dist_max:
                self.value = self.top_set(self.top, 0, update=True)
                return {'RUNNING_MODAL'}

            topf = self.value = self.top_calc(event.mouse_y)
            self.top_set(int(topf), topf % 1)

        elif evt in ('LEFTMOUSE', 'MIDDLEMOUSE') and event.value == 'RELEASE':
            self.top_set(round(self.value), 0)
            context.window_manager.event_timer_remove(self.t)
            return {'FINISHED'}

        elif evt not in ('TIMER', 'TIMER_REPORT', 'NONE'):
            self.top_set(self.top, 0)
            context.window_manager.event_timer_remove(self.t)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


def load_scrollbar_colors(scrollbar_prefs):
    update_scrollbar_cursor_color(scrollbar_prefs, None)
    update_scrollbar_selection_color(scrollbar_prefs, None)
    update_scrollbar_thumb_color(scrollbar_prefs, None)
    update_scrollbar_gutter_color(scrollbar_prefs, None)


def update_scrollbar_cursor_color(self, _):
    cursor.set_background_color(*self.scrollbar_cursor_color)


def update_scrollbar_selection_color(self, _):
    selection.set_background_color(*self.scrollbar_selection_color)


def update_scrollbar_thumb_color(self, _):
    thumb.set_background_color(*self.scrollbar_thumb_color)
    thumb.set_border_color(*self.scrollbar_thumb_color)


def update_scrollbar_gutter_color(self, _):
    gutter.set_background_color(*self.scrollbar_gutter_color)
    gutter.set_border_color(*self.scrollbar_gutter_color)


class TEXTENSION_PG_scrollbar(bpy.types.PropertyGroup):
    show_cursor: bpy.props.BoolProperty(
        description="Show cursor in scrollbar",
        name="Show Cursor",
        default=True,
        update=utils.prefs_modify_cb,
    )
    show_selection: bpy.props.BoolProperty(
        description="Show selection in scrollbar",
        name="Show Selection",
        default=True,
        update=utils.prefs_modify_cb,
    )
    color_params = {
        "min": 0, "max": 1, "size": 4, "subtype": 'COLOR_GAMMA'
    }

    scrollbar_gutter_color: bpy.props.FloatVectorProperty(
        description="Scrollbar background color",
        default=gutter.background,
        name="Gutter Background",
        update=update_scrollbar_gutter_color,
        **color_params,
    )
    scrollbar_cursor_color: bpy.props.FloatVectorProperty(
        description="The color of the cursor on the scrollbar",
        default=cursor.background,
        name="Cursor Color",
        update=update_scrollbar_cursor_color,
        **color_params,
    )
    scrollbar_selection_color: bpy.props.FloatVectorProperty(
        description="The color of the selection range on the scrollbar",
        default=selection.background,
        name="Selection Color",
        update=update_scrollbar_selection_color,
        **color_params,
    )
    scrollbar_thumb_color: bpy.props.FloatVectorProperty(
        description="Scrollbar thumb background color",
        default=thumb.background,
        name="Thumb Background",
        update=update_scrollbar_thumb_color,
        **color_params,
    )
    roundness: bpy.props.FloatProperty(
        description="Scrollbar corner roundness",
        default=0,
        min=0.0,
        max=1.0,
        name="Roundness",
        update=utils.prefs_modify_cb,
    )


classes = (
    TEXTENSION_PG_scrollbar,
    TEXTENSION_OT_scrollbar,
)


def get_bl_prop(struct: bpy.types.Struct, prop: str):
    return struct.path_resolve(prop, False)

def update_mask(color):
    mask.set_background_color(*color, 1.0)

def get_scrollbar_x_points(rw):
    wu = system.wu
    return rw - int(wu - (wu * 0.05)) - 1, rw + 1


def enable():
    assert not TEXTENSION_PG_scrollbar.is_registered

    # Override vanilla scrollbar hit testing
    from .. import test_vanilla_scrollbar
    utils.add_hittest(test_scrollbar)
    utils.remove_hittest(test_vanilla_scrollbar)

    # Override utility function that computes scrollbar width
    utils._get_scrollbar_x_points = utils.get_scrollbar_x_points
    utils.get_scrollbar_x_points = get_scrollbar_x_points

    prefs = utils.prefs()
    utils.register_class_iter(classes)
    type(prefs).scrollbar = bpy.props.PointerProperty(type=TEXTENSION_PG_scrollbar)

    load_scrollbar_colors(prefs.scrollbar)

    theme = bpy.context.preferences.themes["Default"]
    mask.set_background_color(*theme.text_editor.space.back, 1.0)

    key = get_bl_prop(theme.text_editor.space, "back")
    utils.watch_rna(key, update_mask, args=(key,))
    utils.add_draw_hook(draw, bpy.types.SpaceTextEditor, (_context,))


def disable():
    assert TEXTENSION_PG_scrollbar.is_registered
    assert utils.remove_draw_hook(draw)

    # Restore overridden function
    utils.get_scrollbar_x_points = utils._get_scrollbar_x_points
    del utils._get_scrollbar_x_points

    # Restore vanilla hit testing
    from .. import test_vanilla_scrollbar
    utils.remove_hittest(test_scrollbar)
    utils.add_hittest(test_vanilla_scrollbar)
    utils.unregister_class_iter(classes)
    del type(utils.prefs()).scrollbar

    utils.unwatch_rna(update_mask)
    bpy.msgbus.clear_by_owner(utils.this_module())


def draw_settings(prefs, context, layout):
    scrollbar = prefs.scrollbar
    layout.prop(scrollbar, "scrollbar_gutter_color")
    layout.prop(scrollbar, "scrollbar_thumb_color")
    layout.prop(scrollbar, "scrollbar_cursor_color")
    layout.prop(scrollbar, "scrollbar_selection_color")
    layout.prop(scrollbar, "roundness")


def exec_thumb():
    bpy.ops.textension.scrollbar('INVOKE_DEFAULT')


def timed_scroll(direction):
    # TODO: This needs to be a modal operator.
    context = bpy.context
    st = context.space_data
    lines = context.region.height // st.runtime.lheight_px
    bpy.ops.textension.scroll2('INVOKE_DEFAULT', lines=lines, direction=direction)


def exec_up():
    timed_scroll("UP")


def exec_down():
    timed_scroll("DOWN")


def test_scrollbar(data: types.HitTestData):
    context = data.context
    if not context.edit_text:
        return

    region = data.region
    mrx = region.mouse_x
    rw = region.width
    wu = system.wu
    wu_norm = wu * 0.05

    # Scrollbar rect was not hit.
    if not rw - int(wu - wu_norm) - 2 <= mrx:
        return

    st = data.space_data
    mry = region.mouse_y
    rh = region.height
    rui = context.area.regions[2]

    # We have to test against the sidebar toggle's action zone.
    if rui.alignment == 'RIGHT' and rui.width == 1:
        if mrx >= rw - int(wu * 0.4) - 1 and mrx <= rw:
            if mry >= rh - int(wu * 1.4) - 1:
                if mry <= rh - int(wu * 0.8 - wu_norm):
                    # Hit. Fail the test and pass on the event.
                    return None

    # Check which parts of the scrollbar we're hitting.
    if mrx <= rw - 2 and mry <= rh and mry >= 0:
        sy, sh = thumb_vpos_calc(rh, st)

        
        # The scrollbar isn't drawn when the thumb part exceeds
        # the region height, so hit testing needs to fail.
        if sh > rh:
            return

        if sy > -1:
            context.window.cursor_set("DEFAULT")
            if sy <= mry:
                if mry <= sy + sh:
                    return exec_thumb
                return exec_up
            return exec_down

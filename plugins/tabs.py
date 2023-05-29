import bpy
from textension import utils
from textension.ui.gl import Rect
from textension.utils import _context, _system, TextOperator
from textension.prefs import get_prefs

from textension.btypes import uiPopupMenu
import blf


UI_BLOCK_MOVEMOUSE_QUIT = 128
# UI_BLOCK_KEEP_OPEN = 1 << 8
# UI_BTYPE_LABEL = 20 << 9
UI_BLOCK_POPUP_MEMORY = 1 << 12
UI_BTYPE_BUT = 1 << 9
UI_BTYPE_TAB = 16 << 9  # 8192 UILayout tab button identifier.
UI_BTYPE_BUT_MENU = 5 << 9
UI_BTYPE_PULLDOWN = 27 << 9
as_pointer = bpy.types.bpy_struct.as_pointer
source_rect = Rect()
target_rect = Rect()


def redraw_regions_safe(regions):
    """Safely redraw a list of regions"""
    if len(regions) < 2:
        regions[0].tag_redraw()
        return False
    regions_curr = set(utils.iter_regions(area_type='TEXT_EDITOR', region_type='HEADER'))
    
    for r in regions_curr:
        r.tag_redraw()
    return bool(set(regions) - regions_curr)


indmax = (*range(8192),)
def qenum(iterable):
    return zip(indmax, iterable)

def sqenum(iterable):
    return zip(iterable, indmax)


# Tab data:
# - One slot per region, dict access by region.
# - Tab labels full string.
# - Tab rect coords in region space (x1, y1, x2, y2)
# - Tab label coords in region space.
# TODO: move into Tabs class.
class TabsData(dict):
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __missing__(self, region):
        self[region] = data = []
        return data

    def nuke(self):
        for data in self.values():
            data.clear()
        self.clear()
        type(self)._instance = None


class Tabs:
    _instance = None

    def __del__(self):
        print("deleting tabs")

    # Assuming nothing holds a reference to _instance, this should cause the
    # tabs instance to be garbage collected.
    @classmethod
    def nuke(cls):
        if cls._instance is not None:
            cls._instance = None

    def __new__(cls):
        if not isinstance(cls._instance, cls):
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):

        # TODO: Tab colors are hardcoded for now. Should be customizable.
        self.tabrect = Rect(0.23, 0.23, 0.23, 1.0)
        self.actrect = Rect(0.6, 0.6, 0.6, 1.0)
        self.hovrect = Rect(0.3, 0.5, 0.8, 0.15)

        self.tabrect.set_border_color(0.14, 0.14, 0.14, 1.0)
        self.tabrect.set_roundness(2)

        self.wu = _system.wu
        self.acth = 2 * (self.wu * 0.05)  # Active tab decorator height.
        self.pad_init = 20

        self.pad = self.pad_init * (self.wu * 0.05)  # Horizontal tab padding.
        self.padh = self.pad // 2

        self.indices_prev = [None]          # List of previous indices
        self.labels_prev = [None]           # List of previous labels
        self.hover_active = None            # Global hover flag
        self.hover = extras.defdict_item()   # A default dict with None as fallback
        self.data = TabsData()
        self.width = 0                      # Width of all tabs in pixels
        self.offsx_prev = 0                 # Previous view2d offset
        self.wu = None

    def recalc(self):
        self.offsx_prev = None
        utils.redraw_editors(region_type='HEADER')

    def draw(self):
        if (region := _context.region) is not None:
            blf.enable(0, blf.SHADOW)
            blf.shadow(0, 3, 0.0, 0.0, 0.0, 0.7)
            self.hover_active = self.hover[region]

            for label, (x1, y1, x2, y2), labelpos in self.validate_tabs(region):
                # Draw tab rectangle
                blf.color(0, 0.6, 0.6, 0.6, 1.0)
                self.tabrect(x1, y1, x2 - x1, y2 - y1)

                # Draw tab hover highlight
                if label == self.hover_active:
                    blf.color(0, 0.85, 0.85, 0.85, 1.0)
                    self.hovrect(x1 + 1, 1, x2 - x1 - 1, y2 - y1 - 1)

                # Draw active marker
                if label == getattr(_context.edit_text, "name", ""):
                    blf.color(0, 0.85, 0.85, 0.85, 1.0)
                    self.hovrect(x1 + 1, 1, x2 - x1 - 1, y2 - y1 - 1)
                    self.actrect(x1 + 1, 1, x2 - x1 - 1, self.acth + 1)

                # Draw tab label
                blf.position(*labelpos)
                blf.draw(0, label)
            blf.disable(0, blf.SHADOW)

    def hover_set(self, region_target, label):
        self.hover[region_target] = self.hover_active = label
        regions = [region_target]
        for region, label in self.hover.items():
            if region != region_target and label is not None:
                    self.hover[region] = None
                    regions.append(region)
        if redraw_regions_safe(regions):
            print("needs clean")

    def hover_clear(self):
        if self.hover_active is not None:
            self.hover_active = None
            regions = []
            for region, label in self.hover.items():
                if label is not None:
                    self.hover[region] = None
                    regions.append(region)
            if redraw_regions_safe(regions):
                print("needs clean")

    def revalidate(self):
        """Force tabs to have their geometries re-calculated."""
        for region in utils.iter_regions('TEXT_EDITOR', 'HEADER'):
            self.data[region].clear()
            region.tag_redraw()

    def validate_tabs(self, region):
        """Fetch tabs, rebuilding them if necessary."""
        # When widget units change, re-validate.
        if self.wu != system.wu:
            self.wu = system.wu
            self.pad = self.pad_init * self.wu * 0.05
            self.padh = self.pad // 2
            self.acth = 2 * self.wu * 0.05
            self.labels_prev[:] = []
            utils.defer(utils.redraw_editors, delay=0.01, region_type='HEADER')

        # Get indices and reorder if required.
        texts = bpy.data.texts
        indices_curr = [t.tab_index for t in texts]
        if indices_curr != self.indices_prev:
            offs = 0
            end_i = len(texts) - 1
            for i, (tab_i, txt_i) in qenum(sorted(sqenum(indices_curr))):
                i -= offs
                if tab_i is -1:  # New tabs are moved to the end.
                    while end_i in indices_curr:
                        end_i -= 1
                    offs += 1
                    indices_curr[txt_i] = end_i
                elif tab_i != i:
                    indices_curr[txt_i] = i
            for t, i in zip(texts, indices_curr):
                t.tab_index = i
            self.indices_prev = indices_curr

        # Assign labels based on tab indices. Reuse the list.
        labels = indices_curr.copy()
        for t in texts:
            labels[t.tab_index] = t.name

        # Region horizontal pan offset
        offsx = region.offsetx
        # Even if indices match with previous, labels might have changed.
        if labels != self.labels_prev or offsx != self.offsx_prev or len(labels) != len(self.data[region]) - 1:
            self.calc_tab_geometry(region, labels, offsx)
        return self.data[region]

    # Calculate tab and label width, height, positions.
    def calc_tab_geometry(self, region, labels, offset, dimensions=blf.dimensions):
        if labels != self.labels_prev:
            utils.redraw_editors(region_type='HEADER')

        # Get the x position of the last element in region
        but = region.internal.uiblocks.first.contents.buttons.last
        _endx = but.contents.rect.xmax
        x = x_org = _endx - offset
        y2 = region.height - 1
        newbut_width = int(dimensions(0, "+")[0] + self.padh)
        height = dimensions(0, "W")[1]
        y = y2 // 2 - height // 2 + 1

        tab_data = self.data[region]
        tab_data[:] = [("+",
                        (x, 0, x + newbut_width, y2),
                        (0, x + self.padh // 2, y, 0))]
        x += newbut_width - 1
        # Tabs.
        for label in labels:
            width = int(dimensions(0, label)[0] + self.pad)
            tab_data.append((label,
                            (x, 0, x + width, y2),  # Tab position.
                            (0, x + self.padh, y, 0)))  # Label position.
            x += width - 1

        # Store label order, region x offset.
        self.width = x - x_org
        self.offsx_prev = offset
        self.labels_prev[:] = labels


def setdefault(obj, name, attr, getattr=getattr, setattr=setattr):
    try:
        return getattr(obj, name)
    except AttributeError:
        setattr(obj, name, attr)
    return attr


# Test action zone intersection.
# TODO: Use AZone rects instead.
def azone_isect_check(context, event):
    m = context.preferences.system.wu * 0.05

    # Area corner zones
    azh = int(13 * m)
    azw = int(9 * m)

    # Area edge zones
    aet = int(3 * m)  # Top
    aes = int(2 * m)  # Bottom / Sides

    area = context.area
    w = area.width
    h = area.height
    x = event.mouse_x - area.x
    y = event.mouse_y - area.y

    # Test area corners (split, join)
    if (0 < y < azh or h - azh < y < h) and \
       (0 < x < azw or w - azw < x < w):
        return True

    # Test area edges (resize zones)
    return (0 < y < aes or h - aet < y < h) or \
           (0 < x < aes or w - aes < x < w)


def isect_rect(x: int, y: int, rect: tuple[int, int, int, int]) -> bool:
    """Test if a 2D point intersects with a rectangle."""
    x1, y1, x2, y2 = rect
    if x1 <= x and x <= x2 and y1 <= y:
        return y <= y2
    return False


def isect_rect_label(x, y, tab_data):
    for label, rect, _ in tab_data[1:]:
        if isect_rect(x, y, rect):
            return label


# Check self.tab_rects for intersection. With a pixel tolerance of ytol.
def dst_isect_check(self, event):
    ytol = 100 * (system.wu * 0.05)
    mx = event.mouse_region_x
    my = event.mouse_region_y
    rects = self.tab_rects
    rlen = len(rects)
    assert (rlen > 1), "Needs at least two tabs"
    end_points = rects[::rlen - 1]

    def isect(rect):
        x1, y1, x2, y2 = rect
        if x1 <= mx and mx <= x2:
            return y1 - ytol <= my
        return False

    def isect_past(rect, right=False):
        x1, y1, x2, y2 = rect
        if mx >= x2 if right else mx <= x1:
            if y1 - ytol <= my:
                return my <= y2 + ytol
        return False

    # Find closest target.
    for label, rect, _ in rects:
        if label == self.src:
            continue
        if isect(rect):
            self.target_rect[:] = rect
            self.dst = label
            break

    else:
        # Allow overshooting intersection horizontally.
        for bool_, (label, rect, _) in enumerate(end_points):
            if isect_past(rect, bool_) and label != self.src:
                self.target_rect[:] = rect
                self.dst = label
                break

        # No intersection, clear target.
        else:
            self.target_rect.clear()
            self.dst = ""


def _run_script_button(layout, p):
    row = layout.row()
    row.emboss = 'NORMAL' if p.run_script_emboss else 'NONE'
    row.operator("text.run_script",
                 text="Run Script" if p.run_script_text else "",
                 icon='PLAY' if p.run_script_icon else 'NONE')


# Draw new header.
def draw_header(self, context):
    layout = self.layout
    # p = prefs()
    ptabs = utils.prefs().tabs

    # Default ui elements.
    default_row = layout.row()
    default_row.prop(context.area, "ui_type", text="", icon_only=True)
    default_row = layout.row(align=True)
    default_row.alignment = 'LEFT'
    menu = default_row.menu

    if ptabs.show_new_menus:
        menu("TEXT_MT_text")
        menu("TEXTENSION_MT_edit")

    else:
        for m in ("view", "text", "edit", "select", "format"):
            menu("TEXT_MT_" + m)

    menu("TEXT_MT_templates")

    # Run script left.
    if ptabs.run_script_position == 'LEFT':
        _run_script_button(layout, ptabs)

    # New tab, text tabs.
    tabs_row = layout.row(align=True)
    if ptabs.use_large_tabs:
        tabs_row.scale_y = 1.43

    # tabs = Tabs()
    row = layout.row()

    if ptabs.run_script_position == 'RIGHT':
        layout.separator(factor=0.5)
        _run_script_button(layout, ptabs)

    row.separator(factor=0.5)
    wu = context.preferences.system.wu
    # Pad the header to make space for tabs
    row.ui_units_x = tabs.width / wu


def _draw(self, context):
    for name, (x1, y1, x2, y2), labelpos in self.tab_rects:
        # Should be the tab under the mouse.
        if name == self.src:
            y1 = 0  # Must be 0, because blender puts it at -1.
            offsetx = self.dx
            offsety = self.dy

            x1 += offsetx
            x2 += offsetx
            y1 += offsety
            y2 += offsety

            region = context.region
            header_offset = self.header_height

            if region.type in 'WINDOW':
                window_offset = self.window_offset
                if self.footer_stacked:
                    if self.alignment == 'BOTTOM':
                        header_offset = -header_offset
                    window_offset += header_offset

                y1 += window_offset
                y2 += window_offset

            elif region.type == 'FOOTER':
                if self.alignment == 'BOTTOM':
                    header_offset = -header_offset
                y1 += header_offset
                y2 += header_offset

            # Draw target rect.
            if self.target_rect and region.type == 'HEADER':
                tx1, ty1, tx2, ty2 = self.target_rect
                target_rect(tx1, 0, tx2 - tx1, ty2 - ty1)

            # Draw source rect (draggable).
            source_rect(x1, y1, x2 - x1, y2 - y1)

            # Text coords are an approximation.
            w, h = blf.dimensions(0, name)
            x = x1 + ((x2 - x1) / 2) - (w / 2)
            y = y1 + ((y2 - y1) / 2) - (h / 2)
            blf.color(0, *(0.8,) * 4)
            blf.position(0, x, y, 1)
            blf.draw(0, name)
            break


def poll_edit_text(cls, context):
    return poll_area_texted(cls, context) and context.edit_text


def poll_area_texted(cls, context):
    area = context.area
    return area and area.type == 'TEXT_EDITOR'


# Region context menu as blender draws it internally.
class SCREEN_MT_region_context_menu_py(bpy.types.Menu):
    bl_label = ""

    poll = classmethod(poll_area_texted)

    def draw(self, context):
        layout = self.layout
        st = context.space_data
        separator = layout.separator
        operator = layout.operator
        region = context.region
        prop = layout.prop

        layout.label(text=region.type.title())
        separator()
        prop(st, "show_region_header", text="Show Header")

        if getattr(st, "show_region_footer", False):
            prop(st, "show_region_footer", text="Show Footer")

        separator()
        icon = "CHECKBOX_" + (context.area.show_menus and "HLT" or "DEHLT")
        operator("screen.header_toggle_menus", text="Show Menus", icon=icon)
        separator()

        if region.type in "HEADERFOOTER":
            layout.operator_context = "INVOKE_DEFAULT"
            aln = "Top" if region.alignment == "BOTTOM" else "Bottom"
            operator("screen.region_flip", text=f"Flip to {aln}")
            separator()

        operator("screen.screen_full_area", text="Maximize Area")


# Close tab operator.
# Does not inherit from TextOperator because it unlinks a text.
class TEXTENSION_OT_close_tab(bpy.types.Operator):
    bl_idname = "textension.close_tab"
    bl_label = "Close"
    bl_options = {'REGISTER', 'UNDO'}

    type: bpy.props.StringProperty()
    poll = utils.text_poll

    @classmethod
    def description(cls, context, operator):
        return {"CLOSE": "Close current tab",
                "CLOSE_OTHER": "Close other tabs",
                "CLOSE_ALL": "Close all tabs"}[operator.type]

    def execute(self, context):
        unlink = bpy.ops.text.unlink
        if self.type == 'CLOSE':
            return unlink()
        elif self.type == 'CLOSE_OTHER':
            for text in bpy.data.texts:
                if text != context.edit_text:
                    unlink({"edit_text": text})
        elif self.type == 'CLOSE_ALL':
            for text in bpy.data.texts:
                unlink({"edit_text": text})
        utils.redraw_editors(region_type='HEADER')
        return {'FINISHED'}


class TEXTENSION_OT_tab_context_menu(TextOperator):

    @classmethod
    def poll(cls, context):
        area = context.area
        return area and area.type == 'TEXT_EDITOR' and \
            context.region.type == 'HEADER'

    def draw_menu(self, context, event, tab_name):
        menu = context.window_manager.popmenu_begin__internal(tab_name)
        men = uiPopupMenu.from_address(menu.as_pointer())
        block = men.block

        assert block

        block = block.contents
        block.flag &= ~UI_BLOCK_POPUP_MEMORY
        layout = menu.layout

        col = layout.column(align=True)
        col.scale_y = 1.1

        col.operator_context = 'EXEC_DEFAULT'

        tlen = len(bpy.data.texts)
        text = bpy.data.texts[tab_name]
        col.context_pointer_set("edit_text", text)

        # The move operator actions are based on enums, so it's impossible to
        # different polls, so we emulate poll behavior here.
        col1 = col.column()
        col1.enabled = tlen > 1

        col2 = col.column()
        col2.enabled = text.tab_index > 0
        col2.operator("textension.move_tab",
                      text="Move Tab Left").type = 'LEFT'

        col3 = col.column()
        col3.enabled = text.tab_index < tlen - 1
        col3.operator("textension.move_tab",
                      text="Move Tab Right").type = 'RIGHT'

        col.separator(factor=0.5)
        col.operator_context = 'INVOKE_DEFAULT'
        col.operator("textension.rename_text", text="Rename")
        col.separator(factor=0.2)

        col.operator("textension.close_tab",
                     text="Close All Tabs").type = 'CLOSE_ALL'

        col4 = col.column()
        col4.enabled = tlen > 1
        col4.operator("textension.close_tab",
                      text="Close Other Tabs").type = 'CLOSE_OTHER'

        col.separator(factor=0.5)
        col.menu("SCREEN_MT_region_context_menu_py",
                 text=context.region.type.title())
        col.separator(factor=0.5)
        col.operator("textension.close_tab", text="Close").type = 'CLOSE'

        # Calculate the context menu popup position.
        # The menu's corner is aligned with the cursor, like normal software.
        mx, my = event.mouse_x, event.mouse_y
        context.window_manager.popmenu_end__internal(menu)

        rct = block.handle.contents.region.contents.winrct

        margin = context.preferences.system.dpi_fac * 12  # UI_POPUP_MARGIN
        width = rct.xmax - rct.xmin
        height = rct.ymax - rct.ymin

        # Menu flip logic, like normal software.
        if mx + width - (margin * 2) <= context.window.width:
            x1 = int(mx - margin)
        else:
            x1 = int(mx + margin) - width

        if my > height - (margin * 2):
            y1 = int(my - height + margin)
        else:
            y1 = int(my - margin)

        rct.set_position(x1, y1)

        # Moving the mouse should close the menu?
        # block.flag &= ~UI_BLOCK_MOVEMOUSE_QUIT

        # No ID data is being edited. Just cancel.
        return {'CANCELLED'}

    def invoke(self, context, event):
        if not azone_isect_check(context, event):
            x = event.mouse_region_x
            y = event.mouse_region_y

            region = context.region
            tab_name = isect_rect_label(x, y, tabs.data[region])

            if tab_name is not None:
                # Index above 0, should be a valid tab. Check with
                # bpy.data first.
                assert tab_name in bpy.data.texts
                return self.draw_menu(context, event, tab_name)
                # Right click on "+" shouldn't do anything. It's a dummy
                # button.
                return {'CANCELLED'}
        # Doesn't intersect with anything related to tabs. Pass event through.
        return {'PASS_THROUGH'}

    @classmethod
    def register_keymaps(cls):
        from .. import km_utils
        km_utils.kmi_args(cls, "Screen Editing", cls.bl_idname, 'PRESS')
        km_utils.kmi_new('RIGHTMOUSE', note="HIDDEN")


class TEXTENSION_OT_move_tab(TextOperator):
    type: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, operator):
        return {"LEFT": "Move tab to the left",
                "RIGHT": "Move tab to the right"}[operator.type]

    def execute(self, context):
        src_index = context.edit_text.tab_index
        dst_index = None
        texts = bpy.data.texts
        if self.type == "LEFT" and src_index > 0:
            dst_index = src_index - 1
        elif self.type == "RIGHT" and src_index < len(texts) - 1:
            dst_index = src_index + 1

        if dst_index is not None:
            for text in texts:
                if text.tab_index == dst_index:
                    text.tab_index = src_index
                    context.edit_text.tab_index = dst_index
                    return {'FINISHED'}
        return {'CANCELLED'}


def check_drag(x, y, threshold={*range(-4, 5)}):
    if x not in threshold:
        return True
    return y not in threshold


# Get the next tab on the right, or fallback to left.
def remove_get_next_tab(name):
    texts = bpy.data.texts
    act = texts.get(name)
    assert act is not None

    t_idx = act.tab_index
    t_next = None
    texts.remove(act)

    for t in texts:
        if t.tab_index == t_idx + 1:
            return t
        elif t_next is None and t.tab_index == t_idx - 1:
            t_next = t

    return t_next


# Does not inherit from TextOperator because it unlinks a text.
class TEXTENSION_OT_tab_operation(bpy.types.Operator):
    bl_label = "Tab Operation"
    bl_idname = "textension.tab_operation"
    bl_options = {'REGISTER', 'UNDO'}
    skip_events = {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'TIMER', 'EVT_TWEAK_L',
                   'TIMER_REPORT'}
    action: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    tab_rects = None

    @classmethod
    def poll(cls, context, *, is_spacetext=bpy.types.SpaceTextEditor.__instancecheck__):
        if is_spacetext(context.space_data):
            return context.region.type == 'HEADER'
        return False

    @classmethod
    def register_keymaps(cls):
        from .. import km_utils
        km_utils.kmi_args(cls, "Screen Editing", cls.bl_idname, 'PRESS')
        km_utils.kmi_new('LEFTMOUSE', note="HIDDEN")
        km_utils.kmi_new(cls, "View2D", cls.bl_idname, 'MIDDLEMOUSE',
                         'PRESS', note="Close Tab", ).action = 'CLOSE'

    def invoke(self, context, event):
        # It's possible the intersection touches corner handles.
        # In those cases pass the event.
        if azone_isect_check(context, event):
            return {'PASS_THROUGH'}

        region = context.region
        st = context.space_data
        x = event.mouse_region_x
        y = event.mouse_region_y
        tab_rects = tabs.data[region]

        # Might have disabled the tabs.
        assert(tab_rects), (f"Missing tabs?: {tab_rects}")

        # Mouse intersects with "+" tab, make new text block.
        if isect_rect(x, y, tab_rects[0][1]):
            st.text = bpy.data.texts.new(name="Text")
            return {'FINISHED'}

        self.is_mmb = event.type == 'MIDDLEMOUSE'
        # Intersects with text tab. Activate or drag.
        self.src = isect_rect_label(x, y, tab_rects)
        if self.src is not None:
            text = bpy.data.texts.get(self.src)
            if self.action != 'CLOSE':
                st.text = text
                if len(tab_rects[1:]) < 2:
                    return {'FINISHED'}

            self.tab_rects = tab_rects[1:]

            if prefs().tabs.drag_to_reorder:
                header, window = context.area.regions[:][::3]
                self.alignment = header.alignment
                self.header_height = header.height
                self.window_offset = -self.header_height

                if self.alignment == 'TOP':
                    self.window_offset = window.height

                self.target_rect = []
                self.dst = ""
                self.x = event.mouse_x
                self.y = event.mouse_y
                self.dx = self.dy = 0
                self.footer_stacked = self.drag_init = False

                context.window_manager.modal_handler_add(self)
                context.window.cursor_modal_set('DEFAULT')
                return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}

    def end(self, context, cancel=False):
        self.draw_state_set(context, False)
        if cancel:
            return {'CANCELLED'}
        return {'FINISHED'}

    def swap_tabs(self):
        src = bpy.data.texts.get(self.src)
        dst = bpy.data.texts.get(self.dst)
        if src and dst:
            src.tab_index, dst.tab_index = dst.tab_index, src.tab_index
            tabs.revalidate()
            return True
        return False

    def modal(self, context, event):
        self.dx = event.mouse_x - self.x
        self.dy = event.mouse_y - self.y

        if self.is_mmb:
            if event.type == 'MOUSEMOVE' and check_drag(self.dx, self.dy):
                bpy.ops.view2d.pan('INVOKE_DEFAULT')
                return self.end(context, cancel=True)

            elif event.type == 'MIDDLEMOUSE' and event.value == 'RELEASE':
                # TODO: Check contents before remove. Add confirmation.
                context.space_data.text = remove_get_next_tab(self.src)
                return self.end(context)

        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            success = self.drag_init and self.swap_tabs()
            return self.end(context, cancel=not success)

        elif not self.drag_init:
            if event.type == 'MOUSEMOVE' and check_drag(self.dx, self.dy):
                self.draw_state_set(context, True)
                self.drag_init = True
        else:
            dst_isect_check(self, event)
            context.area.tag_redraw()
            if event.type not in self.skip_events:
                return self.end(context, cancel=True)
        return {'RUNNING_MODAL'}

    # TODO: Use unified draw.
    def draw_state_set(self, context, state):
        handles = setdefault(type(self), "_handles", {})
        st = context.space_data
        if state:
            assert not handles
            draw_regions = 'HEADER', 'WINDOW'

            # When header/footer regions are stacked, draw in both.
            if st.show_region_footer:
                if self.alignment == context.area.regions[1].alignment:
                    self.footer_stacked = True
                    draw_regions += 'FOOTER',

            for region_type in draw_regions:
                handles[region_type] = st.draw_handler_add(
                    _draw, (self, context), region_type, 'POST_PIXEL')
        else:
            for region in set(handles):
                st.draw_handler_remove(handles.pop(region), region)
        context.area.tag_redraw()


class TEXTENSION_MT_edit(bpy.types.Menu):
    bl_label = "Edit"

    def draw(self, layout):
        layout = self.layout
        column = layout.column(align=True)
        column.scale_y = 0.9

        column.operator("ed.undo")
        column.operator("ed.redo")
        column.separator()
        column.operator("textension.cut")
        column.operator("textension.copy", icon='COPYDOWN')
        column.operator("textension.paste", icon='PASTEDOWN')
        column.operator("text.duplicate_line")
        column.separator()
        column.menu("TEXT_MT_select")

        column.separator()
        column.operator("text.indent")
        column.operator("textension.unindent")
        column.separator()

        column.operator("textension.search_with_selection", text="Search..")
        column.operator("textension.goto", text="Go To..")

        column.separator()
        column.operator("text.comment_toggle")
        column.separator()
        column.menu("TEXT_MT_edit_to3d")


class BypassDraw:
    def __init__(self, func_org, func_new):
        self.func_org = func_org
        self.func_new = func_new

    def __call__(self, panel, context):
        self.func_new(panel, context)


def set_tabs_draw(state):
    if state and not utils.ud.is_registered("tabs"):

        utils.ud.add("tabs", tabs.draw, (), "HEADER", "POST_PIXEL")
        # Defer redraw so header has time to calculate positions.
        utils.defer(utils.redraw_editors, delay=0.01, region_type="HEADER")

    elif not state and utils.ud.is_registered("tabs"):
        utils.ud.remove("tabs")


def set_header_draw(self, context, *, state=None, state_prev=[False]):
    funcs = bpy.types.TEXT_HT_header._dyn_ui_initialize()
    state = self.show_tabs if state is None else state

    if state:
        if not state_prev[0]:
            for i, func in enumerate(funcs):
                if func.__module__ == 'bl_ui.space_text':
                    funcs[i] = BypassDraw(funcs[i], draw_header)
                    state_prev[0] = True
                    break

    else:
        if state_prev[0]:
            for i, func in enumerate(funcs):
                if isinstance(func, BypassDraw):
                    funcs[i] = func.func_org
                    state_prev[0] = False
                    break
    set_tabs_draw(state)


class TEXTENSION_PG_tabs(bpy.types.PropertyGroup):
    from bpy.props import BoolProperty, EnumProperty

    show_tabs: bpy.props.BoolProperty(
        description="Enable to use tabs in the text editor",
        name="Tabs and Header",
        default=True,
        update=utils.tag_userdef_modified_wrapper(set_header_draw),
    )
    fit_to_region: BoolProperty(
        description="Fit tabs to region width.\n"
        "Run Script button will be moved to the left",
        name="Fit tabs to region",
        default=False,
        update=utils.tag_userdef_modified_wrapper(lambda s, c: s._run_script_button_upd(2)),
    )
    drag_to_reorder: BoolProperty(
        description="Enable tab reordering by dragging them",
        name="Drag to Reorder",
        default=True,
        update=utils.tag_userdef_modified,
    )
    mmb_close: BoolProperty(
        description="Close tabs using middle mouse button",
        name="Middle Mouse Close",
        default=True,
        update=utils.tag_userdef_modified,
    )
    double_click_new: BoolProperty(
        description="Double click empty area to add new tab",
        name="Double-click New Tab",
        default=True,
        update=utils.tag_userdef_modified,
    )
    use_large_tabs: BoolProperty(
        description="Use large tabs",
        name="Use Large Tabs",
        default=True,
        update=utils.tag_userdef_modified,
    )

    def _run_script_button_upd(self, btn):
        """Update the Run Script button placement and text"""
        if btn == 2:
            if self.fit_to_region and "RIGHT" in self.run_script_position:
                self.run_script_position = "LEFT"

        elif btn == 3:
            if "RIGHT" in self.run_script_position:
                self.fit_to_region = False

        elif not self.run_script_text and not self.run_script_icon:
            if btn == 0:
                self.run_script_icon = True

            elif btn == 1:
                self.run_script_text = True
        tabs.recalc()

    show_new_menus: BoolProperty(
        name="Show New Menus",
        description="Show new menus with a condensed layout",
        default=False,
        update=lambda self, context: tabs.recalc(),
    )
    run_script_position: bpy.props.EnumProperty(
        description="Run Script button position relative to tabs",
        name="Run Script Position",
        items=(("RIGHT", "Right", "Run Script right side"),
               ("LEFT", "Left", "Run Script left side"),
               ("OFF", "Off", "Don't show Run Script button")),
        default="LEFT",
        update=lambda self, context: self._run_script_button_upd(3),
    )
    run_script_text: BoolProperty(
        description="Show Run Script text on button",
        name="Show Text",
        default=False,
        update=lambda self, context: self._run_script_button_upd(0),
    )
    run_script_icon: BoolProperty(
        description="Show Run Script icon",
        name="Show Icon",
        default=True,
        update=lambda self, context: self._run_script_button_upd(1),
    )
    run_script_emboss: BoolProperty(
        description="Use embossed button",
        name="Emboss",
        default=True,
        update=utils.tag_userdef_modified,
    )
    del BoolProperty, EnumProperty

    def draw(self, layout, context):
        layout.scale_y = 1.25
        # layout.scale_x = 0.8
        col = layout.column()
        # Tabs.
        col.label(text="Tabs")
        col.prop(self, "show_tabs")
        col.separator()
        col = col.column()
        col.enabled = self.show_tabs
        col.prop(self, "use_large_tabs")
        col.prop(self, "fit_to_region")

        col.prop(self, "drag_to_reorder")
        col.separator()
        col.prop(self, "mmb_close")
        col.prop(self, "double_click_new")

        # Run script.
        col = layout.column()
        col.label(text="Header")
        col.prop(self, "show_new_menus", text="Strip Menus")
        col.separator()
        col.prop(self, "run_script_position")
        # mainrow = column.column()
        # mainrow.alignment = 'CENTER'
        # mainrow.enabled = self.show_tabs
        # col.label(text="Run Script Button Position")

        col.label(text="Run Script Button")
        col.prop(self, "run_script_text")
        col.prop(self, "run_script_icon")
        col.prop(self, "run_script_emboss")


classes = (
    TEXTENSION_PG_tabs,
    TEXTENSION_MT_edit,
    TEXTENSION_OT_move_tab,
    TEXTENSION_OT_tab_operation,
    TEXTENSION_OT_close_tab,
    TEXTENSION_OT_tab_context_menu,
    SCREEN_MT_region_context_menu_py,
)


def revalidate_tabs():
    tabs.revalidate()

def clear_region_cache():
    tabs.data.clear()

def enable():
    # TODO: Store tabs in a sensible location
    global tabs
    tabs = Tabs()
    bpy.types.Text.tab_index = bpy.props.IntProperty(default=-1)
    utils.register_class_iter(classes)
    prefs = utils.prefs()
    type(prefs).tabs = bpy.props.PointerProperty(type=TEXTENSION_PG_tabs)
    set_header_draw(prefs.tabs, None)

    utils.add_hittest(test_tabs, region="HEADER")
    utils.watch_rna((bpy.types.Text, "name"), revalidate_tabs)
    # utils.watch_rna((bpy.types.Window, "workspace"), clear_region_cache)


def disable():
    utils.unwatch_rna(revalidate_tabs)
    # utils.unwatch_rna(clear_region_cache)
    utils.remove_hittest(test_tabs, region="HEADER")

    prefs = utils.prefs()
    set_header_draw(prefs.tabs, None, state=False)
    del type(prefs).tabs
    utils.unregister_class_iter(classes)
    del bpy.types.Text.tab_index
    Tabs.nuke()
    global tabs
    del tabs


def on_leave():
    tabs.hover_clear()

# def test_tabs(data: extras.HitTestData):
#     # Test against tabs
#     if data.prefs.tabs.show_tabs:
#         region = data.region
#         mrx, mry = data.pos
#         for label, rect, _ in tabs.data[region]:
#             if isect_rect(mrx, mry, rect):
#                 if tabs.hover[region] != label:
#                     tabs.hover_set(region, label)
#                 utils.set_hittest_fail_hook(on_leave)
#                 bpy.context.window.cursor_set("DEFAULT")
#                 return True
#         else:
#             if tabs.hover_active:
#                 tabs.hover_clear()
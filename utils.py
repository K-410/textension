import bpy
from . import types
from .types import UnifiedDraw, logger as log, TextUndo, Callable
import ctypes
from bpy.types import PropertyGroup
from bpy.props import CollectionProperty, BoolProperty, StringProperty,\
    IntProperty, EnumProperty, FloatProperty
import sys
from io import StringIO


# The real context
from _bpy import (context as _context,
                  data as _data)

# RNA subscription owner
SUBSCRIBE_OWNER = hash(__package__)
system = _context.preferences.system
log = log()


class Flag:
    _init_state = False
    _state = False
    _is_set = False

    def __init__(self, init_state=False):
        self._init_state = self._state = init_state

    def set(self):
        assert not self._is_set, "Flag already set"
        self._state = not self._init_state
        self._is_set = True

    def reset(self):
        self._state = self._init_state
        self._is_set = False

    def __bool__(self):
        return self._is_set

def get_caller_module(level):
    """
    Return the module this function was called from.
    """
    return sys.modules.get(sys._getframe(level).f_globals["__name__"])

def this_module():
    """
    Returns the module of the caller.
    """
    return sys.modules.get(sys._getframe(1).f_globals["__name__"])


_rna_hooks: dict[tuple, list[tuple[Callable, tuple]]] = {}
def on_rna_changed(key):
    assert key in _rna_hooks
    for func, args in _rna_hooks[key]:
        func(*args)

def watch_rna(key, notify: Callable, args=()):
    if not (hooks := _rna_hooks.setdefault(key, [])):
        bpy.msgbus.subscribe_rna(key=key, owner=key, args=(key,), notify=on_rna_changed)
    hooks.append((notify, args))


def unwatch_rna(notify: Callable):
    """Remove a callback from rna watch.
    """
    for key, hooks in list(_rna_hooks.items()):
        for func, args in hooks:
            if func == notify:
                hooks.remove((func, args))
                break
        if not hooks:
            bpy.msgbus.clear_by_owner(key)
            del _rna_hooks[key]



def register_class(cls):
    bpy.utils.register_class(cls)
    log("REG", cls.__name__)


def unregister_class(cls):
    bpy.utils.unregister_class(cls)
    log("UNREG", cls.__name__)


def register_class_iter(classes):
    for cls_ in classes:
        register_class(cls_)


def unregister_class_iter(classes):
    for cls_ in classes:
        unregister_class(cls_)


def clamp(val, a, b):
    if val < a:
        return a
    elif val > b:
        return b
    return val


def clamp_factory(lower, upper):
    def inner(val, a=lower, b=upper):
        if val < a:
            return a
        elif val > b:
            return b
        return val
    return inner


# Linear value to srgb. Assumes a 0-1 range.
def lin2srgb(lin):
    if lin > 0.0031308:
        return 1.055 * (lin ** (1.0 / 2.4)) - 0.055
    return 12.92 * lin


def get_scrollbar_x_points(region_width):
    """
    Given a region width in pixels, return the x1 and x2 points
    of the scrollbar.
    """
    widget_unit = system.wu
    sx_2 = int(region_width - 0.2 * widget_unit)
    sx_1 = sx_2 - int(0.4 * widget_unit) + 2
    return sx_1, sx_2


def renum(iterable, len=len, zip=zip, range=range, reversed=reversed):
    """
    Reversed enumerator starting at the size of the iterable and decreasing.
    """
    lenit = len(iterable)
    start = lenit - 1
    return zip(range(start, start - lenit, -1), reversed(iterable))


# Default argument is bound when TextensionPreferences is registered.
def prefs(preferences=None):
    return preferences


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


def defer(func, *args, delay=0.0, **kw):
    bpy.app.timers.register(lambda: func(*args, **kw), first_interval=delay)


def setdefault(obj, key, value, get=False):
    """
    Simple utility to set a default attribute on any object.
    """
    if get:
        if not hasattr(obj, key):
            setattr(obj, key, value)
        return obj

    try:
        return getattr(obj, key)
    except AttributeError:
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


def prefs_modify_cb(self, context):
    prefs_modified_set()


def prefs_modified_set(prefs=bpy.context.preferences):
    if not prefs.is_dirty:
        prefs.is_dirty = True


def pmodify_wrap(func):
    def wrap(self, context, *args, **kwargs):
        ret = func(self, context, *args, **kwargs)
        prefs_modified_set()
        return ret
    return wrap


@types._inject_const(context=_context)
def iter_areas(area_type='TEXT_EDITOR'):
    for window in "const context".window_manager.windows:
        for area in window.screen.areas:
            if area.type == area_type:
                yield area


def iter_regions(area_type='TEXT_EDITOR', region_type='WINDOW'):
    for area in iter_areas(area_type):
        for region in area.regions:
            if region.type == region_type:
                yield region


def iter_spaces(space_type='TEXT_EDITOR'):
    for area in iter_areas(space_type):
        yield area.spaces.active


def redraw_editors(area='TEXT_EDITOR', region_type='WINDOW'):
    for region in iter_regions(area, region_type):
        region.tag_redraw()


def area_from_space_data(st):
    for area in st.id_data.areas:
        if area.spaces[0] == st:
            return area

def region_from_space_data(st, region_type='WINDOW'):
    for region in area_from_space_data(st).regions:
        if region.type == region_type:
            return region



def window_from_region(region: bpy.types.Region):
    for win in _context.window_manager.windows:
        if win.screen == region.id_data:
            return win


_region_types = set(
    bpy.types.Region.bl_rna.properties['type'].enum_items.keys())
_editors: dict[str: dict] = {}


def add_draw_hook(fn: Callable, space: bpy.types.Space, args: tuple=(), *, 
                  region: str='WINDOW', type: str='POST_PIXEL'):
    """
    Add a draw callback as a hook.
    """
    if not isinstance(args, tuple):
        args = (args,)

    assert isinstance(space, bpy.types.Space) or \
           issubclass(space, bpy.types.Space), f"Bad space, got {repr(space)}"
    # Not bulletproof. Some spaces don't have certain regions.
    assert region in _region_types, f"Bad region type: {repr(region)}"

    # Ensure arguments are bound reducing call overhead by 5%.
    # This unfortunately modifies 'fn', but the alternative (copy)
    # has a performance penalty and is unacceptable.
    if args and fn.__defaults__ is None:
        assert fn.__code__.co_argcount == len(args)
        fn.__defaults__ = args
    regions = _editors.setdefault(space, {})

    if region not in regions:
        # Region is new - register a region draw callback.
        hooks = [fn]
        def draw(hooks=hooks):
            for func in hooks:
                func()
        handle = space.draw_handler_add(draw, (), region, type)
        regions[region] = (handle, hooks)

    else:
        regions[region][1].append(fn)

def remove_draw_hook(fn: Callable, *, region: str='WINDOW'):
    """
    Remove a draw hook. Returns whether it was removed successfully.
    When the last hook is removed, the region callback is also removed.
    """
    found = False

    for space, regions in list(_editors.items()):
        if region in regions:
            handle, hooks = regions[region]
            if fn in hooks:
                found = True
                hooks.remove(fn)
                if not hooks:
                    space.draw_handler_remove(handle, region)
                    del regions[region]
            if not regions:
                del _editors[space]
        if found:
            break
    return found


def spacetext_cache_factory(cls):
    from .types import is_spacetext

    @types._inject_const(cache=(const_cache := {}), cls=cls, is_spacetext=is_spacetext)
    def instance_from_space(st: is_spacetext.__self__) -> cls:
        try:
            return "const cache"[st]
        except:  # Assume KeyError
            if not "const is_spacetext"(st):
                raise TypeError(f"Expected a SpaceTextEditor instance, got {st}")
            return "const cache".setdefault(st, "const cls"(st))
    instance_from_space.clear: dict.clear = const_cache.clear
    return instance_from_space

# Cache dict with its __missing__ set to "func". "args" is used by "func" and
# is changed by calling "params_set", which invalidates the cache.
def defcache(func, *args, fwd=False, unpack=False):
    all_caches = setdef(defcache, "all_caches", [])
    if unpack:
        def fallback(self, key, args=args):
            self[key] = func(*key, *args)
            return self[key]
    else:
        def fallback(self, key, args=args):
            self[key] = func(key, *args)
            return self[key]

    class Cache(dict):
        __missing__ = fallback
        @staticmethod
        def params_set(*args):
            clear()
            fallback.__defaults__ = (args,)
    cache = Cache()
    clear = cache.clear

    if fwd:
        if not isinstance(args, tuple):
            args = (args,)
        args += (cache,)

    cache.params_set(*args)
    all_caches.append(cache)
    return cache


def push_undo(text: bpy.types.Text):
    return TextUndo(text).push_undo()


def tag_modified(operator: bpy.types.Operator):
    """
    Calling this ensures that Textension operators that use TextUndo tags
    the blend file as dirty.
    """
    if not bpy.data.is_dirty:
        bpy.ops.ed.undo_push(message=operator.bl_label)


def _redraw_now() -> None:
    """
    Redraw immediately. Only use when the alternative isn't acceptable.
    """
    stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        bpy.ops.wm.redraw_timer(iterations=1)
    finally:
        sys.stdout = stdout
    return None

def add_hittest(func: Callable, region: str="WINDOW"):
    from . import TEXTENSION_OT_hit_test
    TEXTENSION_OT_hit_test.hooks[region].append(func)

def remove_hittest(func: Callable, region: str="WINDOW"):
    from . import TEXTENSION_OT_hit_test
    TEXTENSION_OT_hit_test.hooks[region].remove(func)

def hit_test(context):
    from . import TEXTENSION_OT_hit_test
    return TEXTENSION_OT_hit_test.hit_test(context)

def set_hittest_fail_hook(func):
    from . import TEXTENSION_OT_hit_test
    if func not in TEXTENSION_OT_hit_test.fail_hooks:
        TEXTENSION_OT_hit_test.fail_hooks.append(func)
    
# from time import perf_counter
# def _isect_check():
#     # from .scrollbar import thumb_vpos_calc
#     from bpy.types import SpaceTextEditor

#     # TODO: 3.0 workaround
#     # from .tabs import Tabs, isect_rect
#     # tabs = Tabs()
#     # TODO END

#     # hover_clear = tabs.hover_clear

#     # def scrollbar_test(context, x, y):
#     #     # Inside scrollbar?
#     #     # if not p.scrollbar.show_scrollbar:
#     #     #     return
#     #     region = context.region
#     #     h = region.height
#     #     w = region.width
#     #     wu = system.wu
#     #     wu_norm = wu * 0.05

#     #     # Test the scrollbar (horizontal intersection)
#     #     if w - int(wu - wu_norm) - 2 <= x:

#     #         # Test the sidebar toggle
#     #         ui = context.area.regions[2]
#     #         if ui.alignment == 'RIGHT' and ui.width == 1:
#     #             if x >= w - int(wu * 0.4) - 1 and x <= w:
#     #                 if y >= h - int(wu * 1.4) - 1:
#     #                     if y <= h - int(wu * 0.8 - wu_norm):
#     #                         return "AZONE"

#     #         # Test the scrollbar (vertical intersection)
#     #         if x <= w - 2 and y <= h and y >= 0:
#     #             ymin, ymax = thumb_vpos_calc(h, context.space_data)
#     #             if ymin > -1:
#     #                 if ymin <= y:
#     #                     if y <= ymax:
#     #                         return "THUMB"
#     #                     return "UP"
#     #                 return "DOWN"

#     # def test_line_numbers(context, x, y):
#     #     st = context.space_data
#     #     rh = context.region.height
#     #     if st.show_line_numbers:
#     #         if p.use_line_number_select:
#     #             if x <= st.lpad_px - (st.drawcache.cwidth_px + b"\n")[0]:
#     #                 if x >= 1 and y <= rh and y >= 0:
#     #                     return "LNUM"

#     # def test_tabs(context, x, y):
#     #     region = context.region
#     #     if p.tabs.show_tabs:
#     #         if region.type == 'HEADER':
#     #             for label, (x1, y1, x2, y2), _ in tabs.data[region]:
#     #                 if x1 <= x and x <= x2 and y1 <= y and y <= y2:
#     #                     if tabs.hover[region] != label:
#     #                         tabs.hover_set(region, label)
#     #                     return
#     #             hover_clear()

#     def inner(context, mpos):
#         st = context.space_data
#         if not isinstance(st, SpaceTextEditor):
#             # hover_clear()
#             return

#         region = context.region
#         region_type = region.type
#         x = mpos[0] - region.x
#         y = mpos[1] - region.y
#         rh = region.height
#         p = prefs()

#         # Inside window region.
#         # Test against tabs
#         # if p.tabs.show_tabs:
#         #     if region_type == 'HEADER':
#         #         for label, rect, _ in tabs.data[region]:
#         #             if isect_rect(x, y, rect):
#         #                 if tabs.hover[region] != label:
#         #                     tabs.hover_set(region, label)
#         #                 return
#         #     # Clear hover, if any
#         #     elif tabs.hover_active:
#         #         hover_clear()
#         #         return


#     def clear():
#         # nonlocal tabs, hover_clear
#         # del tabs, hover_clear

#         # nonlocal thumb_vpos_calc, SpaceTextEditor
#         nonlocal SpaceTextEditor
#         # del thumb_vpos_calc, SpaceTextEditor
#         del SpaceTextEditor
#         nonlocal inner
#         del inner

#     inner.clear = clear
#     del clear, Tabs
#     return inner




class Operators(PropertyGroup):
    
    class KeymapData(PropertyGroup):
        str: StringProperty()

    @classmethod
    def register(cls):
        bpy.utils.register_class(cls.KeymapData)
        cls.kmidata = CollectionProperty(type=cls.KeymapData)

    @classmethod
    def unregister(cls):
        bpy.utils.unregister_class(cls.KeymapData)
        del cls.kmidata


to_string, from_str = serialize_factory()


class TextensionRuntime(PropertyGroup):
    """
    Runtime settings @ bpy.context.window_manager.textension
    """

    tab: EnumProperty(items=(('CORE',    "Core",    "Core settings"),
                             ('PLUGINS', "Plugins", "Plugins settings"),
                             ('KEYMAPS', "Keymaps", "Keymaps settings")))

    show_all_kmi: BoolProperty(default=False)

    @classmethod
    def register(cls):
        bpy.types.WindowManager.textension = bpy.props.PointerProperty(type=cls)

    @classmethod
    def unregister(cls):
        assert cls.is_registered
        del bpy.types.WindowManager.textension


class TextensionPreferences(bpy.types.AddonPreferences,
                            PropertyGroup):
    """Textension Addon Preferences"""
    bl_idname = __package__
    from .km_utils import kmi_cb

    # Internal.
    # Keep a simple track of install date to determine first install, so we
    # can grab and set default syntax colors based on the active theme.
    install_date: StringProperty(options={'HIDDEN'})

    wheel_scroll_lines: IntProperty(
        name="Scroll Lines", description="Lines to scroll per wheel tick",
        default=3, min=1, max=10,
    )
    nudge_scroll_lines: IntProperty(
        name="Nudge Lines", description="Lines to nudge with Ctrl-Up/Down",
        default=3, min=1, max=10,
    )
    use_smooth_scroll: BoolProperty(
        name="Smooth Scrolling", description="Smooth scroll with mouse wheel",
        default=True, update=kmi_cb("scroll2", "use_smooth_scroll"),
    )
    scroll_speed: FloatProperty(
        name="Scroll Speed", description="Scroll speed multiplier",
        default=5, min=1, max=20,
    )
    use_continuous_scroll: BoolProperty(
        name="Continuous Scrolling",
        description="Enable continuous scrolling with middle mouse",
        default=True,
        update=kmi_cb("scroll_continuous", "use_continuous_scroll"),
    )
    closing_bracket: BoolProperty(
        name="Close Brackets", description="Automatically close brackets",
        default=True,
    )
    use_new_linebreak: BoolProperty(
        name="New Line Break",
        description="Use line break which adds indentation",
        default=True,
        update=kmi_cb("line_break", "use_new_linebreak"),
    )
    use_home_toggle: BoolProperty(
        name="Home Toggle",
        description="Home key toggles between line start and indent level",
        default=True,
        update=kmi_cb("move_toggle", "use_home_toggle"),
    )
    use_search_word: BoolProperty(
        default=True, name="Search by Selection",
        description="Selected word is automatically copied to search field",
        update=kmi_cb("search_with_selection", "use_search_word"),
    )
    use_cursor_history: BoolProperty(
        name="Use Cursor History",
        description="Enable to use cursor history with mouse 4/5",
        default=True,
        update=kmi_cb("cursor_history", "use_cursor_history"),
    )
    use_header_toggle: BoolProperty(
        name="Toggle Header", description="Toggle header with hotkey (Alt)",
        default=True, update=kmi_cb("toggle_header", "use_header_toggle"),
    )
    use_line_number_select: BoolProperty(
        name="Line Number Select",
        description="Select lines from line number margin",
        default=True,
        update=kmi_cb("line_select", "use_line_number_select"),
    )
    triple_click: EnumProperty(
        name="Triple-Click",
        description="Type of selection when doing a triple click",
        default='LINE',
        items=(("LINE", "Line", "Select entire line"),
               ("PATH", "Path", "Select entire python path")),
    )

    def draw_plugins(self, context, layout):

        # def plugin_layout(layout, plugin):
            # layout.separator()

        layout.use_property_split = True
        layout.use_property_decorate = False
        for plugin in self.plugins:
            module = plugin.module
            box = layout.box()
            row = box.row(heading=plugin.name.replace("_", " ").title())
            split = row.split()
            row = split.row()
            row.prop(plugin, "enabled", text="Enable", toggle=True)

            row.label()

            if plugin.enabled:
                draw_func = getattr(module, "draw_settings", None)

                # For custom plugin draw method
                if draw_func is not None:
                    if plugin.show_settings:
                        col = box.column()
                        col.separator(factor=0.5)
                        draw_func(self, context, col)
                        col.separator(factor=0.5)

                    kwargs = {"text": "Settings", "toggle": True, "icon": "NONE"}

                    # If the poll returns False, set an error icon
                    if getattr(module, "poll_plugin", None.__class__)() is False:
                        kwargs["icon"] = "ERROR"

                    row.prop(plugin, "show_settings", **kwargs)
                else:
                    row.label()
            else:
                row.label()
            # plugin_layout(layout, p)

    def draw_keymaps(self, context, layout):
        col = layout.column(align=True)
        # rw = context.region.width

        rw = context.region.width
        textension = context.window_manager.textension
        show_all = textension.show_all_kmi
        text = "Show All" if not show_all else "Collapse"
        col.prop(textension, "show_all_kmi", text=text, emboss=False)
        col.separator(factor=2)

        kmi_range = slice(19 if not show_all else None)

        # Custom keymaps are stored on each operator.
        for cls in classes()[kmi_range]:
            keymaps = getattr(cls, "_keymaps", ())
            for idx, (km, kmi, note) in enumerate(keymaps):
                if note == "HIDDEN":
                    continue

                km_utils.kmi_ensure(cls, idx, kmi)
                self.draw_kmi(col, rw, kmi, note)

        col.separator(factor=2)
        if show_all:
            col.prop(textension, "show_all_kmi", text=text, emboss=False)


    def draw(self, context):
        layout = self.layout

        row = layout.row()
        row.prop(self.runtime, "tab", expand=True)
        layout.separator()

        row = layout.row()
        row.alignment = 'CENTER'
        col = row.column(align=True)
        # col.ui_units_x = 20

        if self.runtime.tab == 'PLUGINS':
            self.draw_plugins(context, col)
        elif self.runtime.tab == 'KEYMAPS':
            self.draw_keymaps(context, col)

        """
        textension = context.window_manager.textension
        if textension is None:
            return
        tab = textension.tab
        rw = context.region.width
        if tab != 'KEYMAP' and textension.show_all_kmi:
            textension.show_all_kmi = False
        show_all = textension.show_all_kmi

        layout = self.layout
        mainrow = layout.row()

        # Tab column.
        tabcol = mainrow.column()
        tabcol.alignment = 'LEFT'
        tabcol.ui_units_x = 3.5
        tabcol.scale_y = 1.1
        tabcol.prop(textension, "tab", expand=True)

        row = mainrow.row()
        row.alignment = 'CENTER'

        def rowgroup(layout, title):
            if isinstance(title, tuple):
                prop = title
            else:
                prop = None
            row = layout.row()
            row.alignment = 'LEFT'
            row.ui_units_x = 20
            col = row.column()
            col.ui_units_x = 20
            b = col.box()
            b.scale_y = 0.5
            if prop:
                state = getattr(*prop)
                r = b.row()
                r.alignment = 'LEFT'
                icon = "CHECKBOX_HLT" if state else "CHECKBOX_DEHLT"
                r.emboss = 'NONE'
                r.prop(*prop, icon=icon)
            else:
                b.label(text=title)
            col.separator(factor=0.2)
            r = col.row()
            return r

        def prop_split(layout, data, prop, **kw):
            col = layout.column(align=True)
            split = col.split(factor=0.25)
            split.prop(data, prop, text="", **kw)
            title = data.__annotations__[prop][1]["name"]
            split.label(text=title)

        def rpad(layout, align=True):
            r = layout.row(align=align)
            r.alignment = 'LEFT'
            return r

        self.tabcol = tabcol
        self.mainrow = mainrow
        self.row = row
        # self.colgroup = colgroup

        if tab == 'MAIN':
            col = row.column()
            columns = int(context.region.width / 405)
            flow = col.grid_flow(columns=columns, row_major=True, align=True)

            # Navigation.
            rg = rowgroup(flow, "Navigation")
            c = rg.column()
            c.prop(self, "use_smooth_scroll")
            c.prop(self, "use_continuous_scroll")
            c.prop(self, "use_home_toggle")

            c = rg.column()
            c.prop(self, "scroll_speed")
            c.prop(self, "wheel_scroll_lines")
            c.prop(self, "nudge_scroll_lines")
            c.separator(factor=4)

            # Editing.
            rg = rowgroup(flow, "Editing")
            c = rg.column()
            c.prop(self, "use_line_number_select")
            c.prop(self, "use_search_word")
            c.prop(self, "use_new_linebreak")
            c.prop(self, "closing_bracket")
            c.separator(factor=4)
            c = rg.column()
            c.ui_units_x = 8.2
            c.prop(self, "use_header_toggle")
            c.separator(factor=1)
            c.label(text="Triple Click Select")
            r = rpad(c)
            r.prop(self, "triple_click", expand=True)

            # Cursor.
            # syntax = self.syntax
            rg = rowgroup(flow, "Cursor")
            c = rg.column()
            c.prop(self, "use_cursor_history")

            # Row > align left > column > ui_units 1
            r = c.row(align=True)
            r.alignment = 'LEFT'
            cs = r.column()
            # cs.prop(syntax.colors, "currct", text="")
            cs.ui_units_x = 1
            r.separator(factor=0.4)
            r.label(text="Cursor Color")

            c.separator()
            r = c.row(align=True)
            r.alignment = 'LEFT'
            cs = r.column()
            cs.label(text="Cursor Type")
            # cs.prop(syntax, "cursor_type", text="")

            c = rg.column()
            c.ui_units_x = 8
            # c.prop(syntax, "smooth_cursor")
            r = c.row()
            c = r.column()
            # c.enabled = syntax.smooth_cursor
            # c.prop(syntax, "smooth_typing")
            c.separator(factor=1)

            r = c.row(align=True)
            r1 = r.row()
            r1.label(text="Direction")
            r2 = r.row(align=True)
            r2.alignment = 'RIGHT'
            r2.ui_units_x = 6
            # r2.prop(syntax, "smooth_horz", toggle=True)
            # r2.prop(syntax, "smooth_vert", toggle=True)
            r = c.row()
            r1 = r.row(align=True)
            r1.alignment = 'LEFT'
            r1.label(text="Speed")
            r2 = r.row(align=True)
            r2.alignment = 'RIGHT'
            c = r2.column()
            # c.prop(syntax, "cursor_speed", slider=True)
            c.ui_units_x = 6
            c.separator(factor=4)

            # Tabs.
            tabs = self.tabs
            rg = rowgroup(flow, (tabs, "show_tabs"))
            if tabs.show_tabs:
                c = rg.column(align=True)
                c.prop(tabs, "use_large_tabs")
                c.prop(tabs, "fit_to_region")
                c.separator()
                c.prop(tabs, "drag_to_reorder")
                c.prop(tabs, "mmb_close")
                c.prop(tabs, "double_click_new")

                c = rg.column(align=True)
                c.ui_units_x = 8
                c.prop(tabs, "show_new_menus")
                c.separator()
                c.label(text="Run Script Button")
                c.prop(tabs, "run_script_text")
                c.prop(tabs, "run_script_icon")
                c.prop(tabs, "run_script_emboss")
                c.separator()

            # rg = rowgroup(flow, "Tabs")

        if tab == 'HIGHLIGHT':
            self.draw_hlite_prefs(self.highlights, context, row)

        # Draw custom keymaps.
        if tab == 'KEYMAP':
            col = row.column(align=True)
            # rw = context.region.width

            text = "Show All" if not show_all else "Collapse"
            col.prop(textension, "show_all_kmi", text=text, emboss=False)
            col.separator(factor=2)

            kmi_range = slice(19 if not show_all else None)

            # Custom keymaps are stored on each operator.
            for cls in classes()[kmi_range]:
                keymaps = getattr(cls, "_keymaps", ())
                for idx, (km, kmi, note) in enumerate(keymaps):
                    if note == "HIDDEN":
                        continue

                    km_utils.kmi_ensure(cls, idx, kmi)
                    self.draw_kmi(col, rw, kmi, note)

            col.separator(factor=2)
            if show_all:
                col.prop(textension, "show_all_kmi", text=text, emboss=False)

        # elif tab == 'SYNTAX':
        #     self.syntax.draw(self, context)
        # elif tab == 'TABS':
        #     self.tabs.draw(self.layout, context)
        layout.separator(factor=4)
"""
    @classmethod
    def register(cls):
        p = bpy.context.preferences.addons[__package__].preferences
        # Bind prefs default arg for faster access
        prefs.__defaults__ = (p,)

        global ud
        ud = UnifiedDraw()

        # Register textension operator kmi data.
        register_class(Operators)
        cls.operators = CollectionProperty(type=Operators)

        from .types import Plugin
        register_class(Plugin)
        cls.plugins = CollectionProperty(type=Plugin)

        plugins = p.plugins
        from . import get_plugins
        plugins_dict = get_plugins()

        # If a previously found plugin no longer exists, remove its entry.
        for index, plugin in reversed(list(enumerate(plugins))):
            if plugin.name not in plugins_dict:
                plugins.remove(index)

        for name, module in plugins_dict.items():
            plugin: Plugin = plugins.get(name)

            if plugin is None:
                plugin = plugins.add()
                plugin.name = name
                plugin.full_name = module.__name__

            if plugin.enabled:
                plugin.module.enable()


        global km_utils
        from . import km_utils
        cls.kmi_ensure = staticmethod(km_utils.kmi_ensure)
        cls.draw_kmi = staticmethod(km_utils.draw_kmi)

        # Convenience access to TextensionRuntime instance
        register_class(TextensionRuntime)
        cls.runtime = bpy.context.window_manager.textension

        # TODO: Move into cls (used for kmi display).
        global classes

        operators = p.operators
        from . import classes
        for cls_ in classes():
            if operators.get(cls_.__name__):
                continue
            op = operators.add()
            op.name = cls_.__name__

        msg = f"Previously installed ({p.install_date})"

        if not p.install_date:
            msg = "First time install"
            from time import asctime
            cls._first_time_install = True
            p.install_date = f"{asctime()}"

        log("ADDON", msg)

    @classmethod
    def unregister(cls):
        p = prefs()

        for plugin in p.plugins:
            if plugin.enabled:
                plugin.module.disable()

        from .types import Plugin
        unregister_class(Plugin)
        del cls.plugins

        unregister_class(TextensionRuntime)
        del cls.runtime

        unregister_class(Operators)
        del cls.operators

        del cls.kmi_ensure
        del cls.draw_kmi

        global ud
        ud.nuke()
        del ud
        caches = getattr(defcache, "all_caches", [])
        for c in caches:
            c.clear()
        caches.clear()

        assert not bool(_editors), f"_editors not cleared, {_editors}"

        # Unbind and invalidate prefs
        prefs.__defaults__ = (None,)


del serialize_factory

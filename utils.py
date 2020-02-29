import bpy
import ctypes

kmi_fields = ("type", "value", "alt", "ctrl", "shift",
              "any", "key_modifier", "oskey", "active")


def bpy_version_check():
    if bpy.app.version[1] < 82:
        raise Exception("\nMinimum Blender version 2.82 required\n")


def _prefs():
    addons = bpy.context.preferences.addons
    _prefs = addons[__package__].preferences

    def get_prefs():
        nonlocal _prefs
        if _prefs is None:
            _prefs = addons[__package__].preferences
        return _prefs

    return get_prefs


# Quick and safe way of storing kmi data as literals
def serialize_factory():
    from json import dumps, loads
    from operator import attrgetter
    getattrs = attrgetter(*kmi_fields)

    def to_string(kmi):
        return dumps(getattrs(kmi))

    def from_str(string):
        return tuple(loads(string))
    return to_string, from_str


to_string, from_str = serialize_factory()


class Mixin:
    @classmethod
    def poll(cls, context):
        return getattr(context, "edit_text", False)

    # Runs after class registration.
    @classmethod
    def register(cls):

        # Register keymaps first.
        # Set macro defines, etc.
        call(cls, "register_keymaps")
        call(cls, "_register")

        keymaps = getattr(cls, "_keymaps", None)
        if keymaps is None:
            return

        # Handle custom keymap edits.
        data = prefs().operators[cls.__name__].kmidata
        if len(data) == len(keymaps):
            for (_, kmi, _), _kmi in zip(keymaps, data):
                for key, val in zip(kmi_fields, from_str(_kmi.str)):
                    if getattr(kmi, key) != val:
                        setattr(kmi, key, val)

        # Addon is registered for first time. Create kmidata.
        elif keymaps:
            # km, kmi, note
            for _, kmi, _ in keymaps:
                item = data.add()
                item.name = kmi.idname
                item.str = to_string(kmi)

        # Shouldn't happen, unless we add/remove default addon keymaps
        # TODO: Should this be handled?
        # else:
        #     raise ValueError("length mismatch %s" % cls)

    @classmethod
    def unregister(cls):
        call(cls, "_unregister")
        kmi_remove(cls)


def call(obj, key):
    try:
        return getattr(obj, key)()
    except (AttributeError, TypeError):
        return False


class TextOperator(Mixin, bpy.types.Operator):
    pass


class TextMacro(Mixin, bpy.types.Macro):
    pass


class classproperty(property):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._get = self.fget.__get__
        setdefault(self, "_set", getattr(self.fset, "__get__", None))
        setdefault(self, "_del", getattr(self.fdel, "__get__", None))

    def __get__(self, instance, cls):
        return self._get(None, cls)()

    def __set__(self, instance, value):
        return self._set(None, type(instance))(value)

    def __delete__(self, instance):
        cls = type(instance)
        return self._del(None, cls)(cls)


# Run by register() in __init__.py to check if keymaps are ready. When they
# are, this function will call register on its own.
def keymaps_ensure(register, keymaps):
    km = bpy.context.window_manager.keyconfigs.active.keymaps
    if all(km_name in km for km_name in keymaps):
        return register(ready=True)

    elif setdefault(keymaps_ensure, "retries", 10) > 0:
        keymaps_ensure.retries -= 1
        return defer_call(0.2, register)

    else:
        # Could happen if blender's keymaps were somehow missing.
        raise Exception("Default keymaps are corrupt. Restore Blender to "
                        "factory defaults before enabling this addon")


def wunits_get():
    from bpy.utils import _preferences
    system = _preferences.system
    ui_scale = wunits = None

    # Get the widget unit based on current ui scale.
    def _wunits_get() -> int:
        nonlocal ui_scale, wunits
        if ui_scale != system.ui_scale:
            ui_scale = system.ui_scale
            p = system.pixel_size
            pd = p * system.dpi
            wunits = int((pd * 20 + 36) / 72 + (2 * (p - pd // 72)))
        return wunits
    return _wunits_get


wunits_get = wunits_get()


def iadd_default(obj, key, default, value):
    ivalue = getattr(obj, key, default) + value
    setattr(obj, key, ivalue)
    return ivalue


# Set kmi args context so subsequent additions are less verbose.
def kmi_args(*args, **kwargs):
    if kwargs:
        kmi_args.kwargs = kwargs
    else:
        kmi_args.kwargs = {}
    if len(args) == 4:
        kmi_args.args = args
    else:
        args = iter(args)
        kmi_args.args = [next(args, d) for d in kmi_args.args]


# Add a new keymap item.
# def kmi_new(cls, kmname, idname, type, value, **kwargs):
def kmi_new(*args, **kwargs):
    if len(args) < 5:
        kwargs.update(kmi_args.kwargs)
        # Type only supplied
        if len(args) == 1:
            type, = args
            cls, kmname, idname, value = kmi_args.args
    else:
        cls, kmname, idname, type, value = args
    kc = bpy.context.window_manager.keyconfigs
    active = kc.active.keymaps.get(kmname)
    if not active:
        raise KeyError(f"{kmname} not in active keyconfig")

    keymaps = kc.addon.keymaps
    km = keymaps.get(kmname)
    if not km:
        # If an addon keymap doesn't exist, template it from active keyconfig.
        km = keymaps.new(kmname,
                         space_type=active.space_type,
                         region_type=active.region_type,
                         modal=active.is_modal)

    note = kwargs.pop("note", "")
    _kmi_new = km.keymap_items.new
    if km.is_modal:
        _kmi_new = km.keymap_items.new_modal
    kmi = _kmi_new(idname, type, value, **kwargs)
    setdefault(cls, "_keymaps", []).append((km, kmi, note))
    return kmi.properties


# Remove (addon) keymap item.
def kmi_remove(cls):
    for km, kmi, _ in setdefault(cls, "_keymaps", []):
        km.keymap_items.remove(kmi)
    cls._keymaps.clear()

    for kmi in setdefault(cls, "_disabled", []):
        kmi.active = True
    cls._disabled.clear()


def defer_call(delay, func, *args, **kwargs):
    from bpy.app.timers import register
    register(lambda: func(*args, **kwargs), first_interval=delay)


# Disable a keymap item
def kmi_mute(cls, space: str, **kwargs):

    # Should not happen unless argument is invalid
    if setdefault(kmi_mute, "retries", 10) <= 0:
        idname = kwargs.get("idname")
        print("Error muting: %s on %s" % (idname, cls))
        # raise Exception("%s on %s" % (cls, idname))

    kc = bpy.context.window_manager.keyconfigs.active
    km = kc.keymaps.get(space)
    if km:
        for kmi in km.keymap_items:
            for key, val in kwargs.items():
                if getattr(kmi, key) != val and val is not None:
                    break
            else:
                kmi.active = False
                setdefault(cls, "_disabled", []).append(kmi)
                return

        else:
            kmi_mute.retries -= 1
            defer_call(0.2, kmi_mute, cls, space, **kwargs)
            return

    # Keymap doesn't exist???
    raise Exception("%s missing from keymap %s" % (space, kc))


# Simple utility to set a default attribute on any object.
def setdefault(obj, key, value, get=False):
    if get:
        if not hasattr(obj, key):
            setattr(obj, key, value)
        return obj

    try:
        return getattr(obj, key)
    except AttributeError:
        setattr(obj, key, value)
        return value


def _kmi_sync():
    _cls = None
    data = None
    op_get = prefs().operators.get

    def kmi_ensure(cls, idx, kmi):
        nonlocal _cls, data

        if _cls != cls:
            _cls = cls
            data = op_get(cls.__name__).kmidata

        kmistr = to_string(kmi)
        item = data[idx]
        if item.str != kmistr:
            item.str = kmistr
    return op_get, kmi_ensure


class KeymapData(bpy.types.PropertyGroup):
    str: bpy.props.StringProperty()


class Operators(bpy.types.PropertyGroup):
    kmidata: bpy.props.CollectionProperty(type=KeymapData)


# Update kmi states (disable/enable) based on user preferences.
def kmi_update(idname, state, all=True):
    from . import classes

    for cls in classes():
        operator = op_get(cls.__name__)

        # Not handling unless it happens at least once.
        if not operator:
            print("Unhandled exception: missing operator", cls.__name__)
            return

        keymaps = getattr(cls, "_keymaps", ())
        for idx, (km, kmi, _) in enumerate(keymaps):
            if kmi.idname.split(".")[1] != idname:
                continue
            kmi.active = state
            operator.kmidata[idx].str = to_string(kmi)
            if not all:
                break


# kmi update callback.
def kmi_cb(idname, key, all=True):
    def cb(self, context):
        state = getattr(self, key)
        return kmi_update(idname, state, all)
    return cb


class TextensionPreferences(bpy.types.AddonPreferences):
    """Textension Addon Preferences"""
    bl_idname = __package__
    from .highlights import HighlightOccurrencesPrefs, draw_hlite_prefs
    if not HighlightOccurrencesPrefs.is_registered:
        bpy.utils.register_class(HighlightOccurrencesPrefs)
    # Custom keymaps per operator are stored here.
    operators: bpy.props.CollectionProperty(type=Operators)
    highlights: bpy.props.PointerProperty(type=HighlightOccurrencesPrefs)

    # Internal. Do not display in preferences ui!
    show_line_highlight: bpy.props.BoolProperty(default=False)
    show_line_numbers: bpy.props.BoolProperty(default=True)
    show_syntax_highlight: bpy.props.BoolProperty(default=True)
    show_word_wrap: bpy.props.BoolProperty(default=True)

    tab: bpy.props.EnumProperty(
        default="SETTINGS",
        name="Preferences Tab",
        items=(
            ("SETTINGS", "Settings", "Main settings tab"),
            ("HIGHLIGHT", "Highlight", "Highlight settings tab"),
            ("KEYMAP", "Keymap", "Keymap settings tab")))

    wheel_scroll_lines: bpy.props.IntProperty(
        default=3,
        name="Wheel Scroll Lines",
        description="Amount of lines to scroll per wheel tick",
        min=1,
        max=100)

    nudge_scroll_lines: bpy.props.IntProperty(
        default=3,
        name="Nudge Scroll Lines",
        description="Amount of lines to nudge scroll (ctrl + arrows)",
        min=1,
        max=100)

    use_smooth_scroll: bpy.props.BoolProperty(
        default=True,
        name="Smooth Scrolling",
        description="Allow smooth scrolling with mouse wheel",
        update=kmi_cb("scroll2", "use_smooth_scroll"))

    scroll_speed: bpy.props.FloatProperty(
        default=5.0,
        name="Smooth Scroll Speed",
        description="Wheel scroll speed multiplier",
        min=1,
        max=10)

    use_continuous_scroll: bpy.props.BoolProperty(
        default=True,
        name="Continuous Scroll",
        description="Enable continuous scrolling (default middle mouse)",
        update=kmi_cb("scroll_continuous", "use_continuous_scroll"))

    closing_bracket: bpy.props.BoolProperty(
        default=True, name="Close Brackets", description="Automatically add "
        "closing bracket")

    use_new_linebreak: bpy.props.BoolProperty(
        default=True,
        name="New Line Break",
        description="Use new line break which adds indentation",
        update=kmi_cb("line_break", "use_new_linebreak"))

    use_home_toggle: bpy.props.BoolProperty(
        default=True,
        name="Home Toggle",
        description="Home toggles between line start and first word",
        update=kmi_cb("move_toggle", "use_home_toggle"))

    use_search_word: bpy.props.BoolProperty(
        default=True,
        name="Search by Selection",
        description="Selected word is automatically copied to search field",
        update=kmi_cb("search_with_selection", "use_search_word"))

    use_cursor_history: bpy.props.BoolProperty(
        default=True,
        name="Enable Cursor History",
        description="Enable to make use of cursor history",
        update=kmi_cb("cursor_history", "use_cursor_history"))

    use_header_toggle: bpy.props.BoolProperty(
        default=True,
        name="Toggle Header Hotkey",
        description="Toggle text editor header with hotkey (default Alt)",
        update=kmi_cb("toggle_header", "use_header_toggle"))

    use_line_number_select: bpy.props.BoolProperty(
        default=True,
        name="Line Number Select",
        description="Enable to make line selection from line number margin",
        update=kmi_cb("line_select", "use_line_number_select"))

    triple_click: bpy.props.EnumProperty(
        default='LINE',
        name="Triple Click",
        description="Type of selection when doing a triple click",
        items=(("LINE", "Line", "Select entire line"),
               ("PATH", "Path", "Select entire python path")))

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.alignment = 'CENTER'
        row.scale_y = 1.25
        row.prop(self, "tab", expand=True)
        layout.separator(factor=2)

        mainrow = layout.row()
        mainrow.alignment = 'CENTER'

        if self.tab == 'SETTINGS':
            col = mainrow.column()
            col.scale_y = 1.25
            col.prop(self, "wheel_scroll_lines")
            col.prop(self, "nudge_scroll_lines")
            col.prop(self, "scroll_speed", slider=True)
            col.separator()
            row = col.row()
            row.scale_x = 0.4
            split = row.split(factor=0.55)
            split.label(text="Triple click selects ..")
            split.row().prop(self, "triple_click", expand=True)

            layout.separator(factor=2)
            row = layout.row()
            row.scale_y = 1.25
            row.alignment = 'CENTER'
            col = row.grid_flow(columns=2)
            col.prop(self, "use_smooth_scroll")
            col.prop(self, "use_continuous_scroll")

            col.prop(self, "closing_bracket")
            col.prop(self, "use_new_linebreak")
            col.prop(self, "use_line_number_select")
            col.prop(self, "use_home_toggle")
            col.prop(self, "use_search_word")
            col.prop(self, "use_cursor_history")
            col.prop(self, "use_header_toggle")

        elif self.tab == 'HIGHLIGHT':
            self.draw_hlite_prefs(self.highlights, context, mainrow)

        elif self.tab == 'KEYMAP':
            col = mainrow.column(align=True)
            rw = context.region.width
            for cls in classes():
                keymaps = getattr(cls, "_keymaps", ())
                for idx, (km, kmi, note) in enumerate(keymaps):
                    if note == "HIDDEN":
                        continue

                    kmi_ensure(cls, idx, kmi)
                    draw_kmi(col, rw, kmi, note)

    @classmethod
    def register(cls):
        global op_get, kmi_ensure, prefs, classes
        from . import classes

        prefs = _prefs()
        op_get, kmi_ensure = _kmi_sync()

        addon = bpy.context.preferences.addons[__package__]
        operators = addon.preferences.operators
        from . import classes

        for cls in classes():

            # TODO: Add check for keymap length?
            if operators.get(cls.__name__):
                continue
            op = operators.add()
            op.name = cls.__name__


def draw_kmi(layout, rw, kmi, note):
    layout.ui_units_x = min(20, rw / 20)
    # layout.ui_units_x = 20
    mainrow = layout.row()
    mainrow.prop(kmi, "active", text="", emboss=False)
    mainrow.active = kmi.active
    mainrow.emboss = 'NONE'
    mainrow.prop(kmi, "show_expanded", text=note or kmi.name)
    if kmi.active:
        row = mainrow.row(align=True)
        row.emboss = 'NORMAL'
        row.prop(kmi, "type", text="", full_event=True)
        if kmi.show_expanded:
            split = layout.split(factor=0.1)
            split.separator()
            split = split.split(factor=0.47)
            row = split.row()
            row.emboss = 'NORMAL'
            row.prop(kmi, "map_type", text="")
            row = split.row(align=True)
            row.alignment = 'CENTER'
            if kmi.map_type != 'KEYBOARD':
                row.prop(kmi, "type", text="")
            prop = row.prop
            prop(kmi, "alt", toggle=True)
            prop(kmi, "ctrl", toggle=True)
            prop(kmi, "shift", toggle=True)
            prop(kmi, "oskey", toggle=True, text="OS")
            row = row.row(align=True)
            row.scale_x = 0.5
            row.prop(kmi, "key_modifier", event=True, text="")
    else:
        kmi.show_expanded = False
    mainrow.active = kmi.active


# Utils for accessing C structs
class ID(ctypes.Structure):
    pass


class TextLine(ctypes.Structure):
    pass


class Text(ctypes.Structure):
    pass


class SpaceText(ctypes.Structure):
    pass


class SpaceText_Runtime(ctypes.Structure):
    pass


class wmEvent(ctypes.Structure):
    def __init__(self):
        import ctypes  # Not sure why linting fails without this?
        p = ctypes.POINTER(wmEvent)
        from_address = p.from_address

        def cast(ptr):
            return p(from_address(ptr))
        self.cast = cast

    def __call__(self, ptr):
        ret = self.cast(ptr)
        if ret and ret.contents:
            return ret.contents
        return None


def listbase(type_=None):
    import ctypes
    type_ptr = ctypes.POINTER(type_)
    fields = ("first", type_ptr), ("last", type_ptr)
    return type("ListBase", (ctypes.Structure,), {'_fields_': fields})


id_p = ctypes.POINTER(ID)
textline_p = ctypes.POINTER(TextLine)
spacetext_rt_p = ctypes.POINTER(SpaceText_Runtime)

ID._fields_ = (
    ("next", ctypes.c_void_p),
    ("prev", ctypes.c_void_p),
    ("newid", ctypes.c_void_p),
    ("lib", ctypes.c_void_p),
    ("name", ctypes.c_char * 66),
    ("flag", ctypes.c_short),
    ("tag", ctypes.c_int),
    ("us", ctypes.c_int),
    ("icon_id", ctypes.c_int),
    ("recalc", ctypes.c_int),
    ("_pad[4]", ctypes.c_char * 4),
    ("properties", ctypes.c_void_p),
    ("override_library", ctypes.c_void_p),
    ("orig_id", id_p),
    ("pyinstance", ctypes.c_void_p)
)
TextLine._fields_ = (
    ("next", textline_p),
    ("prev", textline_p),
    ("line", ctypes.c_char_p),
    ("format", ctypes.c_char_p),
    ("len", ctypes.c_int),
    ("_pad0", ctypes.c_char * 4)
)
Text._fields_ = (
    ("id", ID),
    ("name", ctypes.c_char_p),
    ("compiled", ctypes.c_void_p),
    ("flags", ctypes.c_int),
    ("nlines", ctypes.c_int),
    ("lines", listbase(type_=TextLine)),
    ("curl", textline_p),
    ("sell", textline_p),
    ("curc", ctypes.c_int),
    ("selc", ctypes.c_int),
    ("mtime", ctypes.c_double)
)
SpaceText_Runtime._fields_ = (
    ("lheight_px", ctypes.c_int),
    ("cwidth_px", ctypes.c_int),
    ("scroll_rects", ctypes.c_int * 4 * 2),
    ("line_numbers", ctypes.c_int),
    ("viewlines", ctypes.c_int),
    ("scroll_px_per_line", ctypes.c_float),
    ("_offs_px", ctypes.c_int * 2)
)
SpaceText._fields_ = (
    ("links", ctypes.c_void_p * 2),
    ("regionbase", ctypes.c_void_p * 2),
    ("spacetype", ctypes.c_char),
    ("link_flag", ctypes.c_char),
    ("pad0", ctypes.c_char * 6),
    ("text", ctypes.c_void_p),
    ("top", ctypes.c_int),
    ("left", ctypes.c_int),
    ("_pad1", ctypes.c_char * 4),
    ("flags", ctypes.c_short),
    ("lheight", ctypes.c_short),
    ("tabnumber", ctypes.c_int),
    ("wordwrap", ctypes.c_char),
    ("doplugins", ctypes.c_char),
    ("showlinenrs", ctypes.c_char),
    ("showsyntax", ctypes.c_char),
    ("line_hlight", ctypes.c_char),
    ("overwrite", ctypes.c_char),
    ("live_edit", ctypes.c_char),
    ("_pad2", ctypes.c_char),
    ("findstr", ctypes.c_char * 256),
    ("replacestr", ctypes.c_char * 256),
    ("margin_column", ctypes.c_short),
    ("_pad3", ctypes.c_char * 2),
    ("runtime", SpaceText_Runtime),
)

wmEvent._fields_ = (
    ("next", ctypes.POINTER(wmEvent)),
    ("prev", ctypes.POINTER(wmEvent)),
    ("type", ctypes.c_short)
)
Event = wmEvent()
ST_RUNTIME_OFFS = SpaceText.runtime.offset
SCROLL_OFFS = ST_RUNTIME_OFFS + SpaceText_Runtime._offs_px.offset
ST_FLAGS_OFFS = SpaceText.flags.offset


def _scroll_offset_get():
    import ctypes
    p = ctypes.POINTER(ctypes.c_int * 2)
    from_addr = p.from_address

    def scroll_offset_get(context):
        ret = p(from_addr(SCROLL_OFFS + context.space_data.as_pointer()))

        if ret and ret.contents:
            return ret.contents[1]
        return 0
    return scroll_offset_get


scroll_offset_get = _scroll_offset_get()


def st_runtime():
    from ctypes import POINTER, cast, c_short
    c_short_p = POINTER(c_short)
    st_runtime_p = POINTER(SpaceText_Runtime)

    # Wrapper for space text runtime data.
    # scroll_max: maximum st.top allowed to scroll.
    class STRuntime(SpaceText_Runtime):
        def __init__(self, context, scroll_max=None):
            self.st = st = context.space_data
            if st.type != 'TEXT_EDITOR':
                raise ValueError

            st_p = st.as_pointer()

            # ST_SCROLL_SELECT is assumed (1 << 0)
            # Must be enabled to allow scrolling.
            cast(st_p + ST_FLAGS_OFFS, c_short_p).contents.value |= 0x1

            runtime = cast(st_p + ST_RUNTIME_OFFS, st_runtime_p).contents
            self.offsets = runtime._offs_px
            self.lheight = int(1.3 * runtime.lheight_px)
            self.accum = 0
            if scroll_max is None:
                scroll_max = len(st.text.lines)
            self.scroll_max = scroll_max

        @property
        def offset_px(self):
            return 0

        # Setting this affects draw offsets directly.
        @offset_px.setter
        def offs_px(self, value):
            st = self.st
            lheight = self.lheight
            offsets = self.offsets
            # y_offset = offsets[1]
            lines = 0
            top = st.top

            temp = value + self.accum
            px = round(temp)
            self.accum = temp - px
            scroll_max = self.scroll_max

            # Clamp top and bottom.
            if top >= scroll_max and px > 0:
                st.top = scroll_max
                offsets[1] = 0
                return
            elif top < 1:
                if top < 0:
                    st.top = 0
                if px < 0:
                    offsets[1] = 0
                    return

            px_tot = offsets[1] + px

            if px_tot < 0:
                lines = int((-lheight + px_tot) / lheight)
                px_tot += lheight * abs(lines)

            elif px_tot > 0:
                lines = int(px_tot / lheight)
                px_tot -= lheight * abs(lines)

            st.top += lines
            px_tot -= offsets[1]
            offsets[1] += px_tot
    return STRuntime


STRuntime = st_runtime()

del ctypes, id_p, textline_p, st_runtime

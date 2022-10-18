import bpy
from .utils import prefs, setdefault, defer, to_string


# Return addon keymap by name, or create a new if it doesn't exist.
def addon_km_from_name(name, kc=None):
    if kc is None:
        kc = bpy.context.window_manager.keyconfigs
    km = kc.addon.keymaps.get(name)

    if km is None:
        kmdef = kc.default.keymaps.get(name)
        km = kc.addon.keymaps.new(
            name, space_type=kmdef.space_type,
            region_type=kmdef.region_type, modal=km.is_modal)
    return km


# Run by register() in __init__.py to check if keymaps are ready. When they
# are, this function will call register on its own.
def keymaps_ensure(register, keymaps):
    active = getattr(keymaps_ensure, "active", None)
    kc = bpy.context.window_manager.keyconfigs
    if active is None:
        active = kc.active
    km = active.keymaps
    # All keymaps found, register addon.
    if all(km_name in km for km_name in keymaps):
        return register(ready=True)

    # If not ready, defer registration up to 30 times or roughly 6 seconds.
    elif setdefault(keymaps_ensure, "retries", 30) > 0:
        keymaps_ensure.retries -= 1
        return defer(register)

    # If that fails, try default keyconfig as fallback.
    elif getattr(keymaps_ensure, "active", None) is None:
        keymaps_ensure.active = kc.default
        keymaps_ensure.retries = 30
        return defer(register)

    # When all else fails, print the active keymap and what keymaps exist.
    else:
        print("Textension: Failed in finding default keymaps")
        for km_name in keymaps:
            if km_name not in km:
                print("Keymap %s missing in %s." % (km_name, kc.active.name))
        print("Keymaps found:")
        print(km.keys())
        # Could happen if blender's keymaps were somehow missing.
        # raise Exception("Default keymaps are corrupt. Restore Blender to "
        #                 "factory defaults before enabling this addon")


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
def kmi_new(*args, **kwargs):
    if len(args) < 5:
        kwargs.update(kmi_args.kwargs)
        # Type only supplied
        if len(args) == 1:
            type, = args
            cls, kmname, idname, value = kmi_args.args
    else:
        cls, kmname, idname, type, value = args

    kmi_args.args = cls, kmname, idname, value

    # If fallback exists, use it instead of active.
    kc = bpy.context.window_manager.keyconfigs
    if hasattr(keymaps_ensure, "kc"):
        active = kc
    else:
        active = kc.active.keymaps.get(kmname)
    if not active:
        raise KeyError(f"{kmname} not in active keyconfig")

    # km = addon_km_from_name(kmname, kc)
    keymaps = kc.addon.keymaps
    km = keymaps.get(kmname)
    if not km:
        km = keymaps.new(kmname,
                         space_type=active.space_type,
                         region_type=active.region_type,
                         modal=active.is_modal)

    # Notes are generally for displaying a custom label in the kmi ui, but
    # can also be used to hide internal kmis with "HIDDEN".
    note = kwargs.pop("note", "")
    _kmi_new = km.keymap_items.new
    if km.is_modal:
        _kmi_new = km.keymap_items.new_modal
    kmi = _kmi_new(idname, type, value, **kwargs)
    setdefault(cls, "_keymaps", []).append((km, kmi, note))
    kmi_new.properties = kmi.properties
    return kmi.properties


# Allow defining keymap item properties for last added keymap item.
def kmi_op_args(**kwargs):
    for key, val in kwargs.items():
        setattr(kmi_new.properties, key, val)
    del kmi_new.properties


# Remove (addon) keymap item.
def kmi_remove(cls):
    for km, kmi, _ in setdefault(cls, "_keymaps", []):
        km.keymap_items.remove(kmi)
    del cls._keymaps[:], cls._keymaps

    for kmi in setdefault(cls, "_disabled", []):
        kmi.active = True
    del cls._disabled[:], cls._disabled


# Disable a keymap item
def kmi_mute(cls, space: str, **kwargs):
    # Should not happen unless argument is invalid
    if setdefault(kmi_mute, "retries", 10) <= 0:
        print("Error muting: %s on %s" % (kwargs.get("idname"), cls))

    kc = bpy.context.window_manager.keyconfigs.active
    km = kc.keymaps.get(space)
    if km:
        for kmi in km.keymap_items:
            for key, val in kwargs.items():
                if getattr(kmi, key) != val and val is not None:
                    break
            else:
                kmi.active = False
                return setdefault(cls, "_disabled", []).append(kmi)

        else:
            kmi_mute.retries -= 1
            return defer(kmi_mute, cls, space, delay=0.2, **kwargs)

    # Keymap doesn't exist???
    raise Exception("%s missing from keymap %s" % (space, kc))


def kmi_ensure(cls, idx, kmi):
    global kmi_ensure
    _cls = data = None

    def inner(cls, idx, kmi):
        nonlocal _cls, data

        # Cache the class since it's possible we're still using it.
        if _cls != cls:
            _cls = cls
            operators = prefs().operators
            assert operators
            data = operators.get(cls.__name__).kmidata

        kmistr = to_string(kmi)
        item = data[idx]
        if item.str != kmistr:
            item.str = kmistr
    kmi_ensure = inner


# Update kmi states (disable/enable) based on user preferences.
def kmi_update(idname, state, all=True):
    from . import classes

    op_get = prefs().operators.get
    for cls in classes():
        operator = op_get(cls.__name__)

        # Not handling unless it happens at least once.
        if not operator:
            continue

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
        return kmi_update(idname, getattr(self, key), all)
    return cb


def draw_kmi(layout, rw, kmi, note):
    active = kmi.active
    layout.emboss = 'NONE' if not active else 'NORMAL'
    c = layout.column()
    b = c.box()
    rr = b.row(align=True)

    rr.prop(kmi, "active", text="", emboss=False)
    r = rr.row(align=True)
    r.prop(kmi, "show_expanded", text=note or kmi.name, emboss=False)
    r.enabled = active
    if not active:
        if kmi.show_expanded:
            kmi.show_expanded = False

    r = rr.row()
    r.enabled = active
    r.prop(kmi, "type", text="", full_event=True)
    r.ui_units_x = 7

    if kmi.show_expanded:
        rr = b.row()
        rr.enabled = active
        rr.scale_y = 1.1
        rr.alignment = 'EXPAND'
        r = rr.row(align=True)
        r.ui_units_x = 4
        r.prop(kmi, "map_type", text="")
        r = rr.row(align=True)
        r.ui_units_x = 7
        if kmi.map_type != 'KEYBOARD':
            r.prop(kmi, "type", text="")
        prop = r.prop
        prop(kmi, "alt", toggle=True)
        prop(kmi, "ctrl", toggle=True)
        prop(kmi, "shift", toggle=True)
        prop(kmi, "oskey", toggle=True, text="OS")
        r = rr.row()
        r.ui_units_x = 6.2
        r.prop(kmi, "key_modifier", event=True, text="")
        c.separator()

"""This module implements Textension preferences."""

import bpy
from . import utils
from _bpy import context as _context


system = _context.preferences.system


def init():
    utils.register_classes(classes)
    _init_plugins()


def cleanup():
    prefs = get_prefs()
    for plugin in prefs.plugins:
        if plugin.enabled:
            plugin.module.disable()

    utils.unregister_classes(classes)


def _init_plugins():
    prefs = get_prefs()
    plugins = prefs.plugins
    plugins_dict = get_plugins()

    # If a previously found plugin no longer exists, remove its entry.
    for index, plugin in reversed(list(enumerate(plugins))):
        if plugin.name not in plugins_dict:
            plugins.remove(index)

    for name, module in plugins_dict.items():
        plugin: Plugin = plugins.get(name)
        if not plugin:
            plugin = plugins.add()
            plugin.name = name
            plugin.full_name = module.__name__

        if plugin.enabled:
            plugin.module.enable()

    # Sort plugins alphabetically.
    for index, name in enumerate(sorted(p.name for p in plugins)):
        plugins.move(plugins.find(name), index)


def get_prefs():
    return _context.preferences.addons["textension"].preferences


def resolve_prefs_path(path, coerce=True):
    from operator import attrgetter

    obj_path, name = path.rpartition(".")[::2]

    obj  = attrgetter(obj_path)(get_prefs())
    attr = getattr(obj, name)
    if coerce:
        return attr
    return (obj, attr)


def get_plugins() -> dict:
    """Return a list of plugin submodules that can be enabled."""
    import textension
    import pkgutil
    import os
    plugins_path = os.path.join(textension.__path__[0], ".\\plugins")

    if not os.path.isdir(plugins_path):
        print("Textension: missing 'plugins' directory.")
        return
    plugins = {}
    py_path = f"{__package__}.plugins"
    for info in pkgutil.iter_modules([plugins_path]):
        m = __import__(f"{py_path}.{info.name}", fromlist=(info.name,))
        if hasattr(m, "enable") and hasattr(m, "disable"):
            plugins[info.name] = m
    return plugins


def add_settings(cls) -> bpy.types.PropertyGroup:
    """Add a PropertyGroup to Textension AddonPreferences. The property
    name will be derived from the part after _PG_ of class' name.
    """
    assert "_PG_" in cls.__name__, f"Expected _PG_ in {cls}"
    name = cls.__name__.split("_PG_")[-1]
    prefs = get_prefs()
    setattr(type(prefs), name, bpy.props.PointerProperty(type=cls))
    return getattr(prefs, name)


def remove_settings(cls):
    for value in Preferences.__dict__.values():
        kw = getattr(value, "keywords", None)
        if isinstance(kw, dict) and kw.get("type", None) == cls:
            if isinstance(attr := kw.get("attr", None), str):
                delattr(Preferences, attr)
                return


def enum_plugin_settings(self, context, *, data=[]):
    if not data:
        data += ("general", "", ""),
        for plugin in get_prefs().plugins:
            data += (plugin.name, "", ""),
    return data


class Runtime(bpy.types.PropertyGroup):
    show_all_kmi: bpy.props.BoolProperty(default=False)
    tab: bpy.props.EnumProperty(
        items=(('CORE',    "Core",    "Core settings"),
               ('PLUGINS', "Plugins", "Plugins settings"),
               ('KEYMAPS', "Keymaps", "Keymaps settings")))
    active_plugin_settings: bpy.props.EnumProperty(items=enum_plugin_settings)

    @classmethod
    def register(cls):
        bpy.types.WindowManager.textension = bpy.props.PointerProperty(type=cls)

    @classmethod
    def unregister(cls):
        del bpy.types.WindowManager.textension


class Plugin(bpy.types.PropertyGroup):
    @property
    def module(self):
        import importlib
        return importlib.import_module(self.full_name)

    @property
    def title(self):
        return self.name.replace("_", " ").title()

    def on_enabled(self, context):
        if self.enabled:
            self.module.enable()
        else:
            self.module.disable()
        context.preferences.is_dirty = True

    def on_show_settings(self, context):
        Runtime.active_plugin = self.name

    # The full name as used by importlib's import_module
    full_name: bpy.props.StringProperty()

    # Whether the plugin is enabled
    enabled: bpy.props.BoolProperty(update=on_enabled)

    # Whether a previously enabled plugin is missing
    missing: bpy.props.BoolProperty()

    # Whether to show plugin settings in the ui
    show_settings: bpy.props.BoolProperty(
        default=False,
        description="Show this plugin's settings",
        update=on_show_settings)


class TogglePlugin(bpy.types.Operator):
    bl_idname = "textension.toggle_plugin"
    bl_label = ""
    bl_options = {'INTERNAL'}

    @classmethod
    def description(cls, context, operator):
        if plugin := getattr(context, "plugin", None):
            prefix = "Disable " if plugin.enabled else "Enable "
            return prefix + plugin.title
        return ""

    def execute(self, context):
        if plugin := getattr(context, "plugin", None):
            plugin.enabled = not plugin.enabled

            if not plugin.enabled:
                runtime = context.window_manager.textension
                if runtime.active_plugin_settings == plugin.name:
                    assert plugin.name != "general"
                    runtime.active_plugin_settings = "general"
        return {'CANCELLED'}


class ShowSettings(bpy.types.Operator):
    bl_idname  = "textension.show_settings"
    bl_options = {'INTERNAL'}
    bl_label   = ""

    @classmethod
    def description(cls, context, operator):
        if plugin := getattr(context, "plugin", None):
            return f'Show {plugin.title} Settings'
        return "Show General Settings"

    def execute(self, context):
        name = "general"
        if plugin := getattr(context, "plugin", None):
            name = plugin.name
        context.window_manager.textension.active_plugin_settings = name
        return {'CANCELLED'}



class Preferences(bpy.types.AddonPreferences, bpy.types.PropertyGroup):
    bl_idname = __package__

    plugins: bpy.props.CollectionProperty(type=Plugin)

    def draw(self, context):
        layout = self.layout
        layout = layout.row()
        layout.alignment = 'CENTER'
        layout = layout.column()
        layout.ui_units_x = 32

        lrow = layout.row()
        lrow.alignment = 'EXPAND'

        ratio = max(1.0, (32 / ((context.region.width / system.wu) - 1.5)))

        left = lrow.column(align=True)
        left.ui_units_x = 8.5 * ratio
        left.ui_units_y = 14

        plugin_draw_func = None
        runtime = context.window_manager.textension
        active_plugin = runtime.active_plugin_settings

        row = left.box().row()
        row.alignment = 'RIGHT'
        row.label()

        row1 = row.row()
        row1.label(text="General")
        row1.alignment = 'LEFT'

        row2 = row.row()
        row2.alignment = 'RIGHT'
        if is_active := active_plugin == "general":
            plugin_draw_func = self.draw_general

        row2.operator("textension.show_settings", text="", icon="OPTIONS", depress=is_active)

        for plugin in self.plugins:
            row = left.box().row()
            row.context_pointer_set("plugin", plugin)
            row.emboss = 'NORMAL'
            row.alignment = 'RIGHT'

            is_enabled = plugin.enabled
            row.operator("textension.toggle_plugin", text="", icon='QUIT', depress=is_enabled)

            row1 = row.row()
            row1.label(text=plugin.title)
            row1.alignment = 'LEFT'
            row1.enabled = plugin.enabled

            draw_func = getattr(plugin.module, "draw_settings", None)

            row2 = row.row()
            row2.alignment = 'RIGHT'
            if is_enabled and draw_func:
                if is_active := active_plugin == plugin.name:
                    plugin_draw_func = draw_func
                row2.operator("textension.show_settings", text="", icon="OPTIONS", depress=is_active)

        if not plugin_draw_func:
            runtime.active_plugin = "general"
            plugin_draw_func = self.draw_general

        col = lrow.column()
        row = col.row(align=True)
        row.separator(factor=0.3)
        col = row.column()
        col.ui_units_x = 20
        box = col.column()

        if plugin_draw_func and active_plugin is not "none":
            row = box.row()
            row.alignment = 'EXPAND'
            # row.scale_y = 0.7
            row.box().label(text=active_plugin.replace("_", " ").title() + " Settings")
            plugin_draw_func(self, context, box)
        else:
            box.label()

    @staticmethod
    def draw_general(self, context, layout):
        layout.label(text="General settings here")


def get_ui_scale(context, region_width):
    v2d = context.region.view2d
    x1 = v2d.region_to_view(0, 0)[0]
    x2 = v2d.region_to_view(region_width, 0)[0]
    return region_width / (x2 - x1)


classes = (
    TogglePlugin,
    Plugin,
    Preferences,
    Runtime,
    ShowSettings,
)

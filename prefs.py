import bpy
from . import utils
from _bpy import context as _context

system = _context.preferences.system


def init():
    utils.register_classes(classes)
    _init_plugins()


def cleanup():
    _cleanup_plugins()
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


def _cleanup_plugins():
    prefs = get_prefs()
    for plugin in prefs.plugins:
        if plugin.enabled:
            plugin.module.disable()


def get_prefs():
    return _context.preferences.addons["textension"].preferences


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


class Runtime(bpy.types.PropertyGroup):
    show_all_kmi: bpy.props.BoolProperty(default=False)
    tab: bpy.props.EnumProperty(
        items=(('CORE',    "Core",    "Core settings"),
               ('PLUGINS', "Plugins", "Plugins settings"),
               ('KEYMAPS', "Keymaps", "Keymaps settings")))

    @classmethod
    def register(cls):
        bpy.types.WindowManager.textension = bpy.props.PointerProperty(type=cls)

    @classmethod
    def unregister(cls):
        del bpy.types.WindowManager.textension


class Plugin(bpy.types.PropertyGroup):
    """Represents a Textension plugin which can be enabled/disabled."""

    @property
    def module(self):
        import importlib
        return importlib.import_module(self.full_name)

    def on_enabled(self, context):
        if self.enabled:
            self.module.enable()
        else:
            self.module.disable()
        context.preferences.is_dirty = True

    # The full name as used by importlib's import_module
    full_name: bpy.props.StringProperty()

    # Whether the plugin is enabled
    enabled: bpy.props.BoolProperty(update=on_enabled)

    # Whether a previously enabled plugin is missing
    missing: bpy.props.BoolProperty()

    # Whether to show plugin settings in the ui
    show_settings: bpy.props.BoolProperty(
        default=False,
        description="Show this plugin's settings")


class Preferences(bpy.types.AddonPreferences, bpy.types.PropertyGroup):
    bl_idname = __package__

    plugins: bpy.props.CollectionProperty(type=Plugin)
    # from .keymap import kmi_cb

    # wheel_scroll_lines: bpy.types.IntProperty(
    #     name="Scroll Lines",
    #     description="Lines to scroll per wheel tick",
    #     default=3,
    #     min=1,
    #     max=10,
    # )
    # nudge_scroll_lines: bpy.types.IntProperty(
    #     name="Nudge Lines",
    #     description="Lines to nudge with Ctrl-Up/Down",
    #     default=3,
    #     min=1,
    #     max=10,
    # )
    # use_smooth_scroll: bpy.types.BoolProperty(
    #     name="Smooth Scrolling",
    #     description="Smooth scroll with mouse wheel",
    #     default=True,
    #     update=kmi_cb("scroll2", "use_smooth_scroll"),
    # )
    # scroll_speed: bpy.types.FloatProperty(
    #     name="Scroll Speed",
    #     description="Scroll speed multiplier",
    #     default=5,
    #     min=1,
    #     max=20,
    # )
    # use_continuous_scroll: bpy.types.BoolProperty(
    #     name="Continuous Scrolling",
    #     description="Enable continuous scrolling with middle mouse",
    #     default=True,
    #     update=kmi_cb("scroll_continuous", "use_continuous_scroll"),
    # )
    # closing_bracket: bpy.types.BoolProperty(
    #     name="Close Brackets",
    #     description="Automatically close brackets",
    #     default=True,
    # )
    # use_new_linebreak: bpy.types.BoolProperty(
    #     name="New Line Break",
    #     description="Use line break which adds indentation",
    #     default=True,
    #     update=kmi_cb("line_break", "use_new_linebreak"),
    # )
    # use_home_toggle: bpy.types.BoolProperty(
    #     name="Home Toggle",
    #     description="Home key toggles between line start and indent level",
    #     default=True,
    #     update=kmi_cb("move_toggle", "use_home_toggle"),
    # )
    # use_search_word: bpy.types.BoolProperty(
    #     default=True,
    #     name="Search by Selection",
    #     description="Selected word is automatically copied to search field",
    #     update=kmi_cb("search_with_selection", "use_search_word"),
    # )
    # use_cursor_history: bpy.types.BoolProperty(
    #     name="Use Cursor History",
    #     description="Enable to use cursor history with mouse 4/5",
    #     default=True,
    #     update=kmi_cb("cursor_history", "use_cursor_history"),
    # )
    # use_header_toggle: bpy.types.BoolProperty(
    #     name="Toggle Header",
    #     description="Toggle header with hotkey (Alt)",
    #     default=True,
    #     update=kmi_cb("toggle_header", "use_header_toggle"),
    # )
    # use_line_number_select: bpy.types.BoolProperty(
    #     name="Line Number Select",
    #     description="Select lines from line number margin",
    #     default=True,
    #     update=kmi_cb("line_select", "use_line_number_select"),
    # )
    # triple_click: bpy.types.EnumProperty(
    #     name="Triple-Click",
    #     description="Type of selection when doing a triple click",
    #     default='LINE',
    #     items=(("LINE", "Line", "Select entire line"),
    #            ("PATH", "Path", "Select entire python path")),
    # )

    def draw_plugins(self, context, layout):
        layout.use_property_split = True
        layout.use_property_decorate = False
        for plugin in self.plugins:
            module = plugin.module
            box = layout.box()
            box.emboss = 'NORMAL'
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

    # def draw_keymaps(self, context, layout):
    #     col = layout.column(align=True)
    #     rw = context.region.width
    #     textension = context.window_manager.textension
    #     show_all = textension.show_all_kmi
    #     text = "Show All" if not show_all else "Collapse"
    #     col.prop(textension, "show_all_kmi", text=text, emboss=False)
    #     col.separator(factor=2)

    #     kmi_range = slice(19 if not show_all else None)

    #     # Custom keymaps are stored on each operator.
    #     for cls in classes()[kmi_range]:
    #         keymaps = getattr(cls, "_keymaps", ())
    #         for idx, (km, kmi, note) in enumerate(keymaps):
    #             if note == "HIDDEN":
    #                 continue

    #             km_utils.kmi_ensure(cls, idx, kmi)
    #             self.draw_kmi(col, rw, kmi, note)

    #     col.separator(factor=2)
    #     if show_all:
    #         col.prop(textension, "show_all_kmi", text=text, emboss=False)


    def draw(self, context):
        layout = self.layout
        runtime = context.window_manager.textension

        row = layout.row()
        row.prop(runtime, "tab", expand=True)
        layout.separator()

        row = layout.row()
        row.alignment = 'CENTER'
        col = row.column(align=True)
        # col.ui_units_x = 20

        if runtime.tab == 'PLUGINS':
            self.draw_plugins(context, col)
        # elif runtime.tab == 'KEYMAPS':
        #     self.draw_keymaps(context, col)


classes = (
    Plugin,
    Preferences,
    Runtime
)

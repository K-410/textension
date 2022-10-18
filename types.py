# NOTE: This module must be importable by any submodule.
# NOTE: Do not import stuff from this package at module level.

import bpy
import ctypes
from bpy.types import SpaceTextEditor, Text


is_spacetext = SpaceTextEditor.__instancecheck__
is_text = Text.__instancecheck__

def _inject_const(**kwargs):
    """
    Internal.
    @inject_const(string="Hello World", none_variable=None)
    def func():
        print("const string")
        assert "const none_variable" is None
    """
    def _injector(func):
        for attr, obj in kwargs.items():
            *consts, = func.__code__.co_consts
            consts[consts.index(f"const {attr}")] = obj
            func.__code__ = func.__code__.replace(co_consts=tuple(consts))
            func.__dict__.update(kwargs)
        return func
    return _injector

# def pycapsule_type():
#     import ctypes
#     ob = type("PyTypeObject", (ctypes.Structure,), {})
#     ptr = ctypes.pointer(ob.in_dll(ctypes.pythonapi, "PyCapsule_Type"))
#     return ctypes.py_object.from_address(ctypes.addressof(ptr)).value


# PyCapsule = pycapsule_type()

# class Point:
#     x: int = 0
#     y: int = 0
#     def __new__(cls, x=0, y=0):
#         self = super().__new__(cls)
#         self.x = x
#         self.y = y
#         return self

class mutable_classproperty:
    """
    Mutable property that mutates the attribute from a property
    descriptor to the returned value of the decorated function.
    Only for class attributes.
    """
    def __new__(cls, func):
        (self := super().__new__(cls)).func = func
        return self

    def __set_name__(self, cls, attr):
        def mutate(instance, func=self.func, args=(cls, attr)):
            setattr(*args, ret := func(instance))
            return ret
        setattr(cls, attr, property(mutate))

Callable = type(lambda: None)

class HitTestData:
    from _bpy import context
    space_data: bpy.types.SpaceTextEditor = None
    region: bpy.types.Region = None
    pos: tuple[int, int] = (-1, -1)
    prefs: bpy.types.AddonPreferences

    # 'prefs' becomes a normal attribute on first access.
    @mutable_classproperty
    def prefs(self):
        from .utils import prefs
        return prefs()


class Plugin(bpy.types.PropertyGroup):
    """
    Represents a Textension plugin which can be enabled/disabled.
    """

    # The plugin's associated python module
    @property
    def module(self):
        # return __import__(self.full_name)
        import importlib
        return importlib.import_module(self.full_name)

    def on_enabled(self, context):
        if self.enabled:
            self.module.enable()
        else:
            self.module.disable()
        context.preferences.is_dirty = True

    # Directory name. Also textension.plugins[name]
    name: bpy.props.StringProperty()

    # The full name as used by importlib's import_module
    full_name: bpy.props.StringProperty()

    # Whether the plugin is enabled
    enabled: bpy.props.BoolProperty(update=on_enabled)

    # Whether a previously enabled plugin is missing
    missing: bpy.props.BoolProperty()

    # Whether to show plugin settings in the ui
    show_settings: bpy.props.BoolProperty()


# TODO: Remove this class
# Manager for ordered draw callback
class UnifiedDraw:
    __slots__ = "locations", "routines", "regions"
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def add(self, key, *args):
        index = self.locations[key]

        if self.routines[index]:
            return

        self._draw_add(index, args)
        for idx, routine in enumerate(self.routines):
            if not routine:
                continue
            self._draw_remove(routine)
            _, args = routine
            self._draw_add(idx, args)

    def remove(self, key):
        index = self.locations[key]
        routine = self.routines[index]
        if routine:
            self._draw_remove(routine)
            self.routines[index] = False

    def _draw_remove(self, routine):
        handle, (*_, region_type, _) = routine
        SpaceTextEditor.draw_handler_remove(handle, region_type)

    def _draw_add(self, index, args):
        for arg in args:
            if isinstance(arg, str) and arg in self.regions:
                break
        else:
            args += 'WINDOW', 'POST_PIXEL'
        self.routines[index] = SpaceTextEditor.draw_handler_add(*args), args

    def is_registered(self, key):
        assert self._instance is not None
        return bool(self.routines[self.locations[key]])

    def enumerate(self):
        enum = []
        for (key, val), routine in zip(self.locations.items(), self.routines):
            if routine:
                enum.append((val, key))
        return enum

    def is_empty(self):
        return not any(self.routines)

    def __init__(self):
        from bpy.types import Region
        self.locations = {"syntax": 0, "scroll": 1, "matches": 2, "tabs": 3}
        self.routines = [False for _ in self.locations]
        self.regions = set(Region.bl_rna.properties["type"].enum_items.keys())

    @classmethod
    def nuke(cls):
        if cls._instance is not None:
            self = cls._instance
            for key, index in self.locations.items():
                routine = self.routines[index]
                if routine:
                    self._draw_remove(routine)

            self.routines[:] = [False] * len(self.locations)

    # def __del__(self):
    #     print("UnifiedDraw is GC'ed")

# Automatically generate bl_idname/bl_label on subclasses.
# def _set_bl_meta(subcls):
#     name = subcls.__name__
#     submodule, name = name.split("_OT_")
#     subcls.bl_idname = ".".join(n.lower() for n in (submodule, name))
#     subcls.bl_label = name.replace("_", " ").title()


class OperatorMixin:
    """A mix-in class for text operators providing a unified poll method
    and hotkey registration.
    """
    @classmethod
    def poll(cls, context, *, is_space_text=bpy.types.SpaceTextEditor.__instancecheck__):
        st = context.space_data
        return is_space_text(st) and st.text is not None

    @classmethod
    def register(cls):
        # XXX When an operator's default keymap is changed, the addon MUST
        # be uninstalled and Blender restarted, because this doesn't handle
        # changes.

        # TODO: When an operator is registered for the first time, a hash
        # should be generated based on the keymap settings and stored along
        # with the keymap string (in adddon preferences).
        from .utils import prefs, from_str, to_string
        kmi_fields = ("type", "value", "alt", "ctrl", "shift",
                      "any", "key_modifier", "oskey", "active")

        # Register operator keymaps and macro defines first if any.
        getattr(cls, "register_keymaps", lambda: None)()
        getattr(cls, "_register", lambda: None)()
        op_keymaps = getattr(cls, "_keymaps", None)
        # No keymaps. Nothing to do.
        if op_keymaps is None:
            return

        operators = prefs().operators
        op = operators.get(cls.__name__)
        if op is None:
            op = operators.add()
            op.name = cls.__name__

        # XXX This is a bad check. We shouldn't compare kmi by count.
        if len(op.kmidata) == len(op_keymaps):
            for (_, kmi, _), _kmi in zip(op_keymaps, op.kmidata):
                for key, val in zip(kmi_fields, from_str(_kmi.str)):
                    if getattr(kmi, key) != val:
                        setattr(kmi, key, val)

        else:
            if op_keymaps:
                # km, kmi, note
                for _, kmi, _ in op_keymaps:
                    item = op.kmidata.add()
                    item.name = kmi.idname
                    item.str = to_string(kmi)

        # Shouldn't happen, unless we add/remove default addon keymaps
        # TODO: Should this be handled?
        # else:
        #     raise ValueError("length mismatch %s" % cls)

    @classmethod
    def unregister(cls):
        from .km_utils import kmi_remove
        getattr(cls, "_unregister", lambda: None)()
        kmi_remove(cls)


class TextOperator(OperatorMixin, bpy.types.Operator):
    """Textension text operator"""

    def __init_subclass__(cls):
        # idname/label is generated from the class name, which is
        # expected to follow the SUBMOD_OT_op_name convention.
        cls.bl_idname = ".".join(pair := cls.__name__.lower().split("_ot_"))
        cls.bl_label = pair[1].replace("_", " ").title()


def classproperty(attr, default=None):
    from .utils import setdef
    return property(lambda s: setdef(type(s), f"_{attr}", default),
                    lambda s, v: setattr(type(s), f"_{attr}", v),
                    lambda s: delattr(type(s), f"_{attr}"))


# Default dict. For non-time critical application.
def defdict_pass():
    def fallback(self, key):
        pass
    return type("DefDict", (dict,), {"__missing__": fallback})


def defdict_create(fallback, *args):
    defdict_cls = type("DefDict", (dict,), {})
    defdict = defdict_cls(*args)
    defdict_cls.__missing__ = fallback
    return defdict


def fallback_list(self, key):
    self[key] = ret = []
    return ret


def fallback_item(item):
    def inner(self, key, item=item):
        self[key] = item
        return item
    return inner


def defdict_list(*args):
    return defdict_create(fallback_list, *args)


def defdict_item(*args, item=None):
    return defdict_create(fallback_item(item), *args)


# Unified textension logger.
def logger():
    import collections
    d = collections.deque(maxlen=500)
    from time import time, monotonic, ctime

    # Offset since epoch.
    of = time() - monotonic()

    def write(msgtype, msg, mt=monotonic, append=d.append):
        # print(msgtype, msg)
        append((mt(), msgtype, msg))

    def read(c=ctime, of=of, d=d):
        return ((c(t + of).split()[-2], mt, m) for (t, mt, m) in d)

    def clear(cl=d.clear):
        cl()
    write.read = read
    write.clear = clear
    del collections, d, time, monotonic, ctime, of, read, clear
    return write


class BlendType(dict):
    def __missing__(self, key):
        if isinstance(key, str):
            import bgl
            return self.setdefault(key, getattr(bgl, key))
        elif isinstance(key, tuple):
            return self.setdefault(key, tuple(self[k] for k in key))


class ShaderCache(dict):
    def __missing__(self, shader_type):
        from . import gl
        vert = gl.xyzw_vert
        if shader_type == "bordered":
            frag = gl.rect_bordered_frag
        elif shader_type in {"plain", "line"}:
            frag = gl.plain_frag
        elif shader_type == "stipplev":
            frag = gl.line_stipplev_frag
        else:
            raise Exception("Unhandled shader type")

        from gpu.types import GPUShader
        self[shader_type] = GPUShader(vert, frag)
        return self[shader_type]


# Key is [shader, shader_type, window]
# When window is None, defaults to main window.
class BatchCache(dict):
    def __missing__(self, key):
        sh, shader_type, window = key
        if shader_type in {"bordered", "plain"}:
            btype = 'TRI_FAN'
            *verts, = (c + (0, 1) for c in ((0, 0), (1, 0), (1, 1), (0, 1)))
        from gpu.types import GPUVertBuf, GPUBatch
        vbo = GPUVertBuf(sh.format_calc(), len(verts))
        vbo.attr_fill("pos", verts)
        return self.setdefault(key, GPUBatch(type=btype, buf=vbo))


class TextUndo:
    """
    Undo history per text data-block
    
    Custom python operators that intend to use this must not include 'UNDO'
    in their bl_options attribute.

    Simply call TextUndo.push_undo() before any changes are made. That's it.
    """

    # States are stored in stacks per instance. Each instance is kept in
    # pairs of [ID: TextUndo instance].
    store = {}

    def __new__(cls, text: Text, *, validate=True, store=store):
        assert is_text(text)

        try:
            return store[id := text.id]

        except KeyError:
            self = store[id] = super().__new__(cls)
            self.id = id
            assert text.id == self.id, f"{text.name}\n{id}\n{self.id}\n{text.id}"
            self.undo, self.redo = stacks = [], []
            self.name = text.name

            # 0: load undo, push redo
            # 1: load redo, push undo
            self.stacks = stacks, (*reversed(stacks),)
            if validate:
                self.validate()
            return self

    def validate(self) -> None:
        """
        Add missing TextUndo for all texts.
        """
        text_ids = set()
        store = self.store
        for text in bpy.data.texts:
            id = text.id
            if id not in store:
                TextUndo(text, validate=False)
            text_ids.add(id)

        for id in tuple(store):
            if id not in text_ids:
                print("removing id:", store[id].name)
                del store[id]

    def push_undo(self) -> None:
        """
        Add an undo step, storing current text block state onto the stack.
        """
        self.redo.clear()
        self.undo.append(self.snapshot())

    def get_text(self) -> Text:
        """
        Return the text block associated with this instance.
        """
        match = None
        text_ids = []  # Debug
        for text in bpy.data.texts:
            id = text.id
            text_ids.append(id)  # Debug
            if id == self.id:
                match = text

        if match is None:
            raise Exception(f"Bad id\nText ids: {text_ids}\n"
                            f"Asked for: {self.id} ({self.name})")
        return match

    def snapshot(self) -> tuple[str, tuple[int, int, int, int]]:
        """
        Return a tuple with current text as string and cursor.
        """
        text = self.get_text()
        return "\n".join(l.body for l in text.lines), text.cursor

    def load_state(self, state: int) -> bool:
        """
        Load an undo (0) or redo (1) state onto this TextUndo instance's
        data-block. When undoing, current state is pushed onto redo stack.
        """
        assert state in (0, 1), f"Expected integer in (0, 1), got {state}"
        undo, redo = self.stacks[state]

        try:
            string, cursor = undo.pop()

        except IndexError:
            return False  # Reached end of undo stack

        else:
            text = self.get_text()
            redo.append(self.snapshot())
            text.from_string(string)
            text.cursor = cursor
            return True

del ctypes

# NOTE: This module must be importable by any submodule.
# NOTE: Do not import stuff from this package at module level.

import functools
import operator
import ctypes
import types
import bpy

try:
    from _ctypes import CFuncPtr
except:
    from typing import Any as CFuncPtr

from _bpy import context as _context
from bpy.utils import register_class, unregister_class
from typing import Callable
from types import CellType, FunctionType

from operator import methodcaller, attrgetter
from itertools import compress

from sys import _getframe
from time import monotonic

_system = bpy.context.preferences.system

_call = bpy.ops._op_call

_editors: dict[str: dict] = {}

_region_types = set(bpy.types.Region.bl_rna.properties['type'].enum_items.keys())

_rna_hooks: dict[tuple, list[tuple[Callable, tuple]]] = {}

# All possible cursors to pass Window.set_cursor()
bl_cursor_types = set(bpy.types.Operator.bl_rna.properties['bl_cursor_pending'].enum_items.keys())

is_text = bpy.types.Text.__instancecheck__
is_spacetext = bpy.types.SpaceTextEditor.__instancecheck__
is_operator = bpy.types.Operator.__instancecheck__
is_operator_subclass = bpy.types.Operator.__subclasscheck__
is_module = types.ModuleType.__instancecheck__
is_builtin = types.BuiltinFunctionType.__instancecheck__
is_method = types.MethodType.__instancecheck__
is_function = types.FunctionType.__instancecheck__
is_class = lambda cls: cls.__class__ is type
is_bpyapp = type(bpy.app).__instancecheck__
is_bpystruct = bpy.types.bpy_struct.__instancecheck__
is_str = str.__instancecheck__
is_tuple = tuple.__instancecheck__
is_list = list.__instancecheck__

noop = None.__init__
noop_noargs = object.__init_subclass__

falsy = None.__init__
falsy_noargs = bool

# True.__sizeof__ is actually faster, but returns a non-zero int which may
# or may not be semantically correct.
truthy_noargs = True.__bool__

PyInstanceMethod_New = ctypes.pythonapi.PyInstanceMethod_New
PyInstanceMethod_New.argtypes = (ctypes.py_object,)
PyInstanceMethod_New.restype = ctypes.py_object

PyFunction_SetClosure = ctypes.pythonapi.PyFunction_SetClosure
PyFunction_SetClosure.argtypes = ctypes.py_object, ctypes.py_object
PyFunction_SetClosure.restype = ctypes.c_int


def inline(func):
    if hasattr(func, "__code__"):
        code = func.__code__
        args = (None,) * code.co_argcount
        posonly_args = (None,) * code.co_posonlyargcount
        return func(*args, *posonly_args)
    else:
        return func()


def is_methoddescriptor(obj):
    if hasattr(obj, "__get__"):
        if not hasattr(obj, "__set__"):
            if not is_class(obj):
                if not is_method(obj):
                    return not is_function(obj)
    return False


def safe_redraw():
    try:
        _context.region.tag_redraw()
    except AttributeError:
        pass


# This just ensures tracebacks in the decoratee propagate to stderr.
def unsuppress(func, sentinel=object()):
    def wrapper(*args, **kw):
        ret = sentinel
        try:
            ret = func(*args, **kw)
        finally:
            if ret is sentinel:
                import traceback
                traceback.print_exc()
        return ret
    return wrapper


def classproperty(func):
    return classmethod(property(func))


def instanced_default_cache(default_func):
    _check_type(default_func, FunctionType)
    argcount = default_func.__code__.co_argcount
    assert argcount == 2, f"Expected a function taking 2 arguments, not {argcount}."
    class _DefaultDict(dict):
        __missing__ = default_func
    return _DefaultDict()


@inline
def starchain(it):
    from itertools import chain
    return chain.from_iterable

@inline
def dict_items(d: dict):
    return dict.items


def factory(func):
    args = (None,) * func.__code__.co_argcount
    return func(*args)


def _descriptor(func, setter=None):
    return property(_unbound_method(func), setter)


def _forwarder(*strings: str):
    return _descriptor(operator.attrgetter(*strings))


def _named_index(*indices: tuple[int]):
    return _descriptor(operator.itemgetter(*indices))


def _unbound_getter(*names: str):
    return _unbound_method(operator.attrgetter(*names))


def _unbound_attrcaller(name: str):
    return property(_unbound_getter(name))


@factory
def _unbound_method(func: Callable):
    return PyInstanceMethod_New


# Patch a function with new closures and code object. Returns copy of the old.
def _patch_function(fn: FunctionType, new_fn: FunctionType, rename=True):
    orig = _copy_function(fn)

    # Apply the closure cells from the new function.
    PyFunction_SetClosure(fn, new_fn.__closure__)

    # Apply new defaults, if any.
    fn.__defaults__ = new_fn.__defaults__

    name = f"{fn.__name__}"
    if rename:
        name += f" ({new_fn.__name__})"
    fn.__code__ = new_fn.__code__.replace(co_name=name)
    fn.__orig__ = vars(fn).setdefault("__orig__", orig)
    return orig


# Make a deep copy of a function.
def _copy_function(f):
    g = FunctionType(
        f.__code__,
        f.__globals__,
        name=f.__name__,
        argdefs=f.__defaults__,
        closure=f.__closure__)
    g = functools.update_wrapper(g, f)
    g.__kwdefaults__ = f.__kwdefaults__
    return g


def _check_type(obj, *types):
    if not isinstance(obj, types):
        # Check the class also.
        if not (isinstance(obj, type) and issubclass(obj, types)):
            raise TypeError(f"Expected {types}, got {type(obj)}")


def close_cells(*args):
    """Close ``args`` over a function."""
    import ctypes
    from types import CellType
    def inner(func, args=args):
        ctypes.pythonapi.PyFunction_SetClosure(func, tuple(map(CellType, args)))
        return func
    return inner


def test_and_update(obj, attr, value) -> bool:
    if getattr(obj, attr) != value:
        setattr(obj, attr, value)
        return True
    return False


def defer(callable, *args, delay=0.0, persistent=True, **kw):
    def wrapper(callable=callable, args=args, kw=kw):
        try:
            callable(*args, **kw)
        finally:
            return None

    bpy.app.timers.register(wrapper, first_interval=delay, persistent=persistent)


def register_classes(classes):
    for cls in classes:
        register_class(cls)



def unregister_classes(classes):
    for cls in reversed(classes):
        unregister_class(cls)


def km_def(km: str, km_type: str, km_value: str, **kw):
    # This assumes km_def is called from the class' suite.
    km_meta = _getframe(1).f_locals.setdefault("__km__", [])

    # Keyword args for kmi.new()
    kmi_new_kw = {}
    for key in ("ctrl", "alt", "shift", "repeat", "head"):
        kmi_new_kw[key] = kw.pop(key, False)

    km_meta.append((km, km_type, km_value, kmi_new_kw, kw))


# Not sure how to get this via RNA.
space_map = {
    "CLIP_EDITOR":      bpy.types.SpaceClipEditor,
    "CONSOLE":          bpy.types.SpaceConsole,
    "DOPESHEET_EDITOR": bpy.types.SpaceDopeSheetEditor,
    "FILE_BROWSER":     bpy.types.SpaceFileBrowser,
    "GRAPH_EDITOR":     bpy.types.SpaceGraphEditor,
    "IMAGE_EDITOR":     bpy.types.SpaceImageEditor,
    "INFO":             bpy.types.SpaceInfo,
    "NLA_EDITOR":       bpy.types.SpaceNLA,
    "NODE_EDITOR":      bpy.types.SpaceNodeEditor,
    "OUTLINER":         bpy.types.SpaceOutliner,
    "PREFERENCES":      bpy.types.SpacePreferences,
    "PROPERTIES":       bpy.types.SpaceProperties,
    "SEQUENCE_EDITOR":  bpy.types.SpaceSequenceEditor,
    "SPREADSHEET":      bpy.types.SpaceSpreadsheet,
    "TEXT_EDITOR":      bpy.types.SpaceTextEditor,
    "VIEW_3D":          bpy.types.SpaceView3D,
}


def add_draw_hook(
        hook:   Callable,
        args:   tuple = (),
        space:  str = 'TEXT_EDITOR',
        region: str = 'WINDOW',
        type:   str = 'POST_PIXEL'):

    if not isinstance(args, tuple):
        args = (args,)

    assert all(map(str.__instancecheck__, (space, region, type)))
    space_type = space_map.get(space)

    _check_type(hook, FunctionType)
    _check_type(space_type, bpy.types.Space)

    if args and hook.__defaults__ is None:
        hook.__defaults__ = args

    regions = _editors.setdefault(space_type, {})

    if region not in regions:
        # Region is new - register a region draw callback.
        hooks = [hook]

        @close_cells(hooks)
        def region_draw_handler():
            for draw_callback in hooks:
                draw_callback()

        handle = space_type.draw_handler_add(region_draw_handler, (), region, type)
        regions[region] = (handle, hooks)

    else:
        regions[region][1].append(hook)


def remove_draw_hook(fn: Callable, region: str='WINDOW'):
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
    if not found:
        raise RuntimeError(f"'{fn.__name__}' not a registered hook")
    return found


def unwatch_rna(notify: Callable):
    """Remove a callback from rna watch."""
    for key, hooks in list(_rna_hooks.items()):
        for callback, args in hooks:
            if callback == notify:
                hooks.remove((callback, args))
                break
        if not hooks:
            bpy.msgbus.clear_by_owner(key)
            del _rna_hooks[key]


def on_rna_changed(key):
    for func, args in _rna_hooks[key]:
        func(*args)


def watch_rna(key, notify: Callable, args=()):
    if not (hooks := _rna_hooks.setdefault(key, [])):
        bpy.msgbus.subscribe_rna(key=key, owner=key, args=(key,), notify=on_rna_changed)
    hooks.append((notify, args))


# A wrapper version of 'tag_userdef_modified'.
def tag_userdef_modified_wrapper(func):
    def wrap(self, context, *args, **kwargs):
        ret = func(self, context, *args, **kwargs)
        context.preferences.is_dirty = True
        return ret
    return wrap


# A property update callback that sets the user preferences dirty.
def tag_userdef_modified(self, context):
    context.preferences.is_dirty = True


def clamp_factory(lower, upper):
    def inner(val, a=lower, b=upper):
        if val < a:
            return a
        elif val > b:
            return b
        return val
    return inner


def get_scrollbar_x_points(region_width):
    """Given a region width in pixels, return the x1 and x2 points
    of the scrollbar.
    """
    widget_unit = _system.wu
    sx_2 = int(region_width - 0.2 * widget_unit)
    sx_1 = sx_2 - int(0.4 * widget_unit) + 2
    return sx_1, sx_2


def iter_areas(area_type='TEXT_EDITOR'):
    for area in starchain(map(get_areas_from_window, _context.window_manager.windows)):
        if area.type == area_type:
            yield area


def iter_regions(area_type='TEXT_EDITOR', region_type='WINDOW'):
    for region in starchain(map(get_regions_from_area, iter_areas(area_type))):
        if region.type == region_type:
            yield region


def iter_spaces(space_type='TEXT_EDITOR'):
    yield from map(get_space_from_area, iter_areas(space_type))


def redraw_editors(area='TEXT_EDITOR', region_type='WINDOW'):
    any(map(tag_redraw, iter_regions(area, region_type)))


get_id = attrgetter("id")

get_space_from_area = attrgetter("spaces.active")
get_areas_from_window = attrgetter("screen.areas")
get_regions_from_area = attrgetter("regions")
tag_redraw = methodcaller("tag_redraw")


def text_from_id(text_id: int):
    if isinstance(text_id, int):
        selectors = map(text_id.__eq__, map(get_id, bpy.data.texts))
        return next(compress(bpy.data.texts, selectors), None)
    return None


def namespace(*names: tuple[str], **defaults):
    assert all(isinstance(n, str) for n in names)
    class FixedNamespace:
        __slots__ = names or tuple(defaults)
    namespace = FixedNamespace()
    for k, v in defaults.items():
        setattr(namespace, k, v)
    return namespace


def area_from_space_data(st) -> bpy.types.Area:
    assert isinstance(st.id_data, bpy.types.Screen), st.id_data
    for area in st.id_data.areas:
        if area.spaces[0] == st:
            return area


def region_from_space_data(st, region_type='WINDOW') -> bpy.types.Region:
    for region in area_from_space_data(st).regions:
        if region.type == region_type:
            return region


@factory
def redraw_text():
    from .btypes import ARegion, bContext, byref, get_area_region_type

    art = get_area_region_type('TEXT_EDITOR', 'WINDOW')
    draw = art.draw
    ctx_ref = byref(bContext(_context))

    def redraw_text():
        if region := _context.region:
            draw(ctx_ref, byref(ARegion(region)))
    return redraw_text


def make_space_data_instancer(cls, space_type=bpy.types.SpaceTextEditor):
    def get_instance(*, cache={}) -> cls:
        try:
            return cache[_context.space_data]
        except KeyError:
            _check_type(st := _context.space_data, space_type)
            return cache.setdefault(st, cls(st))
    get_instance.__annotations__["return"] = cls
    return get_instance


# See ``txt_make_dirty`` in source/blender/blenkernel/intern/text.c.
def tag_text_dirty(text: bpy.types.Text):
    _check_type(text, bpy.types.Text)

    internal = text.internal
    TXT_ISDIRTY = 1 << 0
    internal.flags |= TXT_ISDIRTY

    if internal.compiled:
        from ctypes import pythonapi, cast, py_object
        pythonapi.Py_DecRef(cast(internal.compiled, py_object))
        internal.compiled = 0


# A context manager decorator with less overhead.
class cm:
    __slots__ = ("iterator", "result")
    __enter__ = object.__init_subclass__

    def __new__(cls, func):
        self = super().__new__(cls)
        def wrapper(*args):
            self.iterator = func(*args)
            self.result   = next(self.iterator)
            return self
        return wrapper

    def __exit__(self, *_):
        try:
            next(self.iterator)
        except:
            pass


def get_addon_keymap(keymap_name):
    kc = _context.window_manager.keyconfigs
    km = kc.addon.keymaps.get(keymap_name)

    if km is None:
        default_km = kc.default.keymaps.get(keymap_name)
        if not default_km:
            return None

        km = kc.addon.keymaps.new(
            keymap_name,
            space_type=default_km.space_type,
            region_type=default_km.region_type,
            modal=getattr(km, "is_modal", False)  # 3.3.0 and up doesn't have this
        )

    return km


def add_keymap(km_name: str, idname: str, type: str, value: str, **kw):
    km = get_addon_keymap(km_name)
    return km, km.keymap_items.new(idname, type, value, **kw)


@classmethod
def text_poll(cls, context):
    st = context.space_data
    return is_spacetext(st) and st.text


def set_name(name):
    def wrapper(func):
        func.__name__ = name
        func.__qualname__ = name
        func.__code__ = func.__code__.replace(co_name=name)
        return func
    return wrapper


class TextOperator(bpy.types.Operator):
    # Needed for undo plugin.
    _register_hooks   = []
    _unregister_hooks = []

    def __init_subclass__(cls):
        # Automate setting bl_idname and bl_label.
        cls.bl_idname = ".".join(pair := cls.__name__.lower().split("_ot_"))
        cls.bl_label = pair[1].replace("_", " ").title()

    @classmethod
    def register(cls):
        for hook in cls._register_hooks:
            hook(cls)

        # Handle operators with keymap meta definitions here.
        meta = getattr(cls, "__km__", None)
        if not isinstance(meta, list):
            return

        keymaps = []

        for kmname, type, value, kw1, kw2 in meta:
            km = get_addon_keymap(kmname)

            if not km:
                print(f"Textension: Invalid keymap '{kmname}' ({cls.__name__})")
                continue

            kmi = km.keymap_items.new(cls.bl_idname, type, value, **kw1)
            for name, value in kw2.items():
                setattr(kmi.properties, name, value)
            keymaps += [(km, kmi)]
        cls._keymaps = keymaps

    @classmethod
    def unregister(cls):
        for hook in cls._unregister_hooks:
            hook(cls)

        if keymaps := getattr(cls, "_keymaps", None):
            for km, kmi in keymaps:
                km.keymap_items.remove(kmi)
            keymaps.clear()


# State-less interface for LinearStack.
class Adapter:
    def get_string(self) -> str:
        return ""

    def set_string(self, string) -> None:
        pass

    def get_cursor(self):
        pass

    def set_cursor(self, cursor):
        pass

    def get_should_split(self, hint: bool) -> bool:
        return hint

    # Update hook on stack initialization and undo push.
    def update(self, restore=False):
        pass

    # These are fallback polls for when LinearStack.poll_undo/redo fails but
    # we still want to consume the undo event for focused Widgets.
    def poll_undo(self):
        return False

    def poll_redo(self):
        return False

    @property
    def is_valid(self):
        return True

    def __repr__(self):
        return f"{type(self).__name__}"


# TODO: Use unified diff instead of storing the whole text.
class Step:
    __slots__ = ("data", "cursor1", "cursor2", "tag")

    data:    str
    cursor1: tuple[int]
    tag:     str

    def __init__(self, adapter: Adapter, tag=""):
        self.data    = adapter.get_string()
        self.cursor1 = adapter.get_cursor()
        self.cursor2 = None
        self.tag     = tag

    def __repr__(self):
        return f"<Step tag={self.tag} at 0x{id(self):0>16X}>"


class LinearStack:
    __slots__ = ("undo", "redo", "last_push", "adapter")

    def __init__(self, adapter: Adapter):
        self.undo: list[Step] = []
        self.redo: list[Step] = []

        self.adapter   = adapter
        self.last_push = 0.0

        # The initial state.
        self.push_undo(tag="init")

    def __repr__(self):
        return f"<LinearStack ({self.adapter}) at 0x{id(self):0>16X}>"

    def pop_undo(self) -> bool:
        # We don't use ``self.poll_undo()`` here, because the stack can still
        # be empty and poll True. Widgets must consume the undo when in focus.
        if len(self.undo) > 1:
            self.move_and_set(self.undo, self.redo)
            return True

        # Returning False here means that, when this method is used as a hook
        # in ED_OT_undo, other hooks will run. We can't have focused Widgets
        # pass the control to the next hook, so instead we use an adapter poll
        # that allows custom return value.
        return self.adapter.poll_undo()

    def pop_redo(self) -> bool:
        if self.redo:
            self.move_and_set(self.redo, self.undo)
            return True
        return self.adapter.poll_redo()

    def move_and_set(self, from_stack, to_stack):
        to_stack.append(from_stack.pop())
        state = self.undo[-1]
        self.adapter.set_string(state.data)

        if from_stack is self.undo:
            self.adapter.set_cursor(state.cursor2 or state.cursor1)
        else:
            self.adapter.set_cursor(state.cursor1)
        self.adapter.update(restore=True)

    def restore_last(self):
        if self.adapter.is_valid:
            state = self.undo[-1]
            self.adapter.set_string(state.data)
            self.adapter.set_cursor(state.cursor2 or state.cursor1)
            self.adapter.update(restore=True)

    def push_undo(self, tag, *, can_group=True):
        """If ``can_group`` is True, allow merging similar states."""
        if undo := self.undo:
            can_group &= tag == undo[-1].tag

        now = monotonic()
        adapter = self.adapter
        state = Step(adapter, tag=tag)

        if not can_group or now - self.last_push > 0.5 or \
                adapter.get_should_split(can_group):
            undo += [state]
        else:
            undo[-1] = state

        self.last_push = now
        self.redo.clear()
        adapter.update()

    def push_intermediate_cursor(self):
        if self.undo:
            self.undo[-1].cursor2 = self.adapter.get_cursor()

    def poll_undo(self):
        return len(self.undo) > 1 or self.adapter.poll_undo()

    def poll_redo(self):
        return bool(self.undo) or self.adapter.poll_redo()

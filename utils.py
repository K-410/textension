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
from itertools import compress, starmap
from functools import partial

from sys import _getframe
from time import monotonic

from typing import TypeVar

_T = TypeVar("_T")


_system = bpy.context.preferences.system

_call = bpy.ops._op_call
_editors: dict[str: dict] = {}
_region_types = set(bpy.types.Region.bl_rna.properties['type'].enum_items.keys())
_rna_hooks: dict[tuple, list[tuple[Callable, tuple]]] = {}

is_spacetext = bpy.types.SpaceTextEditor.__instancecheck__

PyInstanceMethod_New = ctypes.pythonapi.PyInstanceMethod_New
PyInstanceMethod_New.argtypes = (ctypes.py_object,)
PyInstanceMethod_New.restype = ctypes.py_object

PyFunction_SetClosure = ctypes.pythonapi.PyFunction_SetClosure
PyFunction_SetClosure.argtypes = ctypes.py_object, ctypes.py_object
PyFunction_SetClosure.restype = ctypes.c_int


# This just converts the return values into functions for static analysis.
def inline(func: _T) -> _T:
    if hasattr(func, "__code__"):
        code = func.__code__
        args = (None,) * code.co_argcount
        posonly_args = (None,) * code.co_posonlyargcount
        return func(*args, *posonly_args)
    else:
        return func()


@inline
def filtertrue(seq):
    """Filter objects that are truthy."""
    return partial(filter, None)


def inline_class(*args, star=True):
    def wrapper(cls):
        if star:
            return cls(*args)
        return cls(args)
    return wrapper


@inline
def map_not(iterable) -> map:
    return partial(map, operator.not_)


@inline
def map_contains(sequence1, sequence2):
    return partial(map, operator.contains)


def lazy_overwrite(func) -> property:
    class lazy_overwrite:
        __slots__ = ()
        def __get__(_, instance, objclass=None):
            return instance.__dict__.setdefault(func.__name__, func(instance))
    return lazy_overwrite()


def lazy_class_overwrite(func) -> property:
    class lazy_overwrite:
        def __get__(self, instance_unused, objclass):
            setattr(objclass, func.__name__, ret := func(objclass))
            return ret
    return lazy_overwrite()


def soft_property(custom_getter) -> property:
    """Usage:

    >>> @soft_property
    >>> def value(prop, instance, objclass=None):
    >>>     instance.value = expensive_computation()
    >>>     return instance.value
    """
    @inline
    class _soft_property:
        __slots__ = ()
        __get__   = custom_getter
    return _soft_property


def _soft_forwarder(data_path: str):
    """Forwarding descriptor that can be overwritten like any attribute."""
    
    return soft_property(property(operator.attrgetter(data_path)).__get__)

@inline
class _soft_attribute_error:
    def __new__(cls):
        def raise_attribute_error(self):
            raise AttributeError
        return soft_property(raise_attribute_error)


@inline
class defaultdict_list:
    def __new__(cls) -> dict[list]:
        from collections import defaultdict
        return partial(defaultdict, list)


@inline
def noop(*args, **kw) -> None:
    return None.__init__

@inline
def noop_noargs() -> None:
    return object.__init_subclass__

@inline
def falsy(*args, **kw) -> None:
    return None.__init__

@inline
def falsy_noargs() -> bool:
    return bool

@inline
def get_dict(cls):
    return type.__dict__["__dict__"].__get__

@inline
def get_mro(cls):
    return type.__dict__["__mro__"].__get__

@inline
def dict_get(instance_dict):
    return dict.get

@inline
def obj_get(obj, name):
    return object.__getattribute__

@inline
def get_text_line_sync_key(text):
    """Returns a tuple of TextLine/int of the cursor focus."""
    return attrgetter("select_end_line", "select_end_character")

@inline
def get_mro_dict(obj) -> dict:
    from builtins import type, map, reversed, isinstance
    from .utils import get_mro

    @inline
    def compose(dicts_iterable) -> dict:
        from functools import reduce
        from operator import or_
        return partial(reduce, or_)

    def get_mro_dict(obj) -> dict:
        if not isinstance(obj, type):
            obj = type(obj)
        return compose(map(get_dict, reversed(get_mro(obj))))
    return get_mro_dict


@inline
def get_module_dict(module):
    return type(bpy).__dict__["__dict__"].__get__

@inline
def get_module_dir(module):
    return type(bpy).__dir__

@inline
def consume(iterable) -> None:
    return __import__("collections").deque(maxlen=0).extend


@inline
def _get_dict(typ) -> dict:
    import ctypes
    PyObject_GenericGetDict = ctypes.pythonapi.PyObject_GenericGetDict
    PyObject_GenericGetDict.argtypes = [ctypes.py_object]
    PyObject_GenericGetDict.restype = ctypes.py_object
    return PyObject_GenericGetDict


# True.__sizeof__ is actually faster, but returns a non-zero int which may
# or may not be technically correct.
@inline
def truthy_noargs() -> True:
    return True.__bool__


@inline
def truthyish_noargs() -> int:
    return True.__sizeof__


def safe_redraw():
    try:
        _context.region.tag_redraw()
    except AttributeError:
        pass


# Try to redraw a region from a space data regardless if it's dangling.
def safe_redraw_from_space_data(st: bpy.types.Space, space_type: str = "TEXT_EDITOR"):
    for space in iter_spaces(space_type):
        if space == st:
            return region_from_space_data(space).tag_redraw()


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


def _forwarder(*strings: str, rtype: _T = None) -> _T:
    return property(operator.attrgetter(*strings))


def _class_forwarder(*strings: str, rtype: _T = None) -> _T:
    return classproperty(operator.attrgetter(*strings))


try:
    from collections import _tuplegetter
    def _named_index(index: int, doc: str = ""):
        return _tuplegetter(index, doc)

except ImportError:
    def _named_index(index: int, doc: str = ""):
        return _descriptor(operator.itemgetter(index))


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
            if len(types) == 1:
                s = str(types[0])
            else:
                s = f"any of [{' ,'.join(map(str, types))}]"
            raise TypeError(f"Expected {s}, got {type(obj)}")


def close_cells(*args):
    """Close ``args`` over a function. Used for binding closures to functions
     created inside a loop or for explicit ordering.
     """
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
        except:
            # Somehow Blender suppresses the traceback. WTF.
            import traceback
            traceback.print_exc()
        finally:
            return None

    bpy.app.timers.register(wrapper, first_interval=delay, persistent=persistent)


def register_classes(classes):
    consume(map(register_class, classes))


def unregister_classes(classes):
    consume(map(unregister_class, reversed(classes)))


def km_def(km: str, km_type: str, km_value: str, **kw):
    # This assumes km_def is called from the class' suite.
    km_meta = _getframe(1).f_locals.setdefault("__km__", [])

    # Keyword args for kmi.new()
    kmi_new_kw = {}
    for key in ("ctrl", "alt", "shift", "repeat", "head"):
        kmi_new_kw[key] = kw.pop(key, False)

    km_meta += (km, km_type, km_value, kmi_new_kw, kw),


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

_draw_hook_index_map = {}


def add_draw_hook(
        hook:        FunctionType,
        args:        tuple = (),
        space_type:  str = 'TEXT_EDITOR',
        region_type: str = 'WINDOW',
        draw_type:   str = 'POST_PIXEL',
        draw_index:  int = -1):

    if not isinstance(args, tuple):
        args = (args,)

    space = space_map.get(space_type)

    _check_type(hook, FunctionType)
    _check_type(space, bpy.types.Space)
    _check_type(draw_index, int)

    if args and hook.__defaults__ is None:
        hook.__defaults__ = args

    _draw_hook_index_map[id(hook)] = draw_index

    regions = _editors.setdefault(space, {})

    if region_type not in regions:
        # Add a new draw callback for this region.
        hooks = []

        @close_cells(hooks)
        def region_draw_handler():
            for draw_callback in hooks:
                draw_callback()

        handle = space.draw_handler_add(region_draw_handler, (), region_type, draw_type)
        regions[region_type] = (handle, hooks)

    hooks = regions[region_type][1]

    if draw_index != -1:
        for i, hk in enumerate(hooks):
            hook_index = _draw_hook_index_map.get(id(hk), -1)
            if hook_index > -1 and hook_index > draw_index:
                hooks.insert(i, hook)
                return None

    hooks += hook,


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
    _draw_hook_index_map.pop(id(fn), None)
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
    hooks += (notify, args),


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


def get_scrollbar_x_offsets(region_width):
    """Given a region width in pixels, return the x1 and x2 points
    of the scrollbar.
    """
    widget_unit = _system.wu
    sx_2 = int(region_width - 0.2 * widget_unit)
    sx_1 = sx_2 - int(0.4 * widget_unit) + 2
    return sx_1, sx_2


def iter_areas(area_type='TEXT_EDITOR'):
    for area in starchain(map(get_screen_areas, _context.window_manager.windows)):
        if area.type == area_type:
            yield area


def iter_regions(area_type='TEXT_EDITOR', region_type='WINDOW'):
    for region in starchain(map(get_regions, iter_areas(area_type))):
        if region.type == region_type:
            yield region


def iter_spaces(space_type='TEXT_EDITOR'):
    yield from map(get_spaces_active, iter_areas(space_type))


def redraw_editors(area='TEXT_EDITOR', region_type='WINDOW'):
    consume(map(tag_redraw, iter_regions(area, region_type)))


get_id = attrgetter("id")
get_spaces_active = attrgetter("spaces.active")
get_screen_areas = attrgetter("screen.areas")
get_regions = attrgetter("regions")
tag_redraw = methodcaller("tag_redraw")


def text_from_id(text_id: int):
    if isinstance(text_id, int):
        selectors = map(text_id.__eq__, map(get_id, bpy.data.texts))
        return next(compress(bpy.data.texts, selectors), None)
    return None


def _update_namespace(self, **kw):
    if isinstance(self, type):
        # Class.__setattr__ is a slot wrapper.
        attrsetter = partial(setattr, self)
    else:
        attrsetter = self.__setattr__
    consume(starmap(attrsetter, kw.items()))


def _reset_namespace(self):
    consume(map(self.__delattr__, self.__slots__))


def namespace(*names: tuple[str], **defaults):
    assert all(map(str.__instancecheck__, names))

    class FixedNamespace:
        __slots__ = names or tuple(defaults)
        update: Callable = _update_namespace
        reset: Callable  = _reset_namespace

    namespace = FixedNamespace()
    namespace.update(**defaults)
    return namespace


def region_from_space_data(st, region_type='WINDOW') -> bpy.types.Region:
    for area in st.id_data.areas:
        if area.spaces[0] == st:
            for region in area.regions:
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


# KeyMap.name: (space_type, region_type)
default_keymap_data = {
    "Text":         ('TEXT_EDITOR', 'WINDOW'),
    "Text Generic": ('TEXT_EDITOR', 'WINDOW')
}


def get_addon_keymap(keymap_name):
    kc = _context.window_manager.keyconfigs
    km = kc.addon.keymaps.get(keymap_name)

    if km is None:
        default_km = kc.default.keymaps.get(keymap_name)
        if not default_km:
            if keymap_name not in default_keymap_data:
                assert False, f"Unhandled default keymap name: {keymap_name}"
            space_type, region_type = default_keymap_data[keymap_name]
        else:
            space_type  = default_km.space_type
            region_type = default_km.region_type

        km = kc.addon.keymaps.new(
            keymap_name,
            space_type=space_type,
            region_type=region_type,
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
            keymaps += (km, kmi),
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

    @inline
    def set_string(self, string) -> None:
        return noop

    @inline
    def get_cursor(self):
        return noop_noargs

    @inline
    def set_cursor(self, cursor):
        return noop

    def get_should_split(self, hint: bool) -> bool:
        return hint

    # Update hook on stack initialization and undo push.
    @inline
    def on_update(self, restore=False):
        return noop

    # If LinearStack.poll_undo/redo fails, we still want a way to eat the
    # event. This is primarily for focused Widgets.
    @inline
    def poll_undo(self) -> bool:
        return bool  # Returns False

    @inline
    def poll_redo(self) -> bool:
        return bool  # Returns False

    is_valid: bool = True

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

    undo: list[Step]
    redo: list[Step]

    def reset(self):
        """Reset stacks."""
        self.__init__(self.adapter)

    def __init__(self, adapter: Adapter):
        self.undo = []
        self.redo = []

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
        to_stack += from_stack.pop(),
        state  = self.undo[-1]
        cursor = from_stack is self.undo and state.cursor2 or state.cursor1
        self.set_state(state.data, cursor)

    def restore_last(self):
        if self.adapter.is_valid:
            state = self.undo[-1]
            self.set_state(state.data, state.cursor2 or state.cursor1)

    def set_state(self, data, cursor):
        self.adapter.set_string(data)
        self.adapter.set_cursor(cursor)
        self.adapter.on_update(restore=True)

    def push_undo(self, tag, *, can_group=True):
        """If ``can_group`` is True, allow merging similar states."""
        if undo := self.undo:
            can_group &= tag == undo[-1].tag

        now = monotonic()
        adapter = self.adapter
        state = Step(adapter, tag=tag)

        if not can_group or now - self.last_push > 0.5 or \
                adapter.get_should_split(can_group):
            undo += state,
        else:
            undo[-1] = state

        self.last_push = now
        self.redo.clear()
        adapter.on_update()

    def update_cursor(self):
        if self.undo:
            self.undo[-1].cursor2 = self.adapter.get_cursor()

    def poll_undo(self) -> bool:
        # The init step doesn't count towards undoable steps.
        return len(self.undo) > 1 or self.adapter.poll_undo()

    def poll_redo(self) -> bool:
        return bool(self.redo or self.adapter.poll_redo())


# pydevd substitutes tuple subclass instances' own __repr__ with a useless
# string. This is a workaround specifically for debugging purposes.
class _pydevd_repr_override_meta(type):
    @property
    def __name__(cls):
        import sys
        frame = sys._getframe(1)
        if frame.f_code is not cls.__repr__.__code__:
            for v in filter(cls.__instancecheck__, frame.f_locals.values()):
                return cls.__repr__(v)
        return super().__name__


@inline
def _map_named_indices(iterables):
    return partial(map, type(_named_index(0)).__instancecheck__)


# Base for aggregate initialization classes.
class Aggregation(tuple, metaclass=_pydevd_repr_override_meta):
    __slots__ = ()
    @inline
    def __init__(self, elements): return tuple.__init__
    @inline
    def __new__(self, elements): return tuple.__new__

    def __repr__(self):
        mro_dict = get_mro_dict(self.__class__)
        
        for key in compress(mro_dict, _map_named_indices(mro_dict.values())):
            first_obj = getattr(self, key)
            try:
                obj_name = str(first_obj.__name__)
            except:
                if isinstance(first_obj, str):
                    obj_name = first_obj
                else:
                    obj_name = f"[{type(first_obj).__name__}]"
            if len(obj_name) > 30:
                obj_name = obj_name[:27] + ".."
            info = f"{key}: {obj_name},.."
            return f"{self.__class__.__name__}({info})"
        return f"{self.__class__.__name__}"


class Variadic(MemoryError):
    """Container base class for frameless construction of variadic arguments.

    Arguments are stored as a tuple in ``args`` and can be reassigned.
    Use ``_variadic_index`` for assigning named argument accessors.
    """
    __slots__   = ()
    __getitem__ = _forwarder("args.__getitem__")
    __iter__    = _forwarder("args.__iter__")

    def __repr__(self):
        return f"{self.__class__.__name__}()"


def _variadic_index(index: int):
    return property(operator.itemgetter(index))

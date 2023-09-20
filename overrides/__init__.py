"""This package implements overrides for internal operators."""

from textension.utils import _context, CFuncPtr, defer, namespace, close_cells, set_name, classproperty, filtertrue
from textension import btypes

from operator import methodcaller
from typing import Callable

from bpy.ops import _op_as_string


runtime = namespace(menu=None, active_overrides=0)
_methodcallers = {
    meth: methodcaller(meth) for meth in ("exec", "invoke", "modal", "poll")
}

_overrides = []


def restore_capsules(capsules: list[tuple]):
    for ot, method, real in capsules:
        _overrides.remove((ot, method, real))
        setattr(ot, method, real)
        runtime.active_overrides -= 1


def override(idname, method, fn):
    cfunc = btypes.wmOperatorType.get_member_type(method)

    ot = _get_wmOperatorType(idname)
    real = btypes.cast(getattr(ot, method), cfunc)

    if defaults := list(fn.__defaults__ or ()):
        defaults[-1] = real
    else:
        defaults = (real,)
    fn.__defaults__ = tuple(defaults)

    # Assign the new function pointer to the operator type.
    setattr(ot, method, cfunc(fn))
    runtime.active_overrides += 1
    _overrides.append((ot, method, real))
    return (ot, method, real)


def _safe_print_traceback():
    try:
        import traceback
        traceback.print_exc()
    except:
        return None


# OpOverride models bpy.types.Operator methods, but not the parameters.
class OpOverride:
    overrides: list["OpOverride"]   # List of overridden methods.

    exec:   Callable
    invoke: Callable
    modal:  Callable
    poll:   Callable

    real: CFuncPtr  # The real function pointer.
    args: tuple

    @classproperty
    def bl_idname(cls):
        raise AttributeError

    @property
    def context(self):
        return self.args[0]

    @property
    def op(self):
        return self.args[1] if len(self.args) > 1 else None
    
    @property
    def event(self):
        if len(self.args) > 2:
            return self.args[2].contents
        # exec methods don't receive an event. Try the window eventstate.
        return _context.window.event

    def __init_subclass__(cls):
        name = cls.__name__
        # Skip non-operator subclasses.
        if "_OT_" in name:
            _op_as_string(name)
            cls.overrides = []
            cls.bl_idname = name.replace("_OT_", ".").lower()

    @classmethod
    def get_defined_methods(cls):
        for method_name in cls.iter_method_names():
            yield getattr(cls, method_name)

    @classmethod
    def iter_method_names(cls):
        for name in ("exec", "invoke", "modal", "poll"):
            if getattr(cls, name, None):
                yield name

    @classmethod
    def apply_override(cls):
        assert not cls.overrides, f"{cls} already overridden"

        for name in cls.iter_method_names():
            call_method = _methodcallers[name]

            @close_cells(call_method, cls)
            @set_name(f"{cls.__name__}.{name} (overridden)")
            def wrapper(ctx, op=None, event=None, real=None):
                instance = cls()
                instance.args = tuple(filtertrue((ctx, op, event)))
                instance.real = real

                try:
                    return call_method(instance)
                except:
                    _safe_print_traceback()
                    # We need to return even on exceptions.
                    return btypes.defs.OPERATOR_CANCELLED

            cls.overrides += override(cls.__name__, name, wrapper),

    @classmethod
    def remove_override(cls):
        restore_capsules(cls.overrides)
        cls.overrides.clear()

    def default(self):
        """Call the default, unoverridden operator method."""
        return self.real(*self.args)


# Cache internal operators to avoid unnecessary menu lookups.
_internal_optypes = {}


def _get_wmOperatorType(idname):
    if ret := _internal_optypes.get(idname):
        return ret
    _op_as_string(idname)

    # wmOperatorType isn't exposed elsewhere.
    if not (menu := runtime.menu):
        wm = _context.window_manager
        win_p = btypes.bContext(_context).wm.window
        if not win_p:
            win_p.contents = btypes.wmWindow(wm.windows[0])

        menu = runtime.menu = wm.popmenu_begin__internal("")

        def end_menu():
            wm.popmenu_end__internal(menu)
            _context.window.screen = _context.window.screen
            runtime.menu = None

        defer(end_menu, persistent=True)

    menu.layout.operator(idname.replace("_OT_", ".").lower())
    for but in btypes.uiPopupMenu(menu).block.contents.buttons:
        if ot := but.optype and but.optype.contents:
            if ot.idname.decode() == idname:
                # ``ot.pyop_poll`` is False for internal, non-python operators
                # which are the only ones we cache.
                if not ot.pyop_poll:
                    return _internal_optypes.setdefault(idname, ot)
                return ot
    # Should not be reached, unless the C API broke.
    assert False


def init():
    from .default import apply_default_overrides

    assert runtime.active_overrides == 0
    apply_default_overrides()

    # Remove undo for bpy.ops.text.open. It could support tagging the blend
    # file dirty, but never should opening a text itself warrant an undo step.
    ot = _get_wmOperatorType("TEXT_OT_open")
    ot.flag &= ~btypes.defs.OPTYPE_UNDO


def cleanup():
    from .default import remove_default_overrides

    remove_default_overrides()
    assert runtime.active_overrides == 0, runtime.active_overrides

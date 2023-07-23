# This plugin implements an experimental undo stack for texts.
# This affects the operators:
# - ED_OT_undo:         Resync when undoing (ctrl z)
# - ED_OT_redo:         Resync when redoing (ctrl shift z)
# - ED_OT_undo_history: Resync when undoing a specific undo step.

from textension.utils import LinearStack, Adapter, TextOperator, _check_type, close_cells, set_name, text_from_id, iter_spaces, unsuppress, inline_class, noop_noargs
from textension.core import ensure_cursor_view
from textension import utils
from textension.overrides import override, restore_capsules, _get_wmOperatorType
from textension.overrides.default import Default

from textension.btypes.defs import OPERATOR_CANCELLED, OPERATOR_FINISHED, OPTYPE_UNDO, TXT_ISDIRTY, TXT_ISMEM
from typing import Callable
import bpy


_context = utils._context

undo_store: dict[int, "LinearStack"] = {}


# Dict of python operators using the new undo system
_pyop_store: dict[bpy.types.Operator, list[str]] = {}

# List of Default overrides with undos.
_applied_default_undos = []


def _should_split(text: bpy.types.Text):
    """Examines the last type character and determine if an undo that would
    otherwise be grouped, should be split.
    """
    # If the cursor has a selection, treat it as a split.
    curl, curc, sell, selc = text.cursor_sorted
    if curc != selc or curl != sell or curc < 1:
        return True
    separators = {*" !\"#$%&\'()*+,-/:;<=>?@[\\]^`{|}~."}
    if text.lines[curl].body[curc - 1] in separators:

        # Consecutively typed separators can be grouped.
        # operator, op_locals = _get_caller_operator_locals()
        # if operator is not None:
        #     if operator.bl_idname == "TEXTENSION_OT_insert":
        #         if op_locals.get("typed_char") in separators:
        #             return False
        return True
    return False


# def _get_caller_operator_locals():
#     import sys
#     frame = sys._getframe(2)
#     while frame is not None:
#         for value in frame.f_locals.values():
#             if is_operator(value):
#                 return value, frame.f_locals
#         frame = frame.f_back
#     return None, {}


class TextAdapter(Adapter):
    metadata: tuple[str, float, int]

    def __init__(self, text: bpy.types.Text):
        _check_type(text, bpy.types.Text)
        self.name = text.name
        self.id   = text.id

        self.metadata = ("", 0.0, 0)

    @property
    def text(self) -> bpy.types.Text:
        texts = bpy.data.texts
        try:
            text = texts[self.name]
            text_id = text.id
            assert text_id == self.id

        except (KeyError, AssertionError):

            # Find the text by its id.
            if text := text_from_id(self.id):
                self.name = text.name

            # Find the text by its name.
            elif text := texts.get(self.name):
                remap_id = text.id
                assert remap_id not in undo_store
                undo_store[remap_id] = undo_store.pop(self.id)
                self.id = remap_id

            # Blender removed the text. Restore it.
            else:
                text = texts.new(self.name)
                text.id_proxy = self.id
        return text

    def get_string(self):
        return self.text.as_string()

    def get_cursor(self):
        return self.text.cursor.copy()

    def set_cursor(self, cursor):
        self.text.cursor = cursor

    def set_string(self, data):
        self.text.from_string(data)

    def get_should_split(self, hint: bool):
        return _should_split(self.text)

    def update(self, restore=False):
        text = self.text
        internal = text.internal
        if restore:
            text.filepath, internal.mtime, internal.flags = self.metadata
        else:
            self.metadata = (text.filepath, internal.mtime, internal.flags)

    @property
    def is_valid(self):
        return bool(self.text)


# Dummy stack. Makes it easier to write generic code for undo/redo.
@inline_class(Adapter())
class NO_STACK(LinearStack):
    undo = ()
    redo = ()

    __bool__ = bool

    push_intermediate_cursor = noop_noargs


from operator import attrgetter, not_
from itertools import compress

id_from_text  = attrgetter("id")
id_from_stack = attrgetter("adapter.id")
sync_ids = set()
sync_map = {}


@unsuppress
def sync_pre():
    stacks = map(ensure_stack, bpy.data.texts)
    sync_ids.update(map(id_from_stack, stacks))

    for st in iter_spaces(space_type='TEXT_EDITOR'):
        if text := st.text:
            sync_map[st] = id_from_text(text)
        else:
            sync_map[st] = None


@unsuppress
def sync_post():
    # Remove texts whose ids dont match those from sync_pre.
    saved_ids = map(sync_ids.__contains__, map(id_from_text, bpy.data.texts))
    bpy.data.batch_remove(compress(bpy.data.texts, map(not_, saved_ids)))

    # Restore texts Blender removed from its undo/redo step.
    for stack in tuple(undo_store.values()):
        stack.restore_last()

    # Restore the assigned texts to open editors.
    for st in iter_spaces(space_type='TEXT_EDITOR'):
        st.text = text_from_id(sync_map.get(st))

    sync_map.clear()
    sync_ids.clear()


@bpy.app.handlers.persistent
def purge(*unused_args):
    """Purge stacks and create new for any text blocks.
    This is called via bpy.app.handlers.load_post.
    """
    for stack in undo_store.values():
        stack.undo.clear()
        stack.redo.clear()

    undo_store.clear()
    all(map(ensure_stack, bpy.data.texts))


def _has_undo(cls: bpy.types.Operator):
    return 'UNDO' in getattr(cls, "bl_options", set())


# Method wrapper for python operators. Similar to the C-method wrapper below.
def pyop_wrapper(context, op=None, event=None, method: Callable = None):
    # Ensure a stack exists before calling any operators.
    stack = get_active_stack()
    result = method(*filter(None, (context, op, event)))

    if result == OPERATOR_FINISHED:
        stack.push_undo(tag=op.contents.idname.decode())
        # Prevent Blender from pushing its own undo.
        result = OPERATOR_CANCELLED
    return result


def pyop_enable_new_undo(cls) -> bool:
    _check_type(cls, bpy.types.Operator)
    assert cls.is_registered, f"Operator '{cls}' is not registered"

    if not _has_undo(cls):
        return

    idname = cls.bl_rna.identifier
    methods = _pyop_store.setdefault(cls, [])

    for method in ("invoke", "execute", "modal"):
        if hasattr(cls, method):
            if method == "execute":
                method = "exec"
            methods += override(idname, method, pyop_wrapper),


def pyop_disable_new_undo(cls):
    if not _has_undo(cls):
        assert cls not in _pyop_store
        return
    restore_capsules(_pyop_store[cls])
    _pyop_store[cls].clear()
    del _pyop_store[cls]


def ensure_stack(text: bpy.types.Text):
    _check_type(text, bpy.types.Text)

    id = text.id

    try:
        stack = undo_store[id]
    except KeyError:
        stack = undo_store[id] = LinearStack(TextAdapter(text))
    return stack


def get_active_stack():
    if text := getattr(_context, "edit_text", None):
        return ensure_stack(text)
    return NO_STACK


def undo_poll():
    return get_active_stack().poll_undo()


def redo_poll():
    return get_active_stack().poll_redo()


def undo_pre():
    if stack := get_active_stack():
        if stack.pop_undo():
            ensure_cursor_view(speed=2)
        return True
    return False


def redo_pre():
    if stack := get_active_stack():
        if stack.pop_redo():
            ensure_cursor_view(speed=2)
        return True
    return False


def unlink_pre():
    undo_store.pop(_context.edit_text.id, None)


def new_post(result):
    if result == OPERATOR_FINISHED:
        ensure_stack(_context.text)


def save_post(result=None):
    if result in {None, OPERATOR_FINISHED}:
        # Update filepath, modified time and flags from text.
        stack = ensure_stack(_context.edit_text)
        stack.adapter.update()


# This applies the new undo for all Default operator overrides.
def _apply_default_undo():
    for cls in Default.operators:
        ot = _get_wmOperatorType(cls.__name__)

        if not ot.flag & OPTYPE_UNDO:
            continue

        for method in cls.get_defined_methods():
            name = method.__name__

            if name == "poll":
                continue

            @close_cells(cls, method)
            @set_name(f"{name} (Undo wrapped)")
            def wrapper(self: cls):
                stack = get_active_stack()

                # If the cursor was moved, store the current position.
                stack.push_intermediate_cursor()

                try:
                    result = method(self)
                except:
                    result = OPERATOR_CANCELLED
                else:
                    # Operator finished, push a step, ignore native undo.
                    if result == OPERATOR_FINISHED:
                        stack.push_undo(tag=cls.__name__)
                        result = OPERATOR_CANCELLED
                finally:
                    return result

            wrapper.__name__ = name
            wrapper.__qualname__ = name
            setattr(cls, name, wrapper)
            _applied_default_undos.append((cls, name, method))


def _remove_default_undo():
    for restore_args in _applied_default_undos:
        setattr(*restore_args)
    _applied_default_undos.clear()


def enable():
    # Registered TextOperators.
    for cls in TextOperator.__subclasses__():
        if cls.is_registered:
            pyop_enable_new_undo(cls)

    TextOperator._register_hooks += pyop_enable_new_undo,
    TextOperator._unregister_hooks += pyop_disable_new_undo,

    # Internal operators.
    _apply_default_undo()

    # Override ED_OT_undo and ED_OT_redo's poll/exec methods to use the new
    # undo stack when the cursor is inside the text editor.
    from textension.overrides.default import ED_OT_undo, ED_OT_redo, ED_OT_undo_history
    ED_OT_undo.add_poll(undo_poll)
    ED_OT_undo.add_pre(undo_pre, is_global=True)
    ED_OT_undo._sync_pre_hooks += sync_pre,
    ED_OT_undo._sync_post_hooks += sync_post,

    ED_OT_redo.add_poll(redo_poll)
    ED_OT_redo.add_pre(redo_pre, is_global=True)
    ED_OT_redo._sync_post_hooks += sync_post,
    ED_OT_redo._sync_pre_hooks += sync_pre,

    ED_OT_undo_history._sync_pre_hooks += sync_pre,
    ED_OT_undo_history._sync_post_hooks += sync_post,

    # Register handler that purges all undo states between blend file loads.
    bpy.app.handlers.load_post.append(purge)

    from textension.overrides.default import TEXT_OT_unlink, TEXT_OT_new, TEXT_OT_save, TEXT_OT_save_as, TEXT_OT_resolve_conflict
    TEXT_OT_unlink.add_pre(unlink_pre, is_global=True)
    TEXT_OT_new.add_post(new_post, is_global=True)
    TEXT_OT_save.add_post(save_post, is_global=True)
    TEXT_OT_save_as.add_post(save_post, is_global=True)
    TEXT_OT_resolve_conflict.add_post(save_post, is_global=True)


def disable():
    TextOperator._register_hooks.remove(pyop_enable_new_undo)
    TextOperator._unregister_hooks.remove(pyop_disable_new_undo)

    # Restore Python-based operators.
    for cls in list(_pyop_store):
        pyop_disable_new_undo(cls)

    # Restore C operators.
    from textension.overrides.default import ED_OT_undo, ED_OT_redo
    ED_OT_undo.remove_poll(undo_poll)
    ED_OT_undo.remove_pre(undo_pre)
    ED_OT_undo._sync_post_hooks.remove(sync_post)
    ED_OT_undo._sync_pre_hooks.remove(sync_pre)

    ED_OT_redo.remove_poll(redo_poll)
    ED_OT_redo.remove_pre(redo_pre)
    ED_OT_redo._sync_post_hooks.remove(sync_post)
    ED_OT_redo._sync_pre_hooks.remove(sync_pre)

    from textension.overrides.default import TEXT_OT_unlink, TEXT_OT_new, TEXT_OT_save, TEXT_OT_save_as, TEXT_OT_resolve_conflict
    TEXT_OT_unlink.remove_pre(unlink_pre)
    TEXT_OT_new.remove_post(new_post)
    TEXT_OT_save.remove_post(save_post)
    TEXT_OT_save_as.remove_post(save_post)
    TEXT_OT_resolve_conflict.remove_post(save_post)

    _remove_default_undo()

    purge()
    bpy.app.handlers.load_post.remove(purge)

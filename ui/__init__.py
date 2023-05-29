# This package implements user interface related classes and utilities.

from .utils import *


def init():
    from textension.utils import register_classes
    from .operators import classes
    register_classes(classes)

    from . import gl
    gl.init()

    from .utils import patch_main_draw
    patch_main_draw()


def cleanup():
    from textension.utils import unregister_classes
    from .operators import classes
    unregister_classes(classes)

    from . import gl
    gl.cleanup()

    from .utils import unpatch_main_draw, clear_widget_focus
    clear_widget_focus()
    unpatch_main_draw()
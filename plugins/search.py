

from textension.ui.widgets import Input, Widget
from textension.ui.utils import set_widget_focus, _visible
from textension.utils import _context, TextOperator
from textension import utils


class Search(Widget):
    width  = 350
    height = 450

    is_visible: bool
    background_color = 0.2, 0.2, 0.2, 1.0
    border_color = 0.25, 0.25, 0.25, 1.0

    font_size = 16

    def __init__(self, st):
        super().__init__(parent=None)
        self.space_data = st
        self.is_visible = False
        self.input = Input(parent=self)
        self.update_uniforms(corner_radius=2.0)
        self.input.set_hint("Search")

    def dismiss(self):
        self.input.on_defocus()

        if self.is_visible:
            self.is_visible = False
        utils.safe_redraw()

    def draw(self):
        region = _context.region
        w = 500
        h = 30
        x = (region.width  - w) // 2
        y = (region.height - h) // 2
        self.rect.draw(x, y, w, h)
        self.input.draw()

    def on_activate(self):
        self.is_visible = True

        if self not in _visible:
            _visible.append(self)

        set_widget_focus(self.input)
        utils.safe_redraw()


def draw_search():
    search = get_search()
    if search.is_visible:
        search.draw()


@utils.inline
def get_search() -> Search:
    return utils.make_space_data_instancer(Search)


def test_search(x, y):
    search = get_search()
    if search.is_visible:
        return search.hit_test(x, y)


class TEXTENSION_OT_search(TextOperator):
    poll = utils.text_poll
    utils.km_def("Text Generic", 'F', 'PRESS', ctrl=True)

    def invoke(self, context, event):
        search = get_search()
        search.on_activate()

        text = context.edit_text
        if text.current_line_index == text.select_end_line_index and \
            (string := text.selected_text):
                # Setting a new search string resets the input undo.
                search.input.set_string(string, select=True, reset=True)

        else:
            search.input.select_all()
        return {'FINISHED'}


classes = (
    TEXTENSION_OT_search,
)


def _enable():
    utils.register_classes(classes)
    utils.add_draw_hook(draw_search, draw_index=11)

    from textension.ui.utils import add_hit_test
    add_hit_test(test_search)


def _disable():
    utils.unregister_classes(classes)

    get_search.__kwdefaults__["cache"].clear()
    utils.remove_draw_hook(draw_search)

    # Hit testing
    from textension.ui.utils import remove_hit_test
    remove_hit_test(test_search)

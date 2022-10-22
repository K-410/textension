# This module implements classes for custom drawing.

from contextlib import contextmanager

from gpu.state import blend_set, viewport_get, viewport_set
from gpu.types import (GPUBatch, GPUFrameBuffer, GPUShader, GPUTexture,
                       GPUVertBuf)
from mathutils import Matrix, Vector


# Shader and batch caches. These should be cleared when addon is uninstalled.
shader_cache = {}
batch_cache = {}


def clear_caches():
    shader_cache.clear()
    batch_cache.clear()


# Generic rectangle vertex shader.
# mat is assumed to hold the scale and translation of the rectangle.
xyzw_vert = """
uniform mat4 ProjectionMatrix;
uniform mat4 mat;
in vec4 pos;
void main() {
    gl_Position = ProjectionMatrix * mat * pos;
}"""


# Bordered rectangle fragment shader.
# r holds the rounding radius and a value of 1 means no roundness.
rect_bordered_frag = """
uniform mat4 mat;
uniform float r = 1.0f;  // Roundness.
uniform vec4 background = vec4(1.0f);
uniform vec4 border_color = vec4(1.0f);

vec2 h = vec2(mat[0][0], mat[1][1]) / 2.0f;
vec4 col;
float a, b, fac;
out vec4 fragColor;

void main() {
    vec2 pos = gl_FragCoord.xy - mat[3].xy;
    b = length(max(abs(pos - h) - h + r, 0.0f)) - r;
    a = 0.75f - min(1.0f, max(-0.25f, b));

    if (b < -0.5f) {
        fac = min(1.0f, max(0.0f, 1.63f + a / b));
        col = vec4(mix(border_color, background, fac).rgb, background.a * a);
    }
    else col = vec4(border_color.rgb, border_color.a * a);

    fragColor = col;
}
"""

# Uniform simple rectangle fragment shader.
plain_frag = """
uniform vec4 background = vec4(1.0);
out vec4 fragColor;

void main() {
    fragColor = background;
}
"""

# Texture shader. Same as xyzw_vert, but with uvs.
tex_vert = """
uniform mat4 ProjectionMatrix;
uniform mat4 mat;

in vec4 pos;
out vec2 uv;

void main() {
    uv = pos.xy;
    gl_Position = ProjectionMatrix * mat * pos;
}
"""

tex_frag = '''
uniform sampler2D image;

in vec2 uv;
out vec4 fragColor;

void main() {
    fragColor = texture(image, uv);
}
'''


class ImmRect:
    """Base for immediate-mode drawable rectangles"""

    matrix: Matrix
    x:      float
    y:      float
    width:  float
    height: float

    def __init__(self, vert, frag):
        try:
            shader, fmt = shader_cache[vert, frag]
        except KeyError:
            shader_cache[vert, frag] = (
                shader := GPUShader(vert, frag),
                   fmt := shader.format_calc())
        try:
            batch = batch_cache[shader]
        except KeyError:
            (vbo := GPUVertBuf(fmt, 4)).attr_fill("pos",
                ((0.0, 0.0, 0.0, 1.0), (1.0, 0.0, 0.0, 1.0),
                 (1.0, 1.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0)))
            batch = batch_cache[shader] = GPUBatch(type='TRI_FAN', buf=vbo)

        self.blend_type = 'ALPHA'
        self.shader = shader
        self.batch = batch

        self.uniforms = {}
        self.matrix = self.uniforms["mat"] = Matrix()
        # Bind the first two matrix rows for faster attribute access.
        self._rows12 = (self._row1, self._row2) = self.matrix[:2]
        self.background = self.uniforms["background"] = Vector((1.0, 1.0, 1.0, 1.0))

    def set_background_color(self, r, g, b, a):
        """Set the background color using a sequence of 4 floats."""
        try:
            # When this fails due to bad type, the exception thrown is useful.
            flt = float
            self.background[:] = flt(r), flt(g), flt(b), flt(a)
        except:
            raise ValueError(f"Expected sequence of 4 floats, got {r, g, b, a}")

    def __call__(self, x: float, y: float, w: float, h: float) -> None:
        """Draw at the coordinates (x, y) with size (w, h)"""
        row1, row2 = self._rows12
        self.x = row1[3] = x
        self.y = row2[3] = y
        self.width = row1[0] = w
        self.height = row2[1] = h
        blend_set(self.blend_type)
        self._draw()

    def _draw(self):
        self.shader.bind()
        for attr, value in self.uniforms.items():
            self.shader.uniform_float(attr, value)
        self.batch.draw(self.shader)

    def hit_test(self, x: int | float, y: int | float) -> bool:
        """Hit test this rectangle. Assumes x/y is region space."""
        x -= self._row1[3]
        if x >= 0 and x < self._row1[0]:
            y -= self._row2[3]
            return y >= 0 and y < self._row2[1]
        return False

    @property
    def x2(self):
        """x + width"""
        return self._row1[0] + self._row1[3]

    @property
    def y2(self):
        """y + height"""
        return self._row2[1] + self._row2[3]


class GLRoundedRect(ImmRect):
    """Rounded rectangle class"""

    def __init__(self, r=1.0, g=1.0, b=1.0, a=1.0):
        super().__init__(xyzw_vert, rect_bordered_frag)
        self.set_background_color(r, g, b, a)
        self.border_color = self.uniforms["border_color"] = [r, g, b, a]

    def set_border_color(self, *color: float):
        """Set the color of the rectangle border"""
        self.border_color[:] = color

    def set_roundness(self, pixels: int):
        """Set the corner roundness in pixels"""
        # TODO: We need to clamp the radius to 1.5 because the shader doesn't
        # TODO: handle lower values. Which means we can't get sharp edges.
        self.uniforms["r"] = max(1.5, pixels)


class GLPlainRect(ImmRect):
    """Plain rectangle class"""

    def __init__(self, r=1.0, g=1.0, b=1.0, a=1.0):
        super().__init__(xyzw_vert, plain_frag)
        self.set_background_color(r, g, b, a)


class GLTexture(ImmRect):
    def __init__(self, width, height):
        super().__init__(tex_vert, tex_frag)
        self.color_texture = GPUTexture((width, height), format='RGBA8')
        (self.width, self.height) = self.size = (width, height)
        self.blend_type = 'ALPHA_PREMULT'

    def resize(self, width, height):
        self.__init__(width, height)

    def _draw(self):
        self.shader.bind()
        self.shader.uniform_float("mat", self.matrix)
        self.shader.uniform_sampler("image", self.color_texture)
        self.batch.draw(self.shader)

    @contextmanager
    def bind(self):
        vp = viewport_get()  # Read _before_ binding the framebuffer.
        self.color_texture.clear(format='FLOAT', value=(0.0, 0.0, 0.0, 0.0))
        # FBOs aren't freed when guardedalloc runs on Blender exit, so we don't
        # keep them around. Not ideal, but FBOs are at least cheap to make.
        with GPUFrameBuffer(color_slots=self.color_texture).bind():
            viewport_set(*vp)
            yield


def _add_blend_4f(src, dst):
    """Blend src with dst, with clamp"""
    r, g, b, a = src
    r2, g2, b2, a2 = dst
    r += r2 * a2
    g += g2 * a2
    b += b2 * a2
    if r > 1.0:
        r = 1.0
    if g > 1.0:
        g = 1.0
    if b > 1.0:
        b = 1.0
    return r, g, b, a


def _add_blend_4f_unclamped(src, dst):
    """Blend src with dst, unclamped"""
    r, g, b, a = src
    r2, g2, b2, a2 = dst
    return r + (r2 * a2), g + (g2 * a2), b + (b2 * a2), a

# def arrow(x, y, size):
#     blend_set('ALPHA')
#     shader = gpu.shader.from_builtin("2D_UNIFORM_COLOR")
#     shader.bind()
#     shader.uniform_float("color", (1.0, 1.0, 1.0, 0.4))
#     vbo = GPUVertBuf(shader.format_calc(), 3)
#     vbo.attr_fill("pos", ((x, y), (x + size, y), (x + size, y + size)))
#     GPUBatch(type='TRI_FAN', buf=vbo).draw(shader)
# Unused.
# def glline(x1, y1, x2, y2, color=None, style="line"):
#     if color is None:
#         color = 1.0, 1.0, 1.0, 1.0
#     if color[3] < 1.0:
#         blend_set('ALPHA')


#     shader = shcache[style]
#     shader.bind()
#     shader.uniform_float("color", color)
#     vbo = GPUVertBuf(shader.format_calc(), 2)
#     vbo.attr_fill("pos", ((x1, y1, 0, 1), (x2, y2, 0, 1)))
#     GPUBatch(type='LINES', buf=vbo).draw(shader)


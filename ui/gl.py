"""This module implements classes for custom drawing."""

from gpu.state import viewport_get, viewport_set, blend_set
from gpu.types import GPUFrameBuffer, GPUTexture
from mathutils import Vector
from textension.utils import cm, consume, inline
from itertools import starmap
from operator import attrgetter
from functools import partial

import gpu


@inline
def set_blend_alpha_premult():
    return partial(gpu.state.blend_set, 'ALPHA_PREMULT')


def init():
    vbo = gpu.types.GPUVertBuf(gpu.types.GPUVertFormat(), 4)

    Rect.shader = gpu.types.GPUShader(rct_vert, rct_frag)
    Rect.batch  = gpu.types.GPUBatch(type='TRI_FAN', buf=vbo)
    Rect.batch.program_set(Rect.shader)

    Texture.shader = gpu.types.GPUShader(tex_vert, tex_frag)
    Texture.batch  = gpu.types.GPUBatch(type='TRI_FAN', buf=vbo)
    Texture.batch.program_set(Texture.shader)


def cleanup():
    Rect.shader = None
    Rect.batch  = None

    Texture.shader = None
    Texture.batch  = None

    for instance in Texture._instances:
        instance.__dict__.clear()

    Texture._instances.clear()


def _add_blend_4f(src, dst):
    """Blend src with dst, with saturation"""
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


# Rect vertex shader.
rct_vert = """
const vec4 data[4] = {
    {-1.0, -1.0, 0.0, 1.0},
    { 1.0, -1.0, 0.0, 1.0},
    { 1.0,  1.0, 0.0, 1.0},
    {-1.0,  1.0, 0.0, 1.0},
};

void main() {
    gl_Position = data[gl_VertexID];
};
"""


# Rect fragment shader.
rct_frag = """
uniform vec4  rect             = vec4(200, 200, 50, 50);
uniform float corner_radius    = 0.0;
uniform vec4  background_color = vec4(vec3(0.02), 1.0);

uniform vec4  border_color     = vec4(vec3(0.15), 1.0);
uniform float border_width     = 1.0;

uniform vec4  shadow           = vec4(0.0, 0.0, 0.0, 1.0);
uniform vec2  shadow_offset    = vec2(3.0, -3.0);

out vec4 fragColor;

float rbox(vec2 center, vec2 size, float r) {
    vec2 q = abs(center) - size + r;
    return min(max(q.x, q.y), 0.0) + length(max(q, 0.0)) - r;
}

void main() {
    vec4 final = background_color;
    float bw   = border_width;
    
    vec4 shadow_final = mix(vec4(0.0), shadow, shadow.a);

    // Border is driven by its own transparency and width. When either
    // of those are zero, the border is replaced with the background.
    vec4 border_color = mix(final, border_color, border_color.a * min(1.0, bw));
    
    vec2  size   = (rect.zw - 1.0) * 0.5;
    vec2  center = gl_FragCoord.xy - (rect.xy + size) + vec2(-0.5);
    float dist   = rbox(center, size, corner_radius);

    float shadow_mul = smoothstep(-6, 6, rbox(center - shadow_offset, size, corner_radius));
    vec4 shadow_mix  = vec4(shadow_final.rgb, shadow.a * (1 - shadow_mul));


    shadow_mix = mix(final, shadow_mix, smoothstep(-1.0, 0.0, dist));

    float dist2       = rbox(center * 1.03, size, corner_radius);
    float rect_mask   = smoothstep(1.0, 0.0, dist);
    float border_mask = smoothstep(bw - 0.5, bw, abs(dist));

    final = mix(final, shadow_mix, smoothstep(0.0, 1.0, dist2));
    final = mix(final, border_color, rect_mask);
    final = mix(final, shadow_mix, border_mask);

    fragColor = final;
}
"""


# Texture vertex shader.
tex_vert = """
const vec2 data[4] = {
    {-1.0, -1.0},
    { 1.0, -1.0},
    { 1.0,  1.0},
    {-1.0,  1.0},
};
const vec2 uv_data[4] = {
    {0.0, 0.0},
    {1.0, 0.0},
    {1.0, 1.0},
    {0.0, 1.0},
};

out vec2 uv;
void main() {
    gl_Position.xy = data[gl_VertexID];
    uv = uv_data[gl_VertexID];
};
"""


# Texture fragment shader.
tex_frag = '''
uniform sampler2D image;

in vec2 uv;
out vec4 fragColor;

void main() {
    fragColor = texture(image, uv);
}
'''


class Uniforms(dict):
    background_color: Vector
    border_color:     Vector
    border_width:     float

    __getattribute__ = dict.__getitem__
    __setattr__      = dict.__setitem__
    __delattr__      = dict.__delitem__
    __hash__         = object.__hash__


class Rect(Vector):
    """
    Uniforms:

    ``background_color`` : rectangle background color
    ``border_color``     : rectangle border color
    ``border_width``     : rectangle border width
    ``corner_radius``    : rounding corner radius
    ``rect``             : rectangle position and dimensions
    ``shadow``           : drop shadow color
    """
    __slots__  = ("uniforms", "blend_mode")

    __hash__   = object.__hash__  # Hash by object identity

    # Convenience descriptors
    x:                     float  = Vector.x
    y:                     float  = Vector.y
    width:                 float  = Vector.z
    height:                float  = Vector.w
    position: tuple[float, float] = Vector.xy
    size:     tuple[float, float] = Vector.zw

    batch:      gpu.types.GPUBatch
    blend_mode: str
    shader:     gpu.types.GPUShader

    def __init__(self):
        self.resize_4d()
        self.blend_mode = "ALPHA"
        self.uniforms   = Uniforms(
            rect            =self,
            background_color=Vector((1.0, 1.0, 1.0, 1.0)),
            border_color    =Vector((1.0, 1.0, 1.0, 1.0)),
            border_width    =1.0,
            corner_radius   =0.0,
            shadow          =Vector((0.0, 0.0, 0.0, 0.0))
        )

    def draw(self, x, y, w, h):
        self[0] = x
        self[1] = y
        self[2] = w
        self[3] = h
        self.shader.bind()
        consume(starmap(self.shader.uniform_float, dict.items(self.uniforms)))
        blend_set(self.blend_mode)
        self.batch.draw()

    def update_uniforms(self, **kw):
        uniforms = self.uniforms

        # Not an important sanity check, but we still want to be correct.
        if unknown := kw.keys() - uniforms:
            raise ValueError("\n\n"
                f"Bad uniform: {', '.join(unknown)}\n"
                f"Expected one of: {', '.join(uniforms)}")

        for name, value in kw.items():
            if isinstance(value, (int, float)):
                uniforms[name] = value
            else:
                uniforms[name][:] = value

    def hit_test(self, x: float, y: float) -> bool:
        if (x := x - self[0]) >= 0.0 and x < self[2]:
            return (y := y - self[1]) >= 0.0 and y < self[3]
        return False

    @property
    @inline
    def background_color(self):
        return attrgetter("uniforms.background_color")

    @background_color.setter
    def background_color(self, rgba):
        self.uniforms.background_color[:] = rgba

    @property
    @inline
    def border_color(self):
        return attrgetter("uniforms.border_color")

    @border_color.setter
    def border_color(self, rgba):
        self.uniforms.border_color[:] = rgba

    @property
    @inline
    def border_width(self):
        return attrgetter("uniforms.border_width")

    @border_width.setter
    def border_width(self, value: float):
        self.uniforms.border_width = float(value)

    @property
    @inline
    def corner_radius(self):
        return attrgetter("uniforms.corner_radius")

    @corner_radius.setter
    def corner_radius(self, value: float):
        self.uniforms.corner_radius = max(0.0, float(value))

    @property
    def x2(self):
        """x + width"""
        return self[0] + self[2]

    @property
    def y2(self):
        """y + height"""
        return self[1] + self[3]

    @property
    def inner_x(self) -> float:
        return self.x + self.border_width

    @property
    def inner_y(self) -> float:
        return self.y + self.border_width

    @property
    def width_inner(self) -> float:
        return self.width - (self.border_width * 2.0)

    @property
    def height_inner(self) -> float:
        return self.height - (self.border_width * 2.0)

    @height_inner.setter
    def height_inner(self, value):
        self.height = value + (self.border_width * 2.0)

    @property
    def position_inner(self) -> tuple[float, float]:
        """Position of the inner rect, after counting border width"""
        border_width = self.border_width
        return self.x + border_width, self.y + border_width

    @property
    def size_inner(self):
        """Size of the inner rect"""
        bw2 = (self.border_width * 2.0)
        return round(self.width - bw2), round(self.height - bw2)


class Texture:
    x: float = 0
    y: float = 0

    size: tuple[int, int] = (-1, -1)

    _instances = []  # Track instances so we can clean up FBOs and textures.

    shader: gpu.types.GPUShader  # Assigned in gl.init()
    batch:  gpu.types.GPUBatch   # Assigned in gl.init()

    def __new__(cls, *_):
        cls._instances += super().__new__(cls),
        return cls._instances[-1]

    def __init__(self, size: tuple[int, int] = (100, 100)):
        self.size = (_, _) = tuple(map(int, size))
        self.texture = GPUTexture(self.size)
        self.fbo = GPUFrameBuffer(color_slots=self.texture)
        self.clear_texture  = partial(self.fbo.clear, color=(0.0,) * 4)
        self.upload_texture = partial(self.shader.uniform_sampler, "image", self.texture)

    def draw(self):
        self.shader.bind()
        self.upload_texture()

        # For restoring viewport rect.
        viewport = viewport_get()
        set_blend_alpha_premult()
        viewport_set(self.x, self.y, *self.size)

        self.batch.draw()
        viewport_set(*viewport)

    @cm.decorate
    def bind(self):
        viewport = viewport_get()

        with self.fbo.bind():
            self.clear_texture()
            viewport_set(*viewport)
            yield

    def resize(self, size: tuple[int, int] = (100, 100)):
        if size != self.size:
            self.__init__(size)

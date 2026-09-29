shader_type spatial;

uniform vec3 water_color : source_color = vec3(0.08, 0.22, 0.26);
uniform sampler2D ripple_noise : repeat_enable;
uniform float ripple_strength = 0.12;

void fragment() {
	vec2 uv = UV * 3.0 + vec2(TIME * 0.04, TIME * 0.025);
	vec3 ripple = texture(ripple_noise, uv);
	ALBEDO = water_color + ripple * ripple_strength;
	ROUGHNESS = 0.05;
	METALLIC = 0.3;
	SPECULAR = 0.6;
}

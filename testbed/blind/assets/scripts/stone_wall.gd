extends MeshInstance3D

@export var length := 22.0
@export var height := 4.0
@export var columns := 11
@export var rows := 4
@export var tile_size := 2.0


func _ready() -> void:
	mesh = _build_wall()


func _build_wall() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for row in rows + 1:
		for col in columns + 1:
			var x := length * col / columns
			var y := height * row / rows
			st.set_uv(Vector2(x / tile_size, (height - y) / tile_size))
			st.set_normal(Vector3.BACK)
			st.add_vertex(Vector3(x, y, 0.0))
	for row in rows:
		for col in columns:
			var a := row * columns + col
			var b := a + 1
			var c := a + columns + 1
			var d := c + 1
			st.add_index(a)
			st.add_index(c)
			st.add_index(b)
			st.add_index(b)
			st.add_index(c)
			st.add_index(d)
	st.generate_tangents()
	return st.commit()

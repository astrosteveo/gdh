extends MeshInstance3D
## A mesh of two surfaces side by side, each a 1 by 1 quad facing the camera: surface 0 on the left with the material
## Left, surface 1 on the right with Right.


func _ready() -> void:
	var array_mesh := ArrayMesh.new()
	for side in 2:
		var tool := SurfaceTool.new()
		tool.begin(Mesh.PRIMITIVE_TRIANGLES)
		var x0 := -1.0 + side
		for v in [Vector3(x0, 0, 0), Vector3(x0 + 1, 0, 0), Vector3(x0 + 1, 1, 0),
				Vector3(x0, 0, 0), Vector3(x0 + 1, 1, 0), Vector3(x0, 1, 0)]:
			tool.set_normal(Vector3.BACK)
			tool.add_vertex(v)
		var material := StandardMaterial3D.new()
		material.resource_name = "Left" if side == 0 else "Right"
		material.albedo_color = Color(0.9, 0.2, 0.2) if side == 0 else Color(0.2, 0.4, 0.9)
		material.cull_mode = BaseMaterial3D.CULL_DISABLED
		tool.set_material(material)
		tool.commit(array_mesh)
	mesh = array_mesh

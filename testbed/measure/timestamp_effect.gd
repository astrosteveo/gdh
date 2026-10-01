extends CompositorEffect
## A game's own pass, as gdh live frames sees it: it captures a GPU timestamp where it starts.


func _init() -> void:
	effect_callback_type = EFFECT_CALLBACK_TYPE_POST_TRANSPARENT


func _render_callback(_type: int, _data: RenderData) -> void:
	RenderingServer.get_rendering_device().capture_timestamp("Testbed Effect")

extends Node
## Reports the game's own events to gdh, for `gdh live events` and `gdh live explore`:
##
##   GdhEvents.emit("died", {"at": global_position, "by": attacker.name})
##   GdhEvents.emit("checkpoint", 3)
##
## Under gdh each event is kept with the game frame it happened on. Outside gdh (in the editor, in an exported game)
## emit() does nothing, and costs one lookup. `gdh events install` puts this file in a project and adds it as the
## autoload GdhEvents.


func emit(event: String, data: Variant = null) -> void:
	if Engine.has_meta("gdh_event"):
		(Engine.get_meta("gdh_event") as Callable).call(event, data)

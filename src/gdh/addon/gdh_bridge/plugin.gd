@tool
extends EditorPlugin
## Starts gdh's bridge (server.gd) in this editor, so gdh and Claude Code work through it instead of behind its back.

const Server := preload("server.gd")

var _server: Node


func _enter_tree() -> void:
	_server = Server.new()
	add_child(_server)


func _exit_tree() -> void:
	if _server:
		_server.queue_free()
		_server = null

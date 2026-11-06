extends Node2D
## A scene with a load() method of its own: a bare load(...) in an expression reaches it, as it did before gdh added
## GDScript's load.


func load(path: String) -> String:
	return "own " + path

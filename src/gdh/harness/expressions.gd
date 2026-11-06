extends RefCounted
## GDScript's own functions in eval, --until, --trace and scenario checks. A Godot Expression has Godot's global
## functions (str, abs, clamp...) but not GDScript's, and takes a bare load(...) for a method of its base, the
## current scene. rewrite() turns a bare call of one of CALLS into a call on this object, given to the Expression
## as the input INPUT; explain() names a call that's missing either way, which Godot's own error cuts off.

## Each GDScript function gdh adds, and the method here that stands in for it.
const CALLS := {"load": "gd_load", "preload": "gd_load", "len": "gd_len", "range": "gd_range"}
const INPUT := "__gdh"

static var _cut_off_call := RegEx.create_from_string("^On call to '([^']+)':\\s*$")


func gd_load(path: String) -> Resource:
	return load(path)


func gd_len(value: Variant) -> int:
	return len(value)


func gd_range(a: int, b: Variant = null, step := 1) -> Array:
	return range(a) if b == null else range(a, int(b), step)


## text with each bare call of a name in CALLS (not after a ".", not in a string) made a call on INPUT, unless
## base has a method of that name, which a bare call reaches today.
static func rewrite(text: String, base: Object) -> String:
	var out := ""
	for token in _scan(text):
		var name: String = token.text
		if token.call and CALLS.has(name) and not (base and base.has_method(name)):
			out += "%s.%s" % [INPUT, CALLS[name]]
		else:
			out += name
	return out


## Godot's error for a bare call that neither base nor an Expression has stops after its name; this says what's
## missing. Any other error comes back as it is.
static func explain(error: String, text: String, base: Object) -> String:
	var found := _cut_off_call.search(error)
	if found == null or base == null:
		return error
	var name := found.get_string(1)
	if base.has_method(name) or not _scan(text).any(func(t: Dictionary) -> bool: return t.call and t.text == name):
		return error
	return ("'%s' isn't a method of the scene's root (%s), and an Expression has Godot's global functions (str, abs, "
			+ "clamp...) but not GDScript's own; gdh adds %s.") % [name, base.get_class(), ", ".join(CALLS.keys())]


## text in pieces that join back to it: each {"text", "call"}, where call is true for a name called bare (followed by
## "(" and not after "."). Text in '...' and "..." strings, &"..." and ^"..." included, is never a name.
static func _scan(text: String) -> Array[Dictionary]:
	var tokens: Array[Dictionary] = []
	var n := text.length()
	var i := 0
	while i < n:
		var start := i
		var c := text[i]
		if c == "'" or c == "\"":
			i += 1
			while i < n and text[i] != c:
				i += 2 if text[i] == "\\" else 1
			i = mini(i + 1, n)
			tokens.append({"text": text.substr(start, i - start), "call": false})
			continue
		if not _word(c):
			tokens.append({"text": c, "call": false})
			i += 1
			continue
		while i < n and _word(text[i]):
			i += 1
		var after := i
		while after < n and text[after] in [" ", "\t", "\n"]:
			after += 1
		var before := start - 1
		while before >= 0 and text[before] in [" ", "\t", "\n"]:
			before -= 1
		var bare_call := after < n and text[after] == "(" and (before < 0 or text[before] != ".")
		tokens.append({"text": text.substr(start, i - start), "call": bare_call})
	return tokens


static func _word(c: String) -> bool:
	return c == "_" or c.unicode_at(0) > 127 or (c >= "a" and c <= "z") or (c >= "A" and c <= "Z") \
			or (c >= "0" and c <= "9")
